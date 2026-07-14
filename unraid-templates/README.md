# Install FanBridge on Unraid

The FanBridge template deploys the application without privileged mode and gives it only the Unraid temperature data and USB devices it needs. It also enables Docker's `no-new-privileges` protection and drops all Linux capabilities.

## Availability and installation

FanBridge is not currently listed in the public Community Applications feed while the listing is migrated. Until it appears there, install the template manually from an Unraid terminal:

```bash
mkdir -p /boot/config/plugins/dockerMan/templates-user
curl --fail --location --proto '=https' --tlsv1.2 \
  --output /boot/config/plugins/dockerMan/templates-user/my-fanbridge.xml \
  https://raw.githubusercontent.com/RoBroLabs/fanbridge/main/unraid-templates/templates/my-fanbridge.xml
head -n 2 /boot/config/plugins/dockerMan/templates-user/my-fanbridge.xml
```

Check that the output begins with the XML declaration and `<Container version="2">`. Open **Docker → Add Container**, select **FanBridge** from the template list, and leave **Privileged** off. The template uses the stable `ghcr.io/robrolabs/fanbridge:latest` image.

## Required mappings

| Setting | Host value | Container value | Purpose |
|---|---|---|---|
| Web UI | `8080` | `8080/tcp` | FanBridge interface. |
| AppData | `/mnt/user/appdata/fanbridge` | `/config` | Persistent settings, users, secrets, and history. |
| Unraid emhttp | `/var/local/emhttp` | `/unraid` read only | Live `/unraid/disks.ini` temperature source. |
| Hotplug devices | `/dev` | `/host-dev` read only | Serial discovery across reconnects. |
| RP2040 firmware USB bus | `/dev/bus/usb` | `/dev/bus/usb` | Optional firmware updates from FanBridge. |

The template sets `FANBRIDGE_DISKS_STALE_WARN_SEC=600`, `FANBRIDGE_SECURE_COOKIES=0`, and host port `8080`. Change only the host side of the port mapping if `8080` is already occupied. Set secure cookies to `1` only when users reach FanBridge through HTTPS.

Map the complete `/var/local/emhttp` directory read only. Do not add a second bind for `disks.ini`. Unraid may replace that file, which can leave a single file bind attached to a stale inode.

The read only `/host-dev` mapping lets discovery follow USB reconnects. The template's device cgroup rules restrict actual access to USB ACM serial devices and RP2040 BOOTSEL devices. Do not enable privileged mode.

## First run

1. Start the container and open `http://<unraid-host>:8080/`.
2. Retrieve the generated setup token from the container log or AppData directory:

   ```bash
   docker logs FanBridge
   cat /mnt/user/appdata/fanbridge/setup.token
   ```

3. Create the first administrator with the setup token and a password of at least 8 characters.
4. Open **Add Controller**, press **Scan**, and select the controller's stable `/host-dev/serial/by-id/...` path.
5. Use **Identify** to flash the selected controller's onboard LED before adding it.
6. Open **Settings → Drive Assignment** and assign each disk to one controller or **Not Included**.
7. Review the fan curves and safety behaviour before enabling automatic output.

Leave `FANBRIDGE_SETUP_TOKEN` blank to let FanBridge create `/config/setup.token` and print the token once in the container log. FanBridge removes the generated token file after the first administrator is created. You may set your own long random token, but Unraid stores environment values in the saved container template even when the interface masks them.

Treat `/config` as sensitive. It contains the administrator database, session secret, configuration, history, and controller identities.

## Controller identity and USB reconnects

DIY firmware 2.5.2 reports a persistent full UID and presents a readable label in the form `DIY-RP2040-xxxx`. The final four hexadecimal characters help identify a physical board but are not globally unique. FanBridge stores settings against the complete UID.

Select the stable `/host-dev/serial/by-id/...` path instead of a changing `/host-dev/ttyACM*` number. If the controller is unplugged, moved to another USB port, or restarted during an update, FanBridge rebinds it only after matching the complete UID.

Legacy protocol 1 firmware remains bound to its configured container path and should be upgraded before unattended use.

## Drive temperature polling and safety

FanBridge reads the temperatures that Unraid last wrote to `disks.ini`; it does not query SMART directly. In **Settings → Disk Settings**, set **Tunable (poll_attributes)** to about `300` seconds for a useful cooling cadence. Keep `FANBRIDGE_DISKS_STALE_WARN_SEC` near `600` seconds so one delayed or missed poll is tolerated before the source is treated as stale.

Shorter Unraid polling reacts sooner but adds SMART query overhead and can affect parity checks or poorly behaved USB bridges. Refreshing the FanBridge page only rereads the latest Unraid sample and does not make temperature telemetry update sooner. See [Unraid's SMART monitoring documentation](https://docs.unraid.net/unraid-os/system-administration/monitor-performance/smart-reports-and-disk-health/).

Missing, malformed, stale, or incomplete active drive telemetry produces the configured fail safe demand. Sleeping or unassigned disks follow the idle policy.

Manual mode bypasses temperature curves but does not bypass FanBridge's mandatory safety layer. A critical assigned drive temperature, unsafe telemetry, or an unhealthy control loop forces an acknowledged 100% command. Critical temperature protection remains latched until temperatures clear the configured threshold by 3°C.

The DIY controller has no tachometer input. An acknowledgement confirms that the PWM target was accepted, not that the fan is physically spinning. Validate the fan and electrical path before unattended use.

## Firmware updates

Keep the `/dev/bus/usb` mapping to update registered DIY RP2040 controllers from **Controller Settings → Link Updates & Firmware**. Remove the mapping if updates from FanBridge are not required.

FanBridge holds cooling demand at 100% while the controller enters BOOTSEL, writes the image, and verifies the controller identity after restart. Remote installation accepts final `RoBroLabs/fanbridge` releases that meet the firmware safety baseline and contain both the RP2040 UF2 and its SHA256 companion. A local RP2040 UF2 can also be uploaded through the same panel.

The current firmware is 2.5.2. First time installation and source build instructions are in the [RP2040-Zero controller guide](../fanbridge-link/README.md).

## Docker CLI installation

The Unraid template is the recommended installation method. The equivalent Docker command is:

```bash
docker run -d \
  --name fanbridge \
  --restart unless-stopped \
  --user 0:100 \
  --security-opt=no-new-privileges \
  --cap-drop=ALL \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --device-cgroup-rule='c 166:* rmw' \
  --device-cgroup-rule='c 189:* rmw' \
  -p 8080:8080 \
  -v /mnt/user/appdata/fanbridge:/config \
  -v /var/local/emhttp:/unraid:ro \
  -v /dev:/host-dev:ro \
  -v /dev/bus/usb:/dev/bus/usb:rw \
  -e FANBRIDGE_DISKS_STALE_WARN_SEC=600 \
  ghcr.io/robrolabs/fanbridge:latest
```

| Variable | Default | Purpose |
|---|---:|---|
| `HOME` | `/tmp` | Writable runtime home for the read only container. |
| `FANBRIDGE_MAINTENANCE_MODE` | `0` | Set to `1` only while using manual PWM or fan test controls. |
| `FANBRIDGE_SETUP_TOKEN` | Generated | Optional initial setup token. Leave it unset for an interactive installation. |
| `FANBRIDGE_DISKS_STALE_WARN_SEC` | `600` | Age at which the Unraid temperature source becomes unsafe. |
| `FANBRIDGE_SECURE_COOKIES` | `0` | Set to `1` only when users reach FanBridge exclusively through HTTPS. |
| `GUNICORN_THREADS` | `4` | Concurrent request threads within the single hardware owning process. |
| `GUNICORN_TIMEOUT` | `30` | Gunicorn request timeout in seconds. |

Do not set multiple Gunicorn workers. Controller sessions, state, and scheduling are process local.

## Deployment security

- Keep the Web UI on a trusted LAN or behind an authenticated HTTPS reverse proxy.
- Do not expose the plain HTTP service directly to the internet.
- Leave privileged mode disabled.
- Back up `/config`, restrict its host permissions, and never copy it into an image or support bundle.
- The container root filesystem is read only and uses a small `noexec` `/tmp`. Persistent writes belong under `/config`.
- `/api/metrics` uses the normal FanBridge login session and does not currently offer a dedicated scraper token.
- Report vulnerabilities privately using [SECURITY.md](../SECURITY.md).

## Links

- [FanBridge overview](../README.md)
- [DIY RP2040-Zero controller guide](../fanbridge-link/README.md)
- [Support thread](https://forums.unraid.net/topic/193488-fanbridge-docker-support/)
- [Container image](https://github.com/RoBroLabs/fanbridge/pkgs/container/fanbridge)
