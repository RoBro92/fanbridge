# DIY firmware changelog

This changelog applies only to the single-channel Raspberry Pi Pico/RP2040 DIY target. Application releases use `v*`; DIY firmware releases use `fw-v*`. The future six-channel custom PCB will have its own target, artifact, and release stream.

## 2.5.0 — source ready, hardware validation pending

- Identifies the DIY target as `FANBRIDGE_DIY protocol=2 board=rp2040-zero channels=1 uid=<hex>`.
- Presents the human-readable startup name `DIY-RP2040-xxxx`, where `xxxx` is the final four hexadecimal characters of the persistent UID.
- Adds the bounded `IDENTIFY` command, which flashes the RP2040-Zero onboard WS2812 on GPIO16 for ten seconds without renewing the fan-control lease.
- Advertises `identify.led` and `identify_active` in `STATUS` telemetry.

The four-character suffix is a recognition aid, not the stored identity: FanBridge binds settings using the complete 16-character flash UID. No `fw-v2.5.0` artifact should be published until the 2.4.0 safety/identity tests and the GPIO16 WS2812 identification test have passed on physical hardware.

## 2.4.0 — source ready, hardware validation pending

- Adds a persistent board UID derived from the RP2040 board's paired flash unique ID.
- Advances the serial identity contract to protocol 2: `FANBRIDGE_DIY protocol=2 board=pico-dev channels=1 uid=<hex>`.
- Exposes the same UID as `controller_uid` in `STATUS` telemetry so host diagnostics can cross-check it.
- Keeps user names and settings on the FanBridge server; firmware identity is hardware-only and does not incur configuration flash writes.

No `fw-v2.4.0` artifact should be published until the 2.3.0 safety tests plus UID persistence and two-controller USB-path-swap tests have passed on physical hardware.

## 2.3.0 — source ready, hardware validation pending

- Starts and resets at a 100% cooling request before waiting for USB.
- Adds the machine-readable `FANBRIDGE_DIY protocol=1 board=pico-dev channels=1` identity and truthful capability/status fields.
- Renews the 60-second host-control lease only for a valid numeric `0..100` command; read-only diagnostics cannot preserve a stale setpoint.
- Returns to 100% when the host-control lease expires.
- Adds a 4-second RP2040 hardware watchdog so a stalled firmware loop resets into the full-speed state.
- Reports RPM as unsupported because the DIY target has no tachometer input.
- Pins the tested PlatformIO RP2040 platform to 1.17.0.

No `fw-v2.3.0` artifact should be published or added to the update manifest until the wiring, boot/BOOTSEL, watchdog, PWM waveform, lease, and real-fan tests in `README.md` have passed on hardware and the UF2 SHA-256 has been recorded.

## 2.2.0 — legacy migration only

The published 2.2.0 image starts at 0% and renews its timeout for every serial line, including unknown/read-only diagnostics. Upgrade it before unattended use. FanBridge 1.3 recognises the released response sequence, issues a one-shot validated 100% command, and quarantines automatic control as a migration safeguard.

## 2.1.0

Legacy single-channel DIY Pico firmware. Superseded by the 2.3.0 safety contract.
