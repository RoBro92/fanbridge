# FanBridge

FanBridge is a Dockerised Unraid service that reads array disk temperatures and calculates PWM fan demand for external FanBridge Link controller boards. The repository currently supports the single-channel DIY Raspberry Pi Pico/RP2040 controller; a separate six-channel custom-PCB controller is planned. Automatic output is opt-in; when it is disabled, the interface clearly presents PWM as a recommendation rather than an applied value.

## Features

| Feature | Description |
|---|---|
| **Temperature-aware PWM** | Parses Unraid `disks.ini`, rejects stale input, and applies separate HDD/SSD curves, hysteresis, and fail-safe policy. |
| **Multiple controllers** | Assigns drives to distinct FanBridge Link boards, persists their hardware identities across USB-path changes, and excludes selected devices from control. |
| **USB serial diagnostics** | Verifies controller connectivity and provides read-only PING, firmware-version, and status commands. |
| **Secure first run** | Protects administrator creation with a one-time setup token, CSRF validation, strong password rules, and hardened response headers. |
| **Operational visibility** | Records temperature/PWM history and exposes authenticated logs, diagnostics, and Prometheus-format counters. |

## Hardware Controller

FanBridge has two related controller products. They share a host-protocol family, but they are not interchangeable firmware targets:

| Product | Channels | Current status |
|---|---:|---|
| **DIY FanBridge Link** | 1 | Supported now on the RP2040-Zero development board. Firmware 2.5.0 is in `fanbridge-link/rp2040`; the existing `fw-v<version>` releases and `fanbridge-rp2040-<version>.uf2` assets are for this product. |
| **Custom FanBridge Link PCB** | 6 | Planned raw-RP2040 product on engineering design hold. Its production firmware is not yet present in this repository. |

The products require distinct board identities, build targets, version streams, compatibility checks, and release artifacts. The custom-PCB design hold applies only to the six-channel product; it does not block building, releasing, or supporting DIY firmware 2.5.0. Do not treat the DIY UF2 as six-channel custom-PCB firmware.

For information on the FanBridge Link controller hardware setup and firmware, see the [fanbridge-link directory](fanbridge-link/README.md).

## Installation

### Unraid

FanBridge is not currently present in the public Community Applications feed while its listing is migrated. Until it is listed, install the version 2 template manually by following the [Unraid template instructions](unraid-templates/README.md).

The hardened deployment needs only these host resources:

| Parameter | Host value | Container value |
|---|---|---|
| **AppData** | `/mnt/user/appdata/fanbridge` | `/config` (read-write) |
| **Disk data** | `/var/local/emhttp` | `/unraid` (read-only) |
| **Controller 1** | `/dev/serial/by-id/<controller-id>` | `/dev/ttyACM0` |
| **Controller 2** | A different `/dev/serial/by-id/<controller-id>` | `/dev/ttyACM1` |

Use one stable host by-id path and one distinct container path per board. Add further Device mappings as `/dev/ttyACM2`, `/dev/ttyACM3`, and so on. Do not enable privileged mode, map all of `/dev`, or add an overlapping single-file bind for `/unraid/disks.ini`.

### Docker CLI

The equivalent single-controller deployment is:

```bash
docker run -d \
  --name fanbridge \
  --restart unless-stopped \
  --security-opt=no-new-privileges \
  --cap-drop=ALL \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -p 8080:8080 \
  -v /mnt/user/appdata/fanbridge:/config \
  -v /var/local/emhttp:/unraid:ro \
  --device=/dev/serial/by-id/REPLACE_WITH_YOUR_CONTROLLER:/dev/ttyACM0 \
  -e FANBRIDGE_DISKS_STALE_WARN_SEC=600 \
  ghcr.io/robrolabs/fanbridge:latest
```

### First-run access

The first administrator registration requires a bootstrap token. If `FANBRIDGE_SETUP_TOKEN` is left unset, FanBridge generates one at `/config/setup.token` and prints it once in the container startup log. Retrieve it with `docker logs fanbridge`; the generated token file is removed after successful registration. Keep the AppData directory private. For automated provisioning, you can preset a long random token through the environment instead.

## Production and security notes

- Keep the Web UI on a trusted LAN or behind an authenticated HTTPS reverse proxy. Set `FANBRIDGE_SECURE_COOKIES=1` only when clients reach FanBridge through HTTPS.
- FanBridge consumes Unraid's last `disks.ini` sample; it does not poll SMART directly. A practical control setup is Unraid **Tunable (poll_attributes)** at roughly 300 seconds and `FANBRIDGE_DISKS_STALE_WARN_SEC=600`. Faster polling improves response time but adds drive-query overhead, and the UI's faster refresh does not make the source telemetry instantaneous.
- The image deliberately runs one Gunicorn process with four `gthread` threads because serial sessions and control scheduling are process-local. `GUNICORN_THREADS` and `GUNICORN_TIMEOUT` remain configurable; increasing the worker count is unsupported.
- The session key is generated on first run and persisted as `/config/secret.key`. Back up `/config`, restrict its host permissions, and never copy it into an image.
- Prometheus-format counters are exposed at `/api/metrics`. The route is protected by the normal login session, and unauthenticated API requests receive JSON `401`. FanBridge does not yet issue a dedicated metrics token, so do not make this endpoint public to accommodate an unattended scraper.
- In-container firmware flashing is hard-disabled because updates are not yet safely bound to a controller product/hardware identity. The standard image receives no USB-bus or block-device access; use the checksum-verified host procedure.
- Synthetic temperature data is a local UI/policy-development aid only. Simulation mode never applies temperature-derived PWM to a controller, even if automatic output is configured.

## Firmware updates

Update the controller manually from the Unraid host or a trusted workstation. The current release workflow and update guide apply only to the single-channel DIY Pico/RP2040 target. It publishes a SHA-256 checksum alongside each DIY UF2 asset; verify it before copying the firmware to the BOOTSEL volume. No approved six-channel custom-PCB image exists yet. See the [firmware update guide](fanbridge-link/README.md).

## Architecture

- `container/app.py`: Flask app entry, app factory, middleware.
- `container/api/`: Route groups (blueprints) for serial, app info, and logs.
- `container/services/`: Core services for parsing `disks.ini` and serial discovery.
- `container/core/`: Infrastructure utilities for logging and metrics.
- `fanbridge-link/rp2040/`: Current single-channel DIY Pico/RP2040 firmware source.
- `hardware/fanbridge_link_spec.md`: Separate six-channel custom-PCB requirements and release gates.
- `unraid-templates/`: Unraid Docker templates.
- `docs/HANDOVER_AUDIT_2026-07-13.md`: Full safety, security, compatibility, and release-readiness handover.

## Changelog
For the canonical version history and detailed changelog, see `RELEASE.md`.
