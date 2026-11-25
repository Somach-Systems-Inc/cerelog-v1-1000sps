# What is in these images

Built with PlatformIO (platform `espressif32`, board `esp32dev`, Arduino core for ESP32 2.0.17)
from `firmware/somach_v1_rate.ino`. Checksums are in `SHA256SUMS`.

| File | Flash offset | What it is | Licence |
|---|---|---|---|
| `bootloader.bin` | 0x1000 | ESP-IDF second-stage bootloader, as shipped with the Arduino core | Apache-2.0 (Espressif) |
| `partitions.bin` | 0x8000 | the core's default 4 MB partition table | Apache-2.0 (Espressif) |
| `boot_app0.bin` | 0xe000 | OTA data selecting the first app slot, from the Arduino core | LGPL-2.1 (arduino-esp32) |
| `firmware.bin` | 0x10000 | the patched sketch linked with the Arduino core and ESP-IDF | MIT (sketch); LGPL-2.1 and Apache-2.0 (core, IDF) |

The sketch source, the build file and the exact core version are in this repository, so the
images can be rebuilt and relinked: see "Build it yourself" in the README.
