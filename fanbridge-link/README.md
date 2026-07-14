# DIY FanBridge Link for RP2040-Zero

The DIY FanBridge Link is the supported single-channel controller for FanBridge. It uses an RP2040-Zero to generate the 25 kHz control signal required by a four-wire PWM fan and communicates with the FanBridge Docker application over USB serial.

Current firmware: **[2.5.2](https://github.com/RoBroLabs/fanbridge/releases/tag/fw-v2.5.2)**

## Hardware

You need:

- One RP2040-Zero with the onboard WS2812 connected to GPIO16.
- One 2N3904 NPN transistor.
- One approximately 1 kΩ base resistor.
- One 10 kΩ base-to-emitter resistor.
- A four-wire PWM fan or PWM fan hub and its correctly rated 12 V supply.
- A common ground between the RP2040, transistor circuit, and fan supply.

Check the lead order in the datasheet for the exact transistor you use; 2N3904 package pinouts vary between manufacturers.

## Wiring

| Connection | Destination |
|---|---|
| RP2040-Zero `GP15` | 1 kΩ resistor, then 2N3904 base |
| 10 kΩ resistor | Between 2N3904 base and emitter |
| 2N3904 emitter | RP2040 ground and fan ground |
| 2N3904 collector | Fan PWM input, connector pin 4 |
| Fan pin 1 | 12 V supply ground |
| Fan pin 2 | Correctly rated 12 V supply |
| Fan pin 3 | Not connected; this controller has no tachometer input |

Do not connect 12 V to an RP2040 GPIO or USB VBUS. Do not drive the fan PWM wire directly from a push-pull 3.3 V GPIO. The external 10 kΩ resistor is required so the transistor remains off and the fan PWM input is released while the board is unpowered, resetting, or in BOOTSEL mode.

The fan or hub must run at its safe/full speed when its PWM input is released.

## Install firmware on a new controller

Download both assets from the [2.5.2 firmware release](https://github.com/RoBroLabs/fanbridge/releases/tag/fw-v2.5.2):

- `fanbridge-rp2040-2.5.2.uf2`
- `fanbridge-rp2040-2.5.2.uf2.sha256`

Verify the download on Linux:

```bash
sha256sum --check fanbridge-rp2040-2.5.2.uf2.sha256
```

On macOS:

```bash
shasum -a 256 -c fanbridge-rp2040-2.5.2.uf2.sha256
```

On Windows, compare the result from the following command with the digest written in the `.sha256` file:

```powershell
Get-FileHash .\fanbridge-rp2040-2.5.2.uf2 -Algorithm SHA256
```

Hold the RP2040-Zero **BOOTSEL** button while connecting its USB cable. A drive named `RPI-RP2` appears. Copy `fanbridge-rp2040-2.5.2.uf2` to that drive; the controller programs itself and restarts automatically.

Connect the controller to the Unraid server, open FanBridge, then use **Add Controller → Scan**. The device presents as `DIY-RP2040-xxxx`; press **Identify** to flash its onboard LED before adding it.

## Update an installed controller from FanBridge

The current Unraid template exposes `/dev/bus/usb` and the required USB device classes without privileged mode.

1. Open the controller in FanBridge.
2. Select **Controller Settings → Link Updates & Firmware**.
3. Press **Check for updates**.
4. Select **Install firmware 2.5.2** when offered.

FanBridge verifies the release checksum, commands the registered controller into BOOTSEL, holds cooling demand at 100%, writes the image with `picotool`, and verifies the same controller identity after restart. A locally built or downloaded RP2040 UF2 can instead be installed with **Upload verified .uf2**.

If USB firmware access has been removed from the container, stop FanBridge and use the BOOTSEL copy procedure above from a trusted computer.

## Build from source

The firmware source is in `fanbridge-link/rp2040`. PlatformIO and the RP2040 platform version are pinned for reproducible builds.

From the repository root:

```bash
python3 -m venv .pio-venv
./.pio-venv/bin/python -m pip install --upgrade pip platformio==6.1.19
./.pio-venv/bin/pio run --project-dir fanbridge-link/rp2040
```

The resulting file is:

```text
fanbridge-link/rp2040/.pio/build/pico/firmware.uf2
```

Install that file using **Upload verified .uf2** in FanBridge or the physical BOOTSEL procedure.

## Firmware behaviour

- Starts with the fan request at 100%.
- Produces an inverted, open-collector 25 kHz PWM control signal on `GP15`.
- Accepts numeric PWM targets from `0` to `100` over 115200-baud USB serial.
- Returns to 100% if FanBridge does not renew the control lease for 60 seconds.
- Uses the RP2040 hardware watchdog to recover to the full-speed startup state after a software stall.
- Reports a persistent 16-character hardware UID so FanBridge can retain settings when USB paths change.
- Presents the readable name `DIY-RP2040-xxxx`, where the suffix is a recognition aid derived from the UID.
- Flashes the GPIO16 onboard WS2812 for ten seconds when it receives `IDENTIFY`.
- Supports serial `BOOTSEL` entry for verified updates from FanBridge.
- Does not provide tachometer/RPM feedback.

## Serial commands

| Command | Behaviour |
|---|---|
| `0` through `100` | Set PWM percentage and renew the 60-second control lease. |
| `PING` | Return `PONG`. |
| `VERSION` | Return the firmware version. |
| `ID?` | Return product, protocol, board, channel count, and full UID. |
| `NAME?` | Return `DIY-RP2040-xxxx`. |
| `STATUS` | Return structured firmware, capability, UID, uptime, PWM, and lease status. |
| `IDENTIFY` | Flash the onboard LED for ten seconds without renewing the PWM lease. |
| `RPM?` | Report that tachometer feedback is unsupported. |
| `BOOTSEL` | Release the fan input, turn off the LED, and reboot into RP2040 BOOTSEL mode. |

FanBridge controller names, drive assignments, fan curves, and settings remain server-side and are bound to the complete hardware UID.

See [CHANGELOG.md](CHANGELOG.md) for firmware history and the main [FanBridge README](../README.md) for Docker and Unraid installation.
