#!/bin/bash
# Cerelog ESP-EEG V1 (ESP32-WROOM + CH340): back up, flash the patched firmware, or restore.
#
#   ./flash.sh backup  <port> <out.bin>     read the whole 4 MB flash to a file (about 6 minutes)
#   ./flash.sh patched <port>               write bin/ (refuses unless a backup exists in ./backups)
#   ./flash.sh restore <port> <backup.bin>  write a backup image back, byte for byte
#
# Needs esptool (pip install esptool). 115200 baud on purpose: with the CH340 driver built
# into macOS, esptool lost the board at 921600 and lost bytes at 460800 while reading.
# Override with BAUD=... if your setup is known good. esptool checks a hash of what it wrote.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ESPTOOL="${ESPTOOL:-$(command -v esptool || command -v esptool.py || true)}"
BACKUPS="${BACKUPS:-$HERE/backups}"
BAUD="${BAUD:-115200}"
[ -n "$ESPTOOL" ] && [ -x "$ESPTOOL" ] || { echo "flash.sh: esptool not found (pip install esptool, or set ESPTOOL=/path/to/esptool)" >&2; exit 2; }
cmd="${1:-}"; port="${2:-}"
[ -n "$cmd" ] && [ -n "$port" ] || { sed -n 2,7p "$0" >&2; exit 2; }
[ -e "$port" ] || case "$port" in COM*) ;; *) echo "flash.sh: no such port $port" >&2; exit 2 ;; esac
size() { wc -c < "$1" | tr -d ' '; }

case "$cmd" in
  backup)
    out="${3:?backup needs an output file}"
    [ ! -e "$out" ] || { echo "flash.sh: $out exists; backups are never overwritten" >&2; exit 2; }
    mkdir -p "$(dirname "$out")"
    "$ESPTOOL" --chip esp32 --port "$port" --baud "$BAUD" read-flash 0 0x400000 "$out"
    [ "$(size "$out")" = 4194304 ] || { echo "flash.sh: backup is not 4 MB" >&2; exit 1; }
    shasum -a 256 "$out" | tee "$out.sha256"
    ;;
  patched)
    n=$(find "$BACKUPS" -name '*.bin' -size +4095k 2>/dev/null | wc -l | tr -d ' ')
    [ "$n" -ge 1 ] || { echo "flash.sh: no 4 MB backup in $BACKUPS. Run: ./flash.sh backup $port $BACKUPS/stock.bin" >&2; exit 2; }
    (cd "$HERE/bin" && shasum -a 256 -c SHA256SUMS >/dev/null) || { echo "flash.sh: bin/ does not match SHA256SUMS" >&2; exit 1; }
    "$ESPTOOL" --chip esp32 --port "$port" --baud "$BAUD" write-flash \
      --flash-mode dio --flash-freq 40m --flash-size 4MB \
      0x1000 "$HERE/bin/bootloader.bin" 0x8000 "$HERE/bin/partitions.bin" \
      0xe000 "$HERE/bin/boot_app0.bin" 0x10000 "$HERE/bin/firmware.bin"
    ;;
  restore)
    img="${3:?restore needs the backup image}"
    [ "$(size "$img")" = 4194304 ] || { echo "flash.sh: $img is not a 4 MB image" >&2; exit 2; }
    "$ESPTOOL" --chip esp32 --port "$port" --baud "$BAUD" write-flash \
      --flash-mode keep --flash-freq keep --flash-size keep 0x0 "$img"
    ;;
  *) sed -n 2,7p "$0" >&2; exit 2 ;;
esac
echo "flash.sh: $cmd done on $port"
