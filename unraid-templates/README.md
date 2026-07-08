# FanBridge Unraid Templates

These are the official Docker templates for deploying the FanBridge container on Unraid. FanBridge intelligently bridges Unraid drive temperatures to the external FanBridge Link microcontroller (RP2040) over USB serial to control your system's PWM fans.

## Installation Methods

| Method | Instructions |
|---|---|
| **Community Applications** | In Unraid, navigate to the `Apps` tab and search for **"FanBridge"**. Click install. |
| **Manual XML** | Copy `templates/my-fanbridge.xml` into your Unraid templates directory and use the `Add Container` interface. |

## Default Configuration

The container is configured out-of-the-box with minimal, functional defaults. You can adjust these to fit your Unraid environment.

| Setting | Default Host Value | Container Mapping | Description |
|---|---|---|---|
| **Web UI Port** | `8080` | `8080` | Port for the FanBridge web interface. |
| **AppData** | `/mnt/maincache/appdata/fanbridge` | `/config` | Persistent storage for application configuration. |
| **emhttp Directory** | `/var/local/emhttp` | `/unraid` (Read-Only) | Directory used to read `disks.ini` for drive temperatures. |
| **Serial Device** | `/dev/ttyACM0` | `/dev/ttyACM0` | USB serial connection to the FanBridge Link controller. |

*Advanced Note: You can optionally bind just `/var/local/emhttp/disks.ini` directly to `/unraid/disks.ini` (Read-Only) instead of the entire emhttp folder.*

## Important Links

- **Support Thread:** [Unraid Forums](https://forums.unraid.net/topic/193488-fanbridge-docker-support/)
- **Docker Image:** [`ghcr.io/robro92/fanbridge:latest`](https://github.com/RoBro92/fanbridge/pkgs/container/fanbridge)
- **Donate:** [Support Development on Ko-fi](https://ko-fi.com/robro92)

## License

These template files are provided under the main repository's license. See the upstream project for application licensing details.
