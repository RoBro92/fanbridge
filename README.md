# FanBridge v1.0.0

FanBridge is a Dockerised Unraid service designed to monitor hard drive temperatures and intelligently control external PWM fans via Arduino or RP2040 microcontrollers. It provides a seamless way to keep your drives cool by adjusting fan speeds based on drive temperature data, helping to extend drive lifespan and reduce noise.

This is the 1.0.0 release.

## Features

- Parses Unraid's `disks.ini` to identify drives and their configurations.
- Displays real-time drive temperatures and states in an intuitive web UI.
- Allows exclusion of specific drives from monitoring and fan control.
- Supports configurable fan curves to tailor fan speed responses to temperature changes.
- Provides override settings for manual fan speed control.
- Includes a dark mode toggle for comfortable viewing.
- Offers API endpoints for integration and automation:
  - `/` – homepage with status
  - `/health` – healthcheck endpoint
  - `/api/status` – JSON output with drive temperatures and recommended PWM values
- Authentication with login, password change, and logout functionality.
- CSRF protection and session handling for secure interactions.
- Validation for fan curve inputs to ensure correct configurations.
- Reset to defaults for config and fan curves.
- Status and error surfacing via toasts and banners.
- Mobile-friendly responsive UI for use on various devices.
- Theme menu with dark/light toggle for user preference.
- USB serial communication with Arduino/RP2040 including test integration.
- Link Updates panel shows controller version, manifest URL, and latest version (updates remain disabled in-container).
- Logs tab defaults the download window to the last 24 hours.
- Persistent footer (API, Health, Support, Donate) across tabs.
- Minimal Prometheus metrics at `/metrics` (HTTP requests, serial commands, serial open failures).
- Optional secure cookies via `FANBRIDGE_SECURE_COOKIES=1`; standard security headers.

## Usage

Run fanbridge easily via Docker or as an Unraid app:


### Unraid App

Install fanbridge directly through the Unraid Community Applications plugin for one-click deployment and management.

Unprivileged setup:
- Add a Device mapping for your controller (prefer `/dev/serial/by-id/…`).
- Map `/dev/serial/by-id` into the container read-only.
- Optionally set `FANBRIDGE_SERIAL_PORT` to the by-id path; otherwise the app auto-detects.
- Keep “Privileged” off. No cgroup rules or group-add are required when using Device mapping.

Production tips:
- Reverse proxy/TLS recommended; set `FANBRIDGE_SECURE_COOKIES=1` when HTTPS terminates in front.
- Tune Gunicorn via env: `GUNICORN_WORKERS` (default 2) and `GUNICORN_TIMEOUT` (default 30).
- Metrics: scrape `/metrics` (text format) for basic counters.

### Local Dev Setup (VS Code / Pylance)

To enable IntelliSense and fix missing import warnings (Flask, Werkzeug, dotenv, yaml), create a local virtualenv and install dev deps. You can do this either at the repo root or inside `fanbridge/` — the latter is recommended if you open the `fanbridge/` folder directly in VS Code.

Option A — venv inside `fanbridge/` (recommended when opening `fanbridge/`):

```
cd fanbridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Option B — venv at repo root (if your workspace root is the repo root):

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Then select the interpreter in VS Code (Command Palette → “Python: Select Interpreter”).
If you opened the `fanbridge/` folder, the workspace file `fanbridge/.vscode/settings.json` points to `fanbridge/.venv/bin/python`.


## Roadmap / Planned Features

- ~~Full integration with Arduino or RP2040 microcontrollers to enable real-time fan speed control based on drive temperatures.~~ (partial test mode implemented)
- ~~Secure authentication and user management for enhanced access control.~~
- Richer dashboards with historical temperature and fan speed data visualization.
- ~~Packaging as a one-click Unraid app for simplified installation and updates.~~

## Upcoming Ideas

- Live updates via WebSocket/SSE for smoother refresh.
- Per-drive details drawer with extended info.
- Sparkline mini-graphs for temps.
- Fan curve profiles (Quiet/Balanced/Performance).
- Temporary override slider for manual fan control.
- Sorting & filtering in the drive table.
- Import/export of configs for sharing.
- Enhanced security headers and rate limiting.

## Release Notes (1.0.0)
- Unprivileged serial operation using Device mapping; by-id paths preferred.
- UI polish: logos, persistent footer, Link Updates information restored.
- Logs: default last 24 hours for downloads.
- Security: optional secure cookies + standard headers.
- Ops: `/metrics` endpoint and configurable Gunicorn timeout.

## Changelog
For the canonical version history and detailed changelog, see `fanbridge/RELEASE.md`.
