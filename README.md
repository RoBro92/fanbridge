# FanBridge

FanBridge is a Dockerised Unraid service designed to monitor hard drive temperatures and intelligently control external PWM fans via Arduino or RP2040 microcontrollers. It provides a seamless way to keep your drives cool by adjusting fan speeds based on drive temperature data, helping to extend drive lifespan and reduce noise.

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

## Usage

Run fanbridge easily via Docker or as an Unraid app:


### Unraid App

Install fanbridge directly through the Unraid Community Applications plugin for one-click deployment and management.

### Local Dev Setup (VS Code / Pylance)

To enable editor IntelliSense and fix missing import warnings (Flask, Werkzeug, dotenv, yaml), create a local virtualenv and install dev deps:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Then, in VS Code, select the interpreter at `.venv/bin/python` if not auto-picked. The workspace includes `.vscode/settings.json` that points to this venv.


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

## Changelog

For the canonical version history and detailed changelog, please refer to `fanbridge/RELEASE.md`.
