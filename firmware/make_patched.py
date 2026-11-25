#!/usr/bin/env python3
"""Build somach_v1_rate.ino from the vendor's V1 firmware by exact text replacement.

    python3 make_patched.py <upstream V1_ESP_EEG_firmware.ino> <out.ino>

Every replacement must match exactly once, so a changed upstream file fails loudly here
instead of producing a half-patched firmware. Upstream: github.com/Cerelog-ESP-EEG/ESP-EEG,
firmware/V1_ESP_EEG_firmware.ino, commit c64d263 (read 2026-11-25).

What the patch adds (2026-11-25, Somach):
  * Handshake register 0x03 = rate + baud in one byte: high nibble 0/1/2/4 (0 = back to the
    stock clock at 250 SPS; 1/2/4 = 250/500/1000 SPS on the sample-counter clock), low nibble
    the stock baud code. Checksum and end marker are verified for this register.
  * Sample-counter clock: after a 0x03 handshake the packet's 4-byte time field is
    (DRDY edges since the handshake) x (ms per sample). A lost sample is then an exact gap,
    which the ESP32's millisecond clock cannot show at 1 ms per sample.
  * A frame whose SPI read straddled the next DRDY edge is not sent (it may be torn).
  * The handshake ring buffer is consumed after use. Stock never empties it: the second
    handshake re-applies the first one's bytes and a third stalls the stream until reset.
Nothing changes for a host that sends only the stock 0x01 handshake once per boot.
"""
import sys

REPL = [
# 1. globals
("""volatile bool dataReady = false;
""",
"""volatile bool dataReady = false;
// --- Somach patch: selectable rate + sample-counter clock ---
volatile uint32_t drdyCount = 0;      // DRDY falling edges since the last handshake
uint32_t _last_sent_count = 0;        // counter value of the last packet sent
bool _extended_clock = false;         // false = stock millis() timestamps
uint8_t _ms_per_sample = 4;           // 4, 2, 1 for 250, 500, 1000 SPS
bool set_sample_rate(uint8_t mult);
"""),
# 2. ISR
("""void IRAM_ATTR onDRDYFalling(void) { dataReady = true; }""",
 """void IRAM_ATTR onDRDYFalling(void) { drdyCount++; dataReady = true; }

// Somach patch. mult 1/2/4 -> 250/500/1000 SPS (CONFIG1 DR bits 110/101/100, upper bits as
// in the stock table). Mirrors the stock start-up order: SDATAC, WREG, START, settle, RDATAC.
// The register is read back; on a mismatch the stock value is restored and false returned.
bool set_sample_rate(uint8_t mult) {
  uint8_t dr;
  if (mult == 1) dr = 0b110; else if (mult == 2) dr = 0b101; else if (mult == 4) dr = 0b100; else return false;
  uint8_t cfg1 = 0b10110000 | dr;
  detachInterrupt(digitalPinToInterrupt(pin_DRDY_NUM));
  ADS1299_WREG(0x01, &cfg1, 1);
  delayMicroseconds(10);
  uint8_t back = 0;
  ADS1299_RREG(0x01, &back, 1);
  bool ok = (back == cfg1);
  if (!ok) { uint8_t stock = 0b10110110; ADS1299_WREG(0x01, &stock, 1); delayMicroseconds(10); mult = 1; }
  delay(10);
  ADS1299_START();
  delay(20);
  ADS1299_RDATAC();
  _ms_per_sample = 4 / mult;
  drdyCount = 0; _last_sent_count = 0; dataReady = false;
  attachInterrupt(digitalPinToInterrupt(pin_DRDY_NUM), onDRDYFalling, FALLING);
  return ok;
}"""),
# 3. handshake reader: consume the ring, add register 0x03
("""  while (Serial.available() > 0 && ring_counter < RING_BUFFER_SIZE) {
      ring_buffer[ring_head] = Serial.read(); ring_head = (ring_head + 1) % RING_BUFFER_SIZE; ring_counter++;
  }
  if (ring_counter < handshake_packet_size) return false;
  for (uint8_t i = 0; i < handshake_packet_size + 3; i++) {
      uint8_t ring_index = (ring_tail + i) % RING_BUFFER_SIZE;
      if (ring_buffer[ring_index] == HANDSHAKE_START_MARKER_1 && ring_buffer[(ring_index + 1) % RING_BUFFER_SIZE] == HANDSHAKE_START_MARKER_2 && ring_buffer[(ring_index + 2) % RING_BUFFER_SIZE] == MSG_TYPE_TIMESTAMP) {
""",
"""  // Somach patch: bytes are consumed as they are scanned, so each handshake is applied once
  // and a later one is read on its own bytes (stock re-applied the first forever).
  for (;;) {
    while (Serial.available() > 0 && ring_counter < RING_BUFFER_SIZE) {
        ring_buffer[ring_head] = Serial.read(); ring_head = (ring_head + 1) % RING_BUFFER_SIZE; ring_counter++;
    }
    while (ring_counter >= handshake_packet_size) {
      uint8_t ring_index = ring_tail;
      if (ring_buffer[ring_index] == HANDSHAKE_START_MARKER_1 && ring_buffer[(ring_index + 1) % RING_BUFFER_SIZE] == HANDSHAKE_START_MARKER_2 && ring_buffer[(ring_index + 2) % RING_BUFFER_SIZE] == MSG_TYPE_TIMESTAMP) {
          uint8_t hs_sum = 0;
          for (uint8_t k = 2; k < 9; k++) hs_sum += ring_buffer[(ring_index + k) % RING_BUFFER_SIZE];
          bool hs_intact = hs_sum == ring_buffer[(ring_index + 9) % RING_BUFFER_SIZE]
              && ring_buffer[(ring_index + 10) % RING_BUFFER_SIZE] == HANDSHAKE_END_MARKER_1
              && ring_buffer[(ring_index + 11) % RING_BUFFER_SIZE] == HANDSHAKE_END_MARKER_2;
"""),
("""          if (reg_addr == 0x01) {
              uint32_t new_baud_rate = get_baud_rate_from_config(reg_val);
              if (new_baud_rate > 0) {
                  while (Serial.available() > 0) { Serial.read(); }
                  Serial.flush(); delay(100); Serial.begin(new_baud_rate); delay(100);
              }
          }
          _unix_timestamp_reference = received_timestamp; _millis_reference = millis(); _timestamp_initialized = true;
          delay(100); return true;
      }
  }
  return false;
}""",
"""          ring_tail = (ring_tail + handshake_packet_size) % RING_BUFFER_SIZE; ring_counter -= handshake_packet_size;
          if (reg_addr == 0x01) {
              uint32_t new_baud_rate = get_baud_rate_from_config(reg_val);
              if (new_baud_rate > 0) {
                  while (Serial.available() > 0) { Serial.read(); }
                  Serial.flush(); delay(100); Serial.begin(new_baud_rate); delay(100);
              }
          } else if (reg_addr == 0x03) {
              // rate (high nibble: 0 stock clock, 1/2/4 = 250/500/1000 SPS) + baud code (low nibble)
              uint8_t mult = reg_val >> 4;
              uint32_t new_baud_rate = get_baud_rate_from_config(reg_val & 0x0F);
              if (hs_intact && new_baud_rate > 0 && (mult == 0 || mult == 1 || mult == 2 || mult == 4)) {
                  bool rate_ok = set_sample_rate(mult == 0 ? 1 : mult);
                  _extended_clock = rate_ok && mult != 0;
                  while (Serial.available() > 0) { Serial.read(); }
                  Serial.flush(); delay(100); Serial.begin(new_baud_rate); delay(100);
              }
          }
          _unix_timestamp_reference = received_timestamp; _millis_reference = millis(); _timestamp_initialized = true;
          delay(100);
          // the sample-counter clock starts at the handshake, like the millisecond clock does
          noInterrupts(); drdyCount = 0; dataReady = false; interrupts(); _last_sent_count = 0;
          return true;
      }
      ring_tail = (ring_tail + 1) % RING_BUFFER_SIZE; ring_counter--;   // not a handshake start: slide one byte
    }
    if (Serial.available() == 0) return false;
  }
}"""),
# 4. loop: look for a handshake whenever bytes are waiting (stock waited for 12)
("""  if (Serial.available() >= 12) {
      if (waitForTimestamp()) {""",
 """  if (Serial.available() > 0) {
      if (waitForTimestamp()) {"""),
# 5. loop: counter identity, torn-frame guard
("""          dataReady = false;
     
          byte raw_data[ADS1299_TOTAL_DATA_BYTES];
          read_ADS1299_data(raw_data);
""",
"""          dataReady = false;
          uint32_t sample_count = drdyCount;                              // Somach patch
          if (_extended_clock && sample_count == _last_sent_count) return; // nothing new since the last packet
     
          byte raw_data[ADS1299_TOTAL_DATA_BYTES];
          read_ADS1299_data(raw_data);
          // the next conversion landed during the read: the frame may be torn, so it is not
          // sent and the host sees an exact one-sample gap instead of a wrong value
          if (_extended_clock && drdyCount != sample_count) return;
"""),
("""         uint32_t millis_since_sync = millis() - _millis_reference;
""",
 """         uint32_t millis_since_sync = _extended_clock ? (sample_count * (uint32_t)_ms_per_sample)
                                                      : (millis() - _millis_reference);
         if (_extended_clock) _last_sent_count = sample_count;
"""),
]

def main() -> int:
    src_path, out_path = sys.argv[1], sys.argv[2]
    text = open(src_path, encoding="utf-8").read().replace("\r\n", "\n")
    for i, (old, new) in enumerate(REPL, 1):
        n = text.count(old)
        if n != 1:
            print(f"replacement {i}: expected exactly 1 match, found {n}. Upstream changed; "
                  "re-read it before patching.", file=sys.stderr)
            return 1
        text = text.replace(old, new)
    open(out_path, "w", encoding="utf-8").write(text)
    print(f"wrote {out_path} ({len(REPL)} replacements)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
