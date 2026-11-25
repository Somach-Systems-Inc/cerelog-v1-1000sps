#!/usr/bin/env python3
"""Ask a Cerelog ESP-EEG V1 running the patched firmware for 250, 500 or 1000 samples a
second, read for a few seconds, and report exactly what arrived.

    pip install pyserial
    python cerelog_1k.py /dev/cu.usbserial-210 --fs 1000 --seconds 10
    python cerelog_1k.py COM5 --fs 500 --seconds 30 --csv out.csv

Exit code 0 when the stream ran at the requested rate and under 1% of samples were lost;
1 otherwise. MIT licence, Somach Systems, Inc. 2026.
"""
import argparse
import struct
import sys
import time

import serial

PACKET_LEN = 37                      # AB CD | 31 | time u32 BE | 3 status | 8 x int24 BE | sum | DC BA
START, END = b"\xab\xcd", b"\xdc\xba"
BAUD_CODES = {9600: 0, 19200: 1, 38400: 2, 57600: 3, 115200: 4, 230400: 5, 460800: 6, 921600: 7}
RATE_CODE = {250: 1, 500: 2, 1000: 4}
DEFAULT_BAUD = {250: 115200, 500: 230400, 1000: 460800}   # each runs the line 80% full
LSB_UV = (2 * 4.5 / 24) / 2 ** 24 * 1e6                   # microvolts per count at gain 24


def handshake(fs: int, baud: int) -> bytes:
    """Register 0x03: high nibble = rate code, low nibble = baud code."""
    body = bytes([0x02]) + struct.pack(">I", int(time.time())) + bytes([0x03, (RATE_CODE[fs] << 4) | BAUD_CODES[baud]])
    return b"\xaa\xbb" + body + bytes([sum(body) & 0xFF]) + b"\xcc\xdd"


class Parser:
    """Feed it serial chunks in any split. A packet counts only if the length byte, the
    checksum and the end marker are all right."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.checksum_failures = 0

    def feed(self, chunk: bytes):
        self.buf += chunk
        b, out, pos = self.buf, [], 0
        while True:
            i = b.find(START, pos)
            if i < 0:
                pos = max(pos, len(b) - 1)       # keep one byte: it may be the AB of the next packet
                break
            if len(b) - i < PACKET_LEN:
                pos = i
                break
            p = b[i:i + PACKET_LEN]
            if p[2] != 31 or p[35:37] != END:
                pos = i + 1
                continue
            if sum(p[2:34]) & 0xFF != p[34]:
                self.checksum_failures += 1
                pos = i + 1
                continue
            counts = [int.from_bytes(p[10 + 3 * k:13 + 3 * k], "big", signed=True) for k in range(8)]
            out.append((struct.unpack(">I", bytes(p[3:7]))[0], counts))
            pos = i + PACKET_LEN
        del b[:pos]
        return out


def read_times(ser, seconds: float, stop_after: int = 0):
    """(host time, board time field) for every valid packet read in `seconds`."""
    p, out, end = Parser(), [], time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(4096)
        if chunk:
            now = time.monotonic()
            out += [(now, t) for t, _ in p.feed(chunk)]
            if stop_after and len(out) >= stop_after:
                break
    return out


def connect(port: str, fs: int, baud: int):
    """The board boots at 9600 baud and 250 samples a second, but an earlier run may have
    left it somewhere else, and opening the port can reset it (about 4 s to stream again).
    So each speed is tried for whole valid packets before the request is sent; 9600 is
    tried first and again last; one reset pulse is the last resort."""
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 9600, 0.05
    ser.dtr = False                 # a DTR edge resets the ESP32 through the CH340
    ser.rts = False
    ser.open()
    time.sleep(0.5)
    probes = [9600, baud] + [b for b in (115200, 230400, 460800, 921600) if b != baud] + [9600]
    for attempt in (1, 2):
        for b in probes:
            ser.baudrate = b
            ser.reset_input_buffer()
            if len(read_times(ser, 1.5 if b == 9600 else 0.6, 3)) < 3:
                continue
            print(f"  stream found at {b} baud; asking for {fs} samples a second at {baud} baud")
            ser.reset_input_buffer()
            ser.write(handshake(fs, baud))
            ser.flush()
            time.sleep(2.0 if b == 9600 else 1.0)
            ser.baudrate = baud
            ser.reset_input_buffer()
            if len(read_times(ser, 2.0, 3)) >= 3:
                return ser
            print(f"  no valid packets at {baud} baud after the request")
            break
        if attempt == 1:
            print("  pulsing reset and waiting 5 s for boot")
            ser.baudrate = 9600
            ser.dtr = True
            time.sleep(0.2)
            ser.dtr = False
            time.sleep(5.0)
            probes = [9600]
    ser.close()
    raise SystemExit(f"No {fs} samples-a-second stream from {port} at {baud} baud. If the vendor's own tools "
                     "still work, the board is on stock firmware, which ignores this request: flash bin/ first.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("port")
    ap.add_argument("--fs", type=int, default=1000, choices=sorted(RATE_CODE))
    ap.add_argument("--baud", type=int, default=None, choices=sorted(BAUD_CODES))
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--csv", default=None, help="write sample index and 8 channels in microvolts")
    a = ap.parse_args()
    baud = a.baud or DEFAULT_BAUD[a.fs]
    load = a.fs * PACKET_LEN * 10 / baud
    if load > 0.85:
        raise SystemExit(f"{a.fs} samples a second needs {a.fs * PACKET_LEN * 10 // 1000} kbit/s; {baud} baud cannot carry it")
    step = 1000 // a.fs                        # the time field advances by this every sample
    try:
        ser = connect(a.port, a.fs, baud)
    except serial.SerialException as exc:
        raise SystemExit(f"Could not open {a.port}: {exc}")

    # No firmware can report its own rate, so measure it: packets per second on this
    # computer's clock, and the step of the board's time field.
    ser.reset_input_buffer()
    got = read_times(ser, 1.5)
    got = got[len(got) // 5:]
    if len(got) < 20:
        ser.close()
        raise SystemExit(f"only {len(got)} packets in 1.5 s while checking the rate")
    rate = (len(got) - 1) / (got[-1][0] - got[0][0])
    steps = sorted(b[1] - a_[1] for a_, b in zip(got, got[1:]))
    med = steps[len(steps) // 2]
    if not 0.85 * a.fs <= rate <= 1.15 * a.fs or med != step:
        ser.close()
        raise SystemExit(f"Asked for {a.fs}; the board sends {rate:.0f} packets a second with a {med} ms clock step "
                         f"(wanted {step}). Stock firmware ignores the rate request.")
    print(f"  rate check passed: {rate:.0f} packets a second, clock step {med} ms")

    parser, n, lost, backwards, prev = Parser(), 0, 0, 0, None
    out = open(a.csv, "w") if a.csv else None
    if out:
        out.write("sample," + ",".join(f"ch{k}_uv" for k in range(1, 9)) + "\n")
    ser.reset_input_buffer()
    t0 = time.monotonic()
    first_host = last_host = None
    try:
        while time.monotonic() - t0 < a.seconds:
            chunk = ser.read(4096)
            if not chunk:
                continue
            now = time.monotonic()
            for t, counts in parser.feed(chunk):
                if prev is not None:
                    d = t - prev
                    if d < 0:
                        backwards += 1
                    elif d != step:
                        lost += d // step - 1    # the time field counts samples, so a gap is exact
                prev = t
                n += 1
                if out:
                    out.write(f"{t // step}," + ",".join(f"{c * LSB_UV:.3f}" for c in counts) + "\n")
            if n:
                first_host = first_host or now
                last_host = now
    finally:
        ser.close()
        if out:
            out.close()
    if n < 2 or last_host == first_host:
        print("no data")
        return 1
    print(f"\n{n} packets in {a.seconds:g} s at {baud} baud ({load * 100:.0f}% line load)")
    print(f"  lost samples       {lost}  ({100 * lost / (n + lost):.3f}%)")
    print(f"  checksum failures  {parser.checksum_failures}")
    print(f"  clock went back    {backwards}")
    print(f"  rate on this computer's clock: about {n / (last_host - first_host):.1f} a second "
          "(includes USB chunking; the chip's own oscillator sets the true rate)")
    return 0 if lost <= 0.01 * (n + lost) and backwards == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
