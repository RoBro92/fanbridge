<p align="center">
  <img src="container/static/fanbridge.png" alt="FanBridge logo" width="120" />
</p>

<h1 align="center">FanBridge for Unraid</h1>

<p align="center">
  Unraid disk temperatures in. Safe, controller specific PWM out.
</p>

FanBridge is a Dockerised Unraid service that reads disk temperature and state data from Unraid, calculates cooling demand, and controls external PWM fan hardware over USB serial. Drives can be assigned to separate controllers so each enclosure or cooling zone follows the disks it contains.

FanBridge 1.4.0 supports the single channel DIY FanBridge Link built around an RP2040-Zero. The current controller firmware release is 2.5.2.

> [!IMPORTANT]
> FanBridge controls physical cooling hardware. Automatic output is opt in. Validate every deployment with its actual fans, controller, USB connection, and Unraid host before leaving it unattended.

## Current interface

### Controller dashboard

<p align="center">
  <img src="docs/images/dashboard-diy-rp2040.png" alt="FanBridge DIY RP2040 controller dashboard" width="1000" />
</p>

### Global drive assignments

<p align="center">
  <img src="docs/images/drive-assignments.png" alt="FanBridge global drive assignment screen" width="1000" />
</p>

## What FanBridge does

| Capability | Behaviour |
|---|---|
| **Unraid disk data** | Reads drive identity, state, type, temperature, serial number, and capacity from `/unraid/disks.ini`. |
| **Controller specific cooling** | Assigns each disk to one controller or leaves it out of FanBridge control. |
| **Temperature based PWM** | Uses separate HDD and SSD curves, the hottest assigned drive, hysteresis, overrides, and configurable idle and fail safe output. |
| **Persistent controller identity** | Stores controller settings against the full hardware UID and restores them after a USB path change. |
| **Monitoring and diagnostics** | Provides dashboards, history, logs, serial diagnostics, and authenticated metrics. |

## Supported controller

| Product | Identity | Channels | Status |
|---|---|---:|---|
| **DIY FanBridge Link** | `FANBRIDGE_DIY`, `rp2040-zero` | 1 | Firmware 2.5.2 with persistent identity, LED identification, safe control leasing, and verified updates from FanBridge. |

## Documentation

- [Install FanBridge on Unraid](unraid-templates/README.md)
- [Build and install the DIY RP2040-Zero controller](fanbridge-link/README.md)
- [View FanBridge releases](RELEASE.md)
- [View DIY firmware changes](fanbridge-link/CHANGELOG.md)
- [Get support](https://forums.unraid.net/topic/193488-fanbridge-docker-support/)
- [Report a security issue](SECURITY.md)
