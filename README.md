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

## Usage

Run fanbridge easily via Docker or as an Unraid app:

```

### Unraid App

Install fanbridge directly through the Unraid Community Applications plugin for one-click deployment and management.

## Roadmap / Planned Features

- Full integration with Arduino or RP2040 microcontrollers to enable real-time fan speed control based on drive temperatures.
- Secure authentication and user management for enhanced access control.
- Richer dashboards with historical temperature and fan speed data visualization.
- Packaging as a one-click Unraid app for simplified installation and updates.

## Changelog

For the canonical version history and detailed changelog, please refer to `fanbridge/RELEASE.md`.  

