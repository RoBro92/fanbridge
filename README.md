<p align="center">
  <img src="Images/title.png" alt="FanBridge" width="800" />
</p>

FanBridge is a Dockerised Unraid service designed to monitor hard drive temperatures and intelligently control external PWM fans via Arduino or RP2040 microcontrollers. It provides a seamless way to keep your drives cool by adjusting fan speeds based on drive temperature data, helping to extend drive lifespan and reduce noise.

## Features

| Feature | Description |
|---|---|
| **Intelligent PWM Control** | Parses Unraid `disks.ini` for drive state and temperatures to recommend optimal PWM. |
| **USB Serial Interface** | Seamless USB serial control for Arduino/RP2040 with quick tools (PING, version). |
| **Highly Configurable** | Configurable fan curves, manual overrides, drive exclusions, and default resets. |
| **Secure Web UI** | Clean UI (dark/light mode) with authentication, CSRF protection, and secure headers. |
| **Observability** | Built-in logs API (ring buffer) and Prometheus metrics for monitoring. |

## Screenshots

<div>
<p>
  <strong>Login</strong><br/>
  <img src="Images/login.png" alt="Login page" width="800" />
</p>

<p>
  <strong>Drives</strong><br/>
  <img src="Images/drives.png" alt="Drives configuration" width="800" />
</p>

<p>
  <strong>Serial</strong><br/>
  <img src="Images/serial.png" alt="Serial page" width="800" />
</p>

<p>
  <strong>Logs</strong><br/>
  <img src="Images/logs.png" alt="Logs page" width="800" />
</p>
</div>

## Hardware Controller

FanBridge relies on the **FanBridge Link** hardware controller. A custom-designed PCB is currently in development to provide a streamlined, plug-and-play experience. 

For information on the FanBridge Link controller hardware setup and firmware, see the [firmware directory](firmware/README.md).

## Installation

FanBridge can be installed via Docker or as an Unraid app.

### Unraid App (Recommended)

Install FanBridge directly through the Unraid Community Applications plugin for one-click deployment and management.

| Parameter | Recommended Setting | Description |
|---|---|---|
| **Device mapping** | Leave blank by default | Allows Docker to start even if the controller is unplugged. You can map the device later when connected. |
| **Serial path** | `/dev/serial/by-id` (read-only) | Optional mapping for stable path naming. |
| **Privileged** | Off | No cgroup rules or group-add are required when using standard device mapping. |

*Note: The preferred serial port defaults to `/dev/ttyACM0` (RP2040). You may override this via the `FANBRIDGE_SERIAL_PORT` environment variable.*

### Production Tips

- **Proxy/TLS:** Reverse proxy and TLS are recommended. Set `FANBRIDGE_SECURE_COOKIES=1` when HTTPS terminates in front of the application.
- **Workers:** Tune Gunicorn via environment variables: `GUNICORN_WORKERS` (default 2) and `GUNICORN_TIMEOUT` (default 30).
- **Metrics:** Scrape `/metrics` (text format) for basic counters.
- **Session Secret:** Generated on the first run and persisted at `/config/secret.key` (Docker) or `container/secret.key` (local).

## Roadmap

| Category | Planned Feature | Status |
|---|---|---|
| **Hardware** | Custom PCB release for FanBridge Link | In Progress |
| **UI/UX** | Historical charts and richer dashboards | Planned |
| **UI/UX** | Live updates via WebSocket/SSE for smoother refresh | Planned |
| **Controller** | Read RPM data from fan tachometer | Planned |

## Architecture

- `container/app.py`: Flask app entry, app factory, middleware.
- `container/api/`: Route groups (blueprints) for serial, app info, and logs.
- `container/services/`: Core services for parsing `disks.ini` and serial discovery.
- `container/core/`: Infrastructure utilities for logging and metrics.
- `firmware/`: RP2040 FanBridge Link firmware source.
- `unraid-templates/`: Unraid Docker templates.

## Changelog
For the canonical version history and detailed changelog, see `RELEASE.md`.
