#include <Arduino.h>

#if defined(ARDUINO_ARCH_RP2040)
#include "pico/bootrom.h"  // reset_usb_boot()
#endif

#if defined(ARDUINO_ARCH_MBED)
#include "mbed.h"
static mbed::PwmOut* fanPwm = nullptr;
#endif

// --------------------------------------------------------------------------------------
// Firmware info
static const char* FW_VERSION = "2.1.0";

// User-adjustable settings
// - GATE_PIN: RP2040 GPIO that drives the 2N3904 base via ~1k resistor
// - PWM_FREQ_HZ: 25 kHz matches Intel 4-wire fan spec
// - PWM_RANGE: 8-bit resolution is sufficient for PC fans
static const int      GATE_PIN      = 15;       // <-- change if you wire a different GPIO
static const uint32_t PWM_FREQ_HZ   = 25000;
static const uint16_t PWM_RANGE     = 255;

// Startup and reliability aids
static const uint8_t  START_PERCENT     = 0;    // power-on setpoint
static const bool     DO_SPINUP         = false;
static const uint16_t SPINUP_MS         = 400;
static const uint8_t  MIN_START_PERCENT = 22;   // minimum to start from stop
static const uint8_t  MIN_RUN_PERCENT   = 18;   // minimum while already spinning
static const uint8_t  KICK_PERCENT      = 85;
static const uint16_t KICK_MS           = 500;
// --------------------------------------------------------------------------------------

static uint8_t  userPct    = START_PERCENT; // 0..100 requested
static uint16_t currentRaw = 0;             // 0..PWM_RANGE applied (inverted for sink)
static unsigned long lastCmdTime = 0;       // Watchdog timer
static bool fallbackTriggered = false;      // Watchdog flag

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
    delay(KICK_MS);
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
  Serial.print(F("fw: ")); Serial.println(FW_VERSION);
  printUptime();
  Serial.print(F("setpoint_percent: ")); Serial.print(userPct); Serial.println('%');

  // Convert applied raw back to a 0..100 open-collector duty for reference
  float dutyPct = ((float)PWM_RANGE - (float)currentRaw) * 100.0f / (float)PWM_RANGE;
  if (dutyPct < 0.0f) dutyPct = 0.0f;
  if (dutyPct > 100.0f) dutyPct = 100.0f;

  Serial.print(F("applied_pwm_raw: "));     Serial.println(currentRaw);
  Serial.print(F("applied_pwm_percent: ")); Serial.println(dutyPct, 1);
  Serial.print(F("pwm_freq_hz: "));         Serial.println(PWM_FREQ_HZ);
  Serial.print(F("pwm_range: "));           Serial.println(PWM_RANGE);
  Serial.print(F("gate_pin: "));            Serial.println(GATE_PIN);
}

static void rebootToBootsel() {
#if defined(ARDUINO_ARCH_RP2040)
  Serial.println(F("Rebooting to BOOTSEL..."));
  Serial.flush();
  delay(50);
  reset_usb_boot(0, 0); // no return
#else
  Serial.println(F("ERR: BOOTSEL not supported on this platform"));
#endif
}

static void handleLine(String line) {
  line.trim();
  if (!line.length()) return;

  // Valid data received from host
  lastCmdTime = millis();
  if (fallbackTriggered) {
    fallbackTriggered = false;
  }

  String cmd = line; cmd.toUpperCase();

  if (cmd == "VERSION" || cmd == "VERSION?") { Serial.println(FW_VERSION); return; }
  if (cmd == "PING")                          { Serial.println(F("PONG")); return; }
  if (cmd == "RPM" || cmd == "RPM?")          { Serial.println(F("RPM: 0")); return; }
  if (cmd == "UPTIME" || cmd == "UPTIME?")    { printUptime();             return; }
  if (cmd == "STATUS" || cmd == "STATUS?")    { printStatus();             return; }
  if (cmd == "TEST") {
    const uint8_t prev = userPct;
    Serial.println(F("TEST: 0% 2s, 100% 5s, 0% 2s, restore"));
    setFanPercent(0,   false); delay(2000);
    setFanPercent(100, true ); delay(5000);
    setFanPercent(0,   false); delay(2000);
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
  for (uint16_t i = 0; i < line.length(); ++i) {
    if (!isDigit(line[i])) { numeric = false; break; }
  }
  if (numeric) {
    long v = line.toInt();
    if (v < 0)   v = 0;
    if (v > 100) v = 100;
    setFanPercent((uint8_t)v);
    Serial.print(F("Set fan to ")); Serial.print(userPct); Serial.println('%');
    return;
  }

  Serial.println(F("Unknown. Use: VERSION, PING, UPTIME, STATUS, TEST, BOOTSEL, 0..100"));
}

void setup() {
  Serial.begin(115200);
  const uint32_t t0 = millis();
  while (!Serial && (millis() - t0 < 2000)) { /* allow up to ~2s CDC attach */ }

  setupPwm();

  if (DO_SPINUP && START_PERCENT > 0) {
    setFanPercent(100, true);
    delay(SPINUP_MS);
  }
  setFanPercent(START_PERCENT, false);

  Serial.print(F("FANBRIDGE-LINK ")); Serial.print(FW_VERSION);
  Serial.println(F(" ready @115200 (PWM-only)"));
  Serial.println(F("Commands: VERSION, PING, RPM, UPTIME, STATUS, TEST, BOOTSEL, 0..100"));
  
  lastCmdTime = millis(); // Initialize watchdog timer
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    handleLine(line);
  }

  // Hardware watchdog: 60 seconds without a serial command
  if (millis() - lastCmdTime > 60000 && !fallbackTriggered) {
    fallbackTriggered = true;
    setFanPercent(100, false);
    Serial.println(F("ERR: Serial timeout. Failsafe activated (100%)."));
  }
}

