# FanBridge Unraid Template

This template deploys FanBridge without privileged mode and gives it only the Unraid temperature data and serial devices it needs. It also enables Docker's no-new-privileges protection and drops all Linux capabilities; normal serial I/O, the high web port, and AppData access do not require them.

## Availability and installation

FanBridge is not currently listed in the public Community Applications feed while the listing is migrated. Until it appears there, install the template manually from an Unraid terminal:

```bash
mkdir -p /boot/config/plugins/dockerMan/templates-user
curl --fail --location --proto '=https' --tlsv1.2 \
  --output /boot/config/plugins/dockerMan/templates-user/my-fanbridge.xml \
  https://raw.githubusercontent.com/RoBroLabs/fanbridge/main/unraid-templates/templates/my-fanbridge.xml
head -n 2 /boot/config/plugins/dockerMan/templates-user/my-fanbridge.xml
```

Check that the output begins with the XML declaration and `<Container version="2">`, then open **Docker → Add Container** and select **FanBridge** from the template list. Leave **Privileged** off. The template uses the stable `ghcr.io/robrolabs/fanbridge:latest` image and the same settings submitted to Community Applications.

## Required mappings

| Setting | Host value | Container value | Purpose |
|---|---|---|---|
| Web UI | `8080` | `8080/tcp` | FanBridge interface. |
| AppData | `/mnt/user/appdata/fanbridge` | `/config` | Persistent settings, users, secrets, and history. |
| Unraid emhttp | `/var/local/emhttp` | `/unraid` (read-only) | Live `/unraid/disks.ini` temperature source. |
| Hotplug devices | `/dev` | `/host-dev` (read-only) | Stable serial discovery across reconnects. |
| RP2040 firmware USB bus | `/dev/bus/usb` | `/dev/bus/usb` | Optional non-privileged firmware updates. |

The template sets `FANBRIDGE_DISKS_STALE_WARN_SEC=600`, `FANBRIDGE_SECURE_COOKIES=0`, and host port `8080`. Change only the host side of the port mapping if `8080` is already occupied. Set secure cookies to `1` only behind HTTPS.

Select the controller's stable `/host-dev/serial/by-id/...` path in FanBridge, not a changing `/host-dev/ttyACM*` number. The read-only `/host-dev` path lets discovery follow USB reconnects, while the template's device-cgroup rules restrict actual device access to USB ACM serial and RP2040 BOOTSEL classes. Do not enable privileged mode.

After the container starts, use **Add Controller → Scan** to refresh the exposed serial-device list. DIY firmware 2.5.2 supplies a persistent full UID and an LED-identify action, allowing FanBridge to match the physical board to its saved server-side settings. The display suffix in `DIY-RP2040-xxxx` is only a recognition aid; the complete UID is the binding key.

FanBridge rebinds a registered controller only after matching its complete persistent UID. The `/host-dev` mapping keeps replacement paths visible when a controller is unplugged, moved, or restarts during a firmware update.

Map the entire `/var/local/emhttp` directory read-only. Do not add a second, overlapping bind for `disks.ini`, because a single-file bind can retain a stale inode when Unraid replaces the file.

## Temperature update cadence

FanBridge reads the temperatures that Unraid last wrote to `disks.ini`; it does not query drive SMART data itself. In **Settings → Disk Settings**, set **Tunable (poll_attributes)** to about `300` seconds (five minutes) for a useful cooling-control cadence. Keep `FANBRIDGE_DISKS_STALE_WARN_SEC` at about `600` seconds so one delayed or missed poll is tolerated before FanBridge treats the source as stale.

Shorter Unraid polling reacts sooner but adds SMART-query overhead and can affect parity-check performance or poorly behaved USB bridges. The FanBridge browser may refresh every few seconds, but those refreshes only reread the most recent Unraid sample; they do not make temperature telemetry instantaneous. See [Unraid's SMART monitoring documentation](https://docs.unraid.net/unraid-os/system-administration/monitor-performance/smart-reports-and-disk-health/).

## First-run access

Leave `FANBRIDGE_SETUP_TOKEN` blank to have FanBridge generate `/config/setup.token` and print the token once in the container log. Use that token when creating the first administrator account; FanBridge removes the generated token file after setup succeeds. You may instead preset a long random token, but Unraid stores environment values in its saved container template even when the UI masks them.

With the default AppData mapping, retrieve the generated token using either `docker logs FanBridge` or `cat /mnt/user/appdata/fanbridge/setup.token`, then open `http://<unraid-host>:8080/` and create the first administrator.

The template supports non-privileged RP2040 firmware updates. Keep the `/dev/bus/usb` mapping to use **Controller Settings → Link Updates & Firmware**; remove that optional mapping if firmware updating from FanBridge is not required. First-time installation and source-build instructions are in the [RP2040-Zero firmware guide](../fanbridge-link/README.md).

The template also makes the container root filesystem read-only and provides only a small no-exec `/tmp`; persistent writes belong under the AppData `/config` mapping.

If HTTPS terminates at a reverse proxy, set `FANBRIDGE_SECURE_COOKIES=1`. Do not expose the plain HTTP port directly to the internet.

## Links

- [Support thread](https://forums.unraid.net/topic/193488-fanbridge-docker-support/)
- [Container image](https://github.com/RoBroLabs/fanbridge/pkgs/container/fanbridge)
- [Project repository](https://github.com/RoBroLabs/fanbridge)
