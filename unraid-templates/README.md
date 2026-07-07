# FanBridge Unraid Templates

Docker templates for the FanBridge container on Unraid.

FanBridge bridges Unraid drive temperatures to an external microcontroller (Arduino/RP2040) over USB serial to control PWM fans.

## Install

- In Unraid, install via Community Applications by searching for "FanBridge"; or
- Use the XML template at `templates/my-fanbridge.xml` directly when adding a container.

## Default Configuration

- Web UI port: `8080`
- AppData (config): `/mnt/maincache/appdata/fanbridge` → container `/config`
- Unraid emhttp directory (ro): `/var/local/emhttp` → container `/unraid`
- Serial device: host `/dev/ttyACM0` mapped to container `/dev/ttyACM0`
- Optional (advanced): `/var/local/emhttp/disks.ini` → container `/unraid/disks.ini` (ro)

These are minimal, working defaults. You can adjust paths to your environment as needed.

## Links

- Web UI: `http://[IP]:8080/`
- Support: https://forums.unraid.net/topic/193488-fanbridge-docker-support/
- Docker image: `ghcr.io/robro92/fanbridge:latest`
- Donate: https://ko-fi.com/robro92

## License

Template files are provided under the repository's license. See upstream project for application licensing.

