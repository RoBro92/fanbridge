# FanBridge Link firmware

FanBridge Link controllers receive PWM commands from the FanBridge service over USB serial. This repository currently contains firmware for the single-channel DIY Raspberry Pi Pico/RP2040 controller only.

## Firmware targets

| Product target | Board identity | Channels | Source and release status |
|---|---|---:|---|
| **DIY FanBridge Link** | `FANBRIDGE_DIY`, board `rp2040-zero` | 1 | Available now in `rp2040/`. The current source version is 2.5.0. Existing `fw-v<version>` tags and `fanbridge-rp2040-<version>.uf2` assets belong only to this target. |
| **Custom FanBridge Link PCB** | A separate production identity is required | 6 | Planned raw-RP2040 target on design hold. Its firmware source, build target, tag convention, and artifact name have not been implemented or approved. |

These are independently versioned products: a version number or UF2 for one target says nothing about compatibility with the other. The future custom-PCB workflow must use a distinct target-specific tag and artifact name and must validate the board identity before an update. The custom-PCB design hold does not block DIY 2.5.0 builds, releases, or support.

## DIY electrical interface

The DIY target is a single open-collector PWM control channel on Pico `GP15`. Wire it as follows; check the exact transistor lead order against the datasheet for the part you actually bought because 2N3904 package pinouts vary by manufacturer:

- Pico `GP15` to a roughly 1 kΩ resistor, then to the 2N3904 base.
- A 10 kΩ resistor from base to emitter. This external bias is mandatory: it keeps the transistor off while the Pico is unpowered, resetting, in ROM BOOTSEL, or before firmware configures the GPIO.
- Transistor emitter to Pico ground and fan ground. The USB/Pico and 12 V fan supply need this common signal reference.
- Transistor collector to the four-wire fan's PWM input (fan connector pin 4).
- Power the fan normally from its qualified 12 V supply (pin 2) and return (pin 1). Do not connect 12 V to a Pico GPIO or USB VBUS. The DIY firmware does not read the tach output on pin 3.

The fan itself must run at its safe/full speed when the PWM input is released; qualify this behavior with the exact fan model. The firmware uses inverted Pico output because a low GP15 turns the NPN off and releases the fan's internally pulled-up PWM input. Do not substitute a push-pull 3.3 V connection to the fan PWM wire.

Firmware 2.2 and earlier started at 0% and renewed their fallback timer for diagnostic commands. The repaired host detects the published legacy DIY responses, sends a one-shot validated 100% command, and quarantines automatic output, but that is only a migration safeguard. Upgrade existing DIY boards to 2.5.0 before relying on them for unattended cooling, persistent USB identity, and LED identification.

## Persistent controller identity

Firmware 2.4.0 and newer report `uid=<hex>` in their protocol-2 `ID?` response. The value comes from the RP2040 board's paired flash unique ID and is not the user-facing controller name. FanBridge stores the complete 16-character UID with the controller configuration, while keeping the name, drive assignments, curves, and other settings server-side. If visible controller device paths change, FanBridge scans them and rebinds only an exact UID match; it refuses duplicate UIDs and never guesses from product type alone.

Firmware 2.5.0 presents as `DIY-RP2040-xxxx`, using the last four hexadecimal UID characters as a short physical recognition label. Four hexadecimal characters cannot guarantee fleet-wide uniqueness, so this suffix is never used as the binding key; the full UID remains authoritative.

Protocol-1 firmware remains compatible but is explicitly port-bound. After an existing controller is upgraded to 2.4.0 or newer, FanBridge records its UID on the first verified handshake while preserving its name, assignments, and settings. The replacement path must still be exposed inside the container. Docker device mappings are fixed at container creation, so a host device that disappears from the container may require correcting the stable `/dev/serial/by-id/` mapping and restarting the container.

## Pre-enrolment LED identification

The Add Controller dialog can send the fixed `IDENTIFY` command to a selected, unregistered DIY controller. Firmware 2.5.0 flashes the RP2040-Zero onboard WS2812 on GPIO16 in orange for ten seconds and replies `IDENTIFYING duration_ms=10000`. The command is non-blocking and deliberately does not renew or create a PWM control lease. The host performs a protocol-2 identity handshake first and does not expose arbitrary serial or PWM commands through this endpoint.

This behaviour applies only to the DIY RP2040-Zero. The planned speaker/beeper identification for the official custom PCB is a separate hardware and protocol task and is not implemented here.

## Build the DIY source

The tested PlatformIO platform is pinned in `rp2040/platformio.ini`. From the repository root:

```bash
python3 -m venv .pio-venv
./.pio-venv/bin/python -m pip install --upgrade pip platformio==6.1.19
./.pio-venv/bin/pio run --project-dir fanbridge-link/rp2040
```

The resulting manual-flash image is `fanbridge-link/rp2040/.pio/build/pico/firmware.uf2`. A successful compile is not hardware qualification.

## DIY hardware release test

No physical board was attached during the repository repair, so 2.5.0 must not be described as hardware-validated until the following results are recorded for the actual RP2040-Zero, transistor circuit, fan, hub (if any), and power supply:

1. Scope GP15 and the fan-side PWM input through power-up, USB attach, reset, watchdog reset, and the entire ROM BOOTSEL interval; the fan input must remain released/full-speed until a valid numeric command is accepted.
2. Confirm `ID?` reports `FANBRIDGE_DIY protocol=2 board=rp2040-zero channels=1 uid=<16-hex-character-id>`, the UID persists across reset, power loss, and firmware reflashing, numeric `0..100` commands return the exact requested acknowledgement, malformed/out-of-range commands do not change output, and `RPM?` reports unsupported rather than a fabricated zero.
3. Measure approximately 25 kHz PWM and requested/applied duty at 0%, intermediate points, and 100%; record the fan's minimum start/run behavior and verify transistor voltage/current margins.
4. Stop host commands and confirm the 60-second control lease returns to 100%. Deliberately stall application execution and confirm the 4-second hardware watchdog resets into the full-speed state.
5. Select the unregistered controller in Add Controller, confirm the suggested name matches `DIY-RP2040-xxxx`, press Identify, and verify only that board's GPIO16 WS2812 flashes for ten seconds. Confirm identification does not extend a prior PWM lease and that the LED turns off on timeout and before BOOTSEL.
6. Run the container against a real Unraid `disks.ini`: normal curve demand, hottest-drive override, active missing temperature, stale/missing source, disk wake-up disagreement, controller reconnect, automatic-control disable, and container stop must all produce the expected safe result.

Record the equipment, firmware commit, measurements, and pass/fail result for every item. Only after review should a repository administrator set the protected Actions variable `DIY_FIRMWARE_HIL_APPROVED_VERSION` to the exact version being released. The DIY release workflow refuses to publish any other version.

## Firmware-update policy

Container-side flashing is hard-disabled, not merely hidden behind an environment toggle. The standard Docker and Unraid configurations intentionally have no USB-bus, block-device, or mount access. Manual flashing from the Unraid host or a trusted workstation is the supported path until separate product identities, signed/target-bound manifests, and safe device selection have been implemented and reviewed.

## Verified DIY update from an Unraid terminal

These instructions apply only to the single-channel DIY Pico/RP2040 target. Do not flash the resulting image to a future six-channel custom PCB. Choose a DIY firmware release that includes both the UF2 and its `.sha256` companion. New DIY releases created by this repository's workflow publish both files. Download them over HTTPS and verify the checksum before touching the controller:

```bash
set -euo pipefail

VERSION="REPLACE_WITH_RELEASE_VERSION"  # for example: 2.5.0
ASSET="fanbridge-rp2040-${VERSION}.uf2"
BASE_URL="https://github.com/RoBroLabs/fanbridge/releases/download/fw-v${VERSION}"
WORK_DIR="$(mktemp -d /tmp/fanbridge-firmware.XXXXXX)"
cd "$WORK_DIR"

curl --fail --location --proto '=https' --tlsv1.2 \
  --output "$ASSET" "$BASE_URL/$ASSET"
curl --fail --location --proto '=https' --tlsv1.2 \
  --output "$ASSET.sha256" "$BASE_URL/$ASSET.sha256"
sha256sum --check "$ASSET.sha256"
```

Run all of the following command blocks in the same Bash session so the verified asset variables remain available. Stop the FanBridge container so it cannot write to the serial port during the update. Set `CONTAINER_NAME` to the name shown in Unraid, set `SERIAL_DEVICE` to the controller's stable host identity, confirm it is a character device, and request BOOTSEL mode:

```bash
CONTAINER_NAME="fanbridge"  # commonly "FanBridge" when installed from the Unraid template
docker stop "$CONTAINER_NAME"
restart_container() { docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true; }
trap restart_container EXIT

SERIAL_DEVICE="/dev/serial/by-id/REPLACE_WITH_YOUR_CONTROLLER"
test -c "$SERIAL_DEVICE"
stty -F "$SERIAL_DEVICE" 115200 raw -echo
printf 'BOOTSEL\n' > "$SERIAL_DEVICE"
```

The controller will disappear as a serial device and re-enumerate as an `RPI-RP2` volume. Resolve that exact label, verify the target, mount it with restrictive options, and copy the already-verified UF2:

```bash
set -euo pipefail

BOOT_LINK="/dev/disk/by-label/RPI-RP2"
for attempt in $(seq 1 15); do
  test -e "$BOOT_LINK" && break
  sleep 1
done
test -e "$BOOT_LINK"

BOOT_DEVICE="$(readlink -f "$BOOT_LINK")"
test -b "$BOOT_DEVICE"
test "$(lsblk -no LABEL "$BOOT_DEVICE" | head -n 1)" = "RPI-RP2"

MOUNT_DIR="$(mktemp -d /tmp/fanbridge-rp2.XXXXXX)"
cleanup() {
  if mountpoint -q "$MOUNT_DIR"; then
    umount "$MOUNT_DIR" 2>/dev/null || true
  fi
  rmdir "$MOUNT_DIR" 2>/dev/null || true
  docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mount -o rw,nosuid,nodev,noexec "$BOOT_DEVICE" "$MOUNT_DIR"
cp -- "$WORK_DIR/$ASSET" "$MOUNT_DIR/$ASSET"
sync
if mountpoint -q "$MOUNT_DIR"; then
  umount "$MOUNT_DIR"
fi
rmdir "$MOUNT_DIR" 2>/dev/null || true
rm -rf -- "$WORK_DIR"

sleep 5
if ! test -c "$SERIAL_DEVICE"; then
  echo "Warning: the stable serial path has not returned; check the USB connection." >&2
fi
docker start "$CONTAINER_NAME"
trap - EXIT
```

If the serial path does not return, unplug and reconnect the controller, then check `ls -l /dev/serial/by-id/`. If `RPI-RP2` never appears, hold the physical BOOTSEL button while reconnecting USB. Never select a raw `/dev/sdX` device by guesswork.

## DIY Pico first-time installation

An unprogrammed DIY Pico cannot accept the serial `BOOTSEL` command. Download and verify the DIY UF2 and checksum as above, hold the board's physical BOOTSEL button while connecting USB, and confirm the mounted volume is labelled `RPI-RP2`. Copy the UF2 to that volume; the board will program itself and restart.

On a desktop workstation the BOOTSEL volume is normally mounted automatically, so the final copy can be performed in the file manager after checksum verification.

## Release naming

- The current DIY release stream uses `fw-v<diy-version>` tags.
- DIY assets use `fanbridge-rp2040-<diy-version>.uf2` and a matching `.uf2.sha256` file. Version 2.5.0 is the current DIY source version.
- The future six-channel custom PCB must have its own version stream, target-specific tag convention, artifact name, checksum, and compatibility validation. Those names remain intentionally undefined until its hardware and firmware targets are approved.
- Never infer cross-product compatibility from matching version numbers, the shared RP2040 architecture, or the generic FanBridge Link name.

See [CHANGELOG.md](CHANGELOG.md) for the independent DIY firmware history.
