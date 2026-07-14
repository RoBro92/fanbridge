#include <Arduino.h>
#include <Adafruit_NeoPixel.h>

#if defined(ARDUINO_ARCH_RP2040)
#include "pico/bootrom.h"  // reset_usb_boot()
#include "hardware/flash.h"
#endif

#if defined(ARDUINO_ARCH_RP2040) && !defined(ARDUINO_ARCH_MBED)
#include "hardware/watchdog.h"
#endif

#if defined(ARDUINO_ARCH_MBED)
#include "mbed.h"
static mbed::PwmOut* fanPwm = nullptr;
#endif

// --------------------------------------------------------------------------------------
// Firmware info
static const char* FW_VERSION = "2.5.2";
static const uint8_t PROTOCOL_VERSION = 2;
static const char* BOARD_ID = "rp2040-zero";
#if defined(ARDUINO_ARCH_RP2040)
static const uint8_t CONTROLLER_UID_BYTES = 8;
static char controllerUid[2 * CONTROLLER_UID_BYTES + 1] = {0};
#endif

// User-adjustable settings
// - GATE_PIN: RP2040 GPIO that drives the 2N3904 base via ~1k resistor
// - PWM_FREQ_HZ: 25 kHz matches Intel 4-wire fan spec
// - PWM_RANGE: 8-bit resolution is sufficient for PC fans
static const int      GATE_PIN      = 15;       // <-- change if you wire a different GPIO
static const uint32_t PWM_FREQ_HZ   = 25000;
static const uint16_t PWM_RANGE     = 255;
static const uint8_t  IDENTIFY_LED_PIN = 16;    // RP2040-Zero onboard WS2812
static const uint32_t IDENTIFY_DURATION_MS = 10000UL;
static const uint16_t IDENTIFY_BLINK_MS = 250;

// Startup and reliability aids
// Fail safe: the controller must never depend on the host being available in
// order to cool the enclosure.  It boots at full speed and only leaves that
// state after receiving a valid numeric 0..100 setpoint.
static const uint8_t  START_PERCENT     = 100;  // power-on setpoint
static const bool     DO_SPINUP         = false;
static const uint16_t SPINUP_MS         = 400;
static const uint8_t  MIN_START_PERCENT = 22;   // minimum to start from stop
static const uint8_t  MIN_RUN_PERCENT   = 18;   // minimum while already spinning
static const uint8_t  KICK_PERCENT      = 85;
static const uint16_t KICK_MS           = 500;
static const uint32_t CONTROL_LEASE_MS  = 60000UL;
static const uint32_t WATCHDOG_MS       = 4000UL;
// --------------------------------------------------------------------------------------

static uint8_t  userPct    = START_PERCENT; // 0..100 requested
static uint16_t currentRaw = 0;             // 0..PWM_RANGE applied (inverted for sink)
static unsigned long lastControlTime = 0;   // Last valid numeric PWM command
static bool hasReceivedControl = false;
static bool failsafeActive = true;
static bool watchdogStarted = false;
static bool identifyActive = false;
static bool identifyLedOn = false;
static unsigned long identifyStartedAt = 0;
static unsigned long identifyLastToggleAt = 0;
// Adafruit_NeoPixel's RP2040 constructor claims a PIO state machine. Construct
// it only after the Arduino/Mbed runtime and USB CDC are up; doing that from a
// global constructor can touch PIO before the platform has initialized.
static Adafruit_NeoPixel* identifyLed = nullptr;

static void loadControllerUid() {
#if defined(ARDUINO_ARCH_RP2040)
  uint8_t raw[CONTROLLER_UID_BYTES] = {0};
  static const char HEX_DIGITS[] = "0123456789abcdef";
  flash_get_unique_id(raw);
  for (uint8_t i = 0; i < CONTROLLER_UID_BYTES; ++i) {
    controllerUid[i * 2] = HEX_DIGITS[(raw[i] >> 4) & 0x0f];
    controllerUid[i * 2 + 1] = HEX_DIGITS[raw[i] & 0x0f];
  }
  controllerUid[2 * CONTROLLER_UID_BYTES] = '\0';
#endif
}

static void printControllerDisplayName() {
  Serial.print(F("DIY-RP2040-"));
  // The four-character suffix is for human recognition only. The host binds
  // settings with the complete 64-bit controllerUid above.
  Serial.print(controllerUid + (2 * CONTROLLER_UID_BYTES - 4));
}

static void setIdentifyLed(bool enabled) {
  identifyLedOn = enabled;
  if (!identifyLed) return;
  identifyLed->setPixelColor(0, enabled ? identifyLed->Color(255, 72, 0) : 0);
  identifyLed->show();
}

static void setupIdentifyLed() {
  if (!identifyLed) {
    identifyLed = new Adafruit_NeoPixel(1, IDENTIFY_LED_PIN, NEO_GRB + NEO_KHZ800);
  }
  identifyLed->begin();
  identifyLed->setBrightness(32);
  identifyLed->clear();
  identifyLed->show();
}

static void startIdentify() {
  identifyActive = true;
  identifyStartedAt = millis();
  identifyLastToggleAt = identifyStartedAt;
  setIdentifyLed(true);
}

static void serviceIdentifyLed() {
  if (!identifyActive) return;
  const unsigned long now = millis();
  if ((unsigned long)(now - identifyStartedAt) >= IDENTIFY_DURATION_MS) {
    identifyActive = false;
    setIdentifyLed(false);
    return;
  }
  if ((unsigned long)(now - identifyLastToggleAt) >= IDENTIFY_BLINK_MS) {
    identifyLastToggleAt = now;
    setIdentifyLed(!identifyLedOn);
  }
}

static void startHardwareWatchdog() {
#if defined(ARDUINO_ARCH_MBED)
  mbed::Watchdog& watchdog = mbed::Watchdog::get_instance();
  if (!watchdog.is_running()) {
    watchdog.start(WATCHDOG_MS);
  }
  watchdogStarted = watchdog.is_running();
#elif defined(ARDUINO_ARCH_RP2040)
  watchdog_enable(WATCHDOG_MS, true);
  watchdogStarted = true;
#endif
}

static void serviceHardwareWatchdog() {
  if (!watchdogStarted) return;
#if defined(ARDUINO_ARCH_MBED)
  mbed::Watchdog::get_instance().kick();
#elif defined(ARDUINO_ARCH_RP2040)
  watchdog_update();
#endif
}

static void safeDelay(uint32_t durationMs) {
  const uint32_t started = millis();
  while ((uint32_t)(millis() - started) < durationMs) {
    serviceHardwareWatchdog();
    const uint32_t elapsed = (uint32_t)(millis() - started);
    const uint32_t remaining = durationMs > elapsed ? durationMs - elapsed : 0;
    delay(remaining > 50 ? 50 : remaining);
  }
}

// 0..100% -> raw 0..PWM_RANGE (inverted: 0%=high, 100%=low for open-collector sink)
static inline uint16_t pctToRaw(uint8_t pct) {
  if (pct > 100) pct = 100;
  return (uint16_t)map(pct, 0, 100, PWM_RANGE, 0);
}

static void writeRaw(uint16_t raw) {
  if (raw > PWM_RANGE) raw = PWM_RANGE;
  currentRaw = raw;
#if defined(ARDUINO_ARCH_MBED)
  if (!fanPwm) return;
  const float duty = (float)raw / (float)PWM_RANGE; // 0..1
  fanPwm->write(duty);
#else
  analogWrite(GATE_PIN, raw);
#endif
}

static void setupPwm() {
#if defined(ARDUINO_ARCH_MBED)
  if (!fanPwm) {
    fanPwm = new mbed::PwmOut(digitalPinToPinName(GATE_PIN));
  }
  fanPwm->period_us(40);   // ~25 kHz
  fanPwm->write(0.0f);
#else
  pinMode(GATE_PIN, OUTPUT);
  #if defined(ARDUINO_ARCH_RP2040) && !defined(ARDUINO_ARCH_MBED)
    analogWriteFreq(PWM_FREQ_HZ);
    analogWriteRange(PWM_RANGE);
  #endif
#endif
}

// Applies minimums and optional kick to ensure reliable starts
static void setFanPercent(uint8_t pct, bool useKick = true) {
  if (pct > 100) pct = 100;
  // Without a working reset watchdog, a stalled loop could otherwise hold a
  // low PWM indefinitely.  Every internal caller therefore fails closed too,
  // even if a future command path forgets its own explicit error response.
  if (!watchdogStarted && pct < 100) pct = 100;
  userPct = pct;

  if (pct == 0) {
    writeRaw(pctToRaw(0));
    return;
  }

  const bool wasOff = (currentRaw >= (PWM_RANGE - 5));  // near "off" (high)
  const uint8_t minAllowed = wasOff ? MIN_START_PERCENT : MIN_RUN_PERCENT;
  const uint8_t adjPct = (pct < minAllowed) ? minAllowed : pct;

  if (useKick && wasOff) {
    writeRaw(pctToRaw(KICK_PERCENT));
    safeDelay(KICK_MS);
  }
  writeRaw(pctToRaw(adjPct));
}

static void printUptime() {
  const unsigned long ms = millis();
  const unsigned long s  = ms / 1000UL;
  const unsigned long m  = s  / 60UL;
  const unsigned long h  = m  / 60UL;
  const unsigned long d  = h  / 24UL;

  Serial.print(F("uptime: "));
  Serial.print(ms); Serial.print(F(" ms ("));
  Serial.print(s);  Serial.print(F(" s, "));
  Serial.print(m);  Serial.print(F(" min, "));
  Serial.print(h);  Serial.print(F(" h, "));
  Serial.print(d);  Serial.println(F(" days)"));
}

static void printStatus() {
  float dutyPct = ((float)PWM_RANGE - (float)currentRaw) * 100.0f / (float)PWM_RANGE;
  if (dutyPct < 0.0f) dutyPct = 0.0f;
  if (dutyPct > 100.0f) dutyPct = 100.0f;

  Serial.print(F("{\"fw\":\"")); Serial.print(FW_VERSION);
  Serial.print(F("\",\"protocol_version\":")); Serial.print(PROTOCOL_VERSION);
  Serial.print(F(",\"board\":\"")); Serial.print(BOARD_ID);
  Serial.print(F("\",\"controller_uid\":\"")); Serial.print(controllerUid);
  Serial.print(F("\",\"channel_count\":1"));
  Serial.print(F(",\"capabilities\":[\"pwm.single\",\"failsafe.lease\",\"identify.led\"]"));
  Serial.print(F(",\"uptime_ms\":")); Serial.print(millis());
  Serial.print(F(",\"setpoint_pct\":")); Serial.print(userPct);
  Serial.print(F(",\"applied_pwm_raw\":")); Serial.print(currentRaw);
  Serial.print(F(",\"applied_pwm_pct\":")); Serial.print(dutyPct, 1);
  Serial.print(F(",\"pwm_freq_hz\":")); Serial.print(PWM_FREQ_HZ);
  Serial.print(F(",\"pwm_range\":")); Serial.print(PWM_RANGE);
  Serial.print(F(",\"gate_pin\":")); Serial.print(GATE_PIN);
  // A Pico development board has a VSYS divider on ADC29; the future custom
  // PCB does not yet have a frozen divider/interface. Never fabricate that
  // telemetry as if it were portable hardware capability.
  Serial.print(F(",\"vsys_v\":null"));
  Serial.print(F(",\"failsafe_active\":")); Serial.print(failsafeActive ? F("true") : F("false"));
  Serial.print(F(",\"control_lease_ms\":")); Serial.print(CONTROL_LEASE_MS);
  Serial.print(F(",\"watchdog_active\":")); Serial.print(watchdogStarted ? F("true") : F("false"));
  Serial.print(F(",\"watchdog_ms\":"));
  if (watchdogStarted) Serial.print(WATCHDOG_MS); else Serial.print(F("null"));
  Serial.print(F(",\"identify_active\":")); Serial.print(identifyActive ? F("true") : F("false"));
  Serial.print(F(",\"control_age_ms\":"));
  if (hasReceivedControl) {
    Serial.print((unsigned long)(millis() - lastControlTime));
  } else {
    Serial.print(F("null"));
  }
  Serial.println(F("}"));
}

static void rebootToBootsel() {
#if defined(ARDUINO_ARCH_RP2040)
  identifyActive = false;
  setIdentifyLed(false);
  setFanPercent(100, false);
  Serial.println(F("Rebooting to BOOTSEL..."));
  Serial.flush();
  safeDelay(50);
  reset_usb_boot(0, 0); // no return
#else
  Serial.println(F("ERR: BOOTSEL not supported on this platform"));
#endif
}

static void handleLine(String line) {
  line.trim();
  if (!line.length()) return;

  String cmd = line; cmd.toUpperCase();

  if (cmd == "ID" || cmd == "ID?") {
    Serial.print(F("FANBRIDGE_DIY protocol=")); Serial.print(PROTOCOL_VERSION);
    Serial.print(F(" board=")); Serial.print(BOARD_ID);
    Serial.print(F(" channels=1 uid=")); Serial.println(controllerUid);
    return;
  }
  if (cmd == "VERSION" || cmd == "VERSION?") { Serial.println(FW_VERSION); return; }
  if (cmd == "PING")                          { Serial.println(F("PONG")); return; }
  if (cmd == "RPM" || cmd == "RPM?")          { Serial.println(F("ERR: RPM unsupported")); return; }
  if (cmd == "UPTIME" || cmd == "UPTIME?")    { printUptime();             return; }
  if (cmd == "STATUS" || cmd == "STATUS?")    { printStatus();             return; }
  if (cmd == "IDENTIFY" || cmd == "IDENTIFY?") {
    startIdentify();
    Serial.print(F("IDENTIFYING duration_ms=")); Serial.println(IDENTIFY_DURATION_MS);
    return;
  }
  if (cmd == "TEST") {
    if (!watchdogStarted) {
      setFanPercent(100, false);
      Serial.println(F("ERR: watchdog unavailable; fan held at 100%"));
      return;
    }
    const uint8_t prev = userPct;
    Serial.println(F("TEST: 0% 2s, 100% 5s, 0% 2s, restore"));
    setFanPercent(0,   false); safeDelay(2000);
    setFanPercent(100, true ); safeDelay(5000);
    setFanPercent(0,   false); safeDelay(2000);
    setFanPercent(prev, false);
    Serial.print(F("Restored to ")); Serial.print(prev); Serial.println('%');
    return;
  }
  if (cmd == "BOOTSEL" || cmd == "UPDATE" || cmd == "FW UPDATE") {
    rebootToBootsel();
    return;
  }

  // Numeric percent 0..100
  bool numeric = true;
  bool outOfRange = false;
  uint16_t value = 0;
  for (uint16_t i = 0; i < line.length(); ++i) {
    if (!isDigit(line[i])) {
      numeric = false;
      break;
    }
    if (!outOfRange) {
      value = (uint16_t)(value * 10U + (uint16_t)(line[i] - '0'));
      outOfRange = value > 100U;
    }
  }
  if (numeric) {
    if (outOfRange) {
      Serial.println(F("ERR: PWM percent must be 0..100"));
      return;
    }
    if (!watchdogStarted && value < 100U) {
      setFanPercent(100, false);
      Serial.println(F("ERR: watchdog unavailable; fan held at 100%"));
      return;
    }
    setFanPercent((uint8_t)value);
    lastControlTime = millis();
    hasReceivedControl = true;
    failsafeActive = false;
    Serial.print(F("Set fan to ")); Serial.print(userPct); Serial.println('%');
    return;
  }

  Serial.println(F("Unknown. Use: VERSION, PING, UPTIME, STATUS, IDENTIFY, TEST, BOOTSEL, 0..100"));
}

void setup() {
  // Assert the safe output before waiting for USB. The external 10k base-to-
  // emitter resistor is still mandatory so reset/ROM BOOTSEL also releases
  // the fan PWM input when application firmware is not executing.
  setupPwm();
  setFanPercent(START_PERCENT, false);

  // Bring up USB before starting the hardware watchdog. Arduino Mbed's CDC
  // initialisation may wait for descriptor negotiation; starting a four-second
  // watchdog first can reset the RP2040 in the middle of enumeration and leave
  // the host reporting repeated descriptor timeouts.
  Serial.begin(115200);
  const uint32_t t0 = millis();
  while (!Serial && (millis() - t0 < 2000)) {
    delay(10);
  }

  // The Pico SDK derives this persistent board identity from the unique ID of
  // the flash device permanently paired with this RP2040 board. Keep all
  // non-essential peripherals out of the USB-critical startup path.
  loadControllerUid();
  setupIdentifyLed();
  startHardwareWatchdog();
  if (DO_SPINUP && START_PERCENT > 0) {
    setFanPercent(100, true);
    safeDelay(SPINUP_MS);
  }
  setFanPercent(START_PERCENT, false);

  printControllerDisplayName(); Serial.print(' '); Serial.print(FW_VERSION);
  Serial.println(F(" ready @115200 (PWM-only)"));
  Serial.println(F("Commands: VERSION, PING, RPM, UPTIME, STATUS, IDENTIFY, TEST, BOOTSEL, 0..100"));

  // No lease exists at startup.  START_PERCENT is already the safe fallback;
  // STATUS/PING/diagnostic traffic intentionally does not create a lease.
  hasReceivedControl = false;
  failsafeActive = true;
}

void loop() {
  serviceHardwareWatchdog();
  serviceIdentifyLed();
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    handleLine(line);
  }

  // Control lease: only a valid numeric PWM command renews it.  Read-only
  // telemetry must never keep a stale/unsafe setpoint alive.
  if (hasReceivedControl && !failsafeActive &&
      (unsigned long)(millis() - lastControlTime) > CONTROL_LEASE_MS) {
    failsafeActive = true;
    setFanPercent(100, false);
    Serial.println(F("ERR: PWM control lease expired. Failsafe activated (100%)."));
  }
}
