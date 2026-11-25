# Cerelog ESP-EEG V1 at 500 and 1000 samples a second

A small patch to the firmware of the [Cerelog ESP-EEG](https://github.com/Cerelog-ESP-EEG/ESP-EEG)
V1 board (an ESP32 with an ADS1299, the 8-channel amplifier chip also used in the OpenBCI Cyton).
The stock firmware is fixed at 250 samples a second. With this patch the computer can ask for
250, 500 or 1000 when it connects.

Why bother: muscle signals on the skin (surface EMG) carry most of their energy between 20 and
450 Hz. At 250 samples a second the chip's own filter is already 3 dB down at 65 Hz, so the board
sees only the bottom of that range. At 1000 the same point moves to 262 Hz. The public
silent-speech recordings from Gaddy and Klein (2020) were made at 1000, 8 channels.

This is an unofficial patch from Somach Systems. It is not made or endorsed by Cerelog Inc.
It has been run on one board and one computer. Read "What has not been tested" before you
rely on it.

## What changes

The board still boots exactly like stock: 9600 baud, 250 samples a second, millisecond time
stamps. A program that sends the stock handshake gets the stock stream. Three things are new.

1. **A rate request.** The stock handshake is 12 bytes: `AA BB 02`, a 4-byte Unix time, a
   register byte, a value byte, a checksum, `CC DD`. Register `0x01` sets the serial speed.
   This patch adds register `0x03`, which sets rate and speed together:

   | value byte | meaning |
   |---|---|
   | high nibble `1`, `2`, `4` | 250, 500, 1000 samples a second |
   | high nibble `0` | back to 250 with the stock millisecond clock |
   | low nibble `0` to `7` | the stock baud codes: 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600 |

   So `0x46` means 1000 samples a second at 460800 baud. The firmware checks the handshake's
   checksum and end marker, writes the chip's CONFIG1 register, reads it back, and only then
   switches. If the read-back is wrong it restores 250.

2. **A sample-counter clock.** After a rate request, the 4-byte time field in every packet stops
   being the ESP32's millisecond clock. It becomes the number of samples since the handshake,
   times the milliseconds per sample (4, 2 or 1), counted from the chip's own data-ready pulses.
   A lost sample is then an exact gap in that field. A millisecond clock cannot show a lost
   sample when a sample is only 1 ms long. If the chip's next sample lands while the previous one
   is still being read, that frame is not sent, so it shows as a one-sample gap and never as a
   wrong value.

3. **One fix.** From reading the stock code, its handshake buffer is never emptied: a second
   handshake re-applies the first one's bytes, and a third stalls the stream until the board is
   reset. The patch uses each handshake once.

Gain (24), the reference wiring (every channel against SRB1) and the bias settings are not
touched. The chip register table is the vendor's.

## Measured

One V1 board on a MacBook (macOS, the CH340 driver that ships with the system), on battery,
2026-11-25.

| Run | Packets | Rate against the computer's clock | Lost | Checksum failures |
|---|---|---|---|---|
| Stock firmware, 250, 15 s (before the flash) | 3771 | 249.8 | 0 | 0 |
| Patched, stock handshake, 250, 15 s | 3768 | 249.9 | 0 | 0 |
| Patched, 500 at 230400 baud, 12 s | 6036 | 499.8 | 0 | 0 |
| Patched, 1000 at 460800 baud, 20 s | 20064 | 999.6 | 0 | 0 |
| Patched, 1000 at 460800 baud, 180 s | 180016 | 999.6 | 0 | 0 |

The chip's own oscillator sets the true rate. On this board it runs about 0.04% slow, at every
setting.

## Flash it

You need Python, `pip install esptool`, and a USB data cable.

    ./flash.sh backup  /dev/cu.usbserial-XXX backups/stock.bin    # about 6 minutes; do not skip
    ./flash.sh patched /dev/cu.usbserial-XXX

`flash.sh patched` refuses to run until a 4 MB backup exists in `backups/`. To go back to what
was on your board:

    ./flash.sh restore /dev/cu.usbserial-XXX backups/stock.bin

The script uses 115200 baud on purpose. With the macOS CH340 driver, esptool lost the board at
921600 and lost bytes at 460800 while reading. `BAUD=460800 ./flash.sh ...` overrides it.

## Ask for a rate

    pip install pyserial
    python host/cerelog_1k.py /dev/cu.usbserial-XXX --fs 1000 --seconds 10
    python host/cerelog_1k.py /dev/cu.usbserial-XXX --fs 500 --seconds 30 --csv out.csv

`host/cerelog_1k.py` is one file. It finds the stream, sends the request, checks that the board
really changed (stock firmware ignores the request, and no firmware can report its own rate, so
the script measures it), then counts packets, lost samples and checksum failures. Use it as the
reference for your own reader.

A packet is 37 bytes, 370 bits on the wire with start and stop bits. 1000 a second is
370 kbit/s, so 460800 baud runs 80% full. That is the same load the stock firmware runs at
(250 a second on 115200). 921600 would halve it and is accepted with `--baud 921600`, but it was
not reliable with the macOS driver.

## Build it yourself

`firmware/make_patched.py` rebuilds `somach_v1_rate.ino` from the vendor's file by exact text
replacement, and fails loudly if the vendor's file has changed:

    python firmware/make_patched.py path/to/V1_ESP_EEG_firmware.ino out.ino

It was made against vendor commit `c64d263`. `firmware/somach_v1_rate.patch` is the same change
as a diff. To compile: put the `.ino` in `src/main.ino` beside `firmware/platformio.ini` and run
`pio run` (PlatformIO, Arduino core for ESP32 2.0.17). `bin/NOTICE.md` lists what each image is.

## What has not been tested

- Any second board, any V2 board (the V2 firmware is different and is not patched here).
- BrainFlow or the vendor's own software, before or after the patch. The stock route was
  checked with a port of the vendor's connect sequence, not with BrainFlow itself. A program
  that never sends register `0x03` should see no difference.
- The noise floor at 500 and 1000. Expect it to rise with the wider band (about double at 1000).
- Windows and Linux. The serial speeds may behave better or worse with other CH340 drivers.
- Hours-long runs. The longest was three minutes. Watch the lost-sample count.
- The vendor built their image with Arduino core 3.1.1; this one uses 2.0.17. The chip settings
  are byte for byte the same; the serial and timing code underneath is not.

## Licence

MIT. The firmware is Cerelog Inc.'s (MIT, their repository's `/firmware`); the patch, the host
example and the scripts are Somach Systems, Inc.'s. See `LICENSE` and `bin/NOTICE.md`.
