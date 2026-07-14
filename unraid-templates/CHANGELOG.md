# Changelog

## v1.3.0

- Converted the template to the current `<Container version="2">` format.
- Changed AppData to the portable `/mnt/user/appdata/fanbridge` path.
- Removed privileged-mode advice, firmware block/USB mappings, and the overlapping `disks.ini` bind.
- Enabled no-new-privileges and dropped all Linux capabilities.
- Made the container root filesystem read-only with a restricted no-exec `/tmp`.
- Added stable `/dev/serial/by-id` guidance and an optional second controller mapping.
- Added optional first-run setup-token configuration and hard-disabled in-container firmware flashing.
- Added a 600-second stale-data threshold and guidance for five-minute Unraid SMART polling.
- Corrected installation guidance while the app is absent from the public Community Applications feed.
- Finalised the stable 1.3.0 template and aligned its descriptions with the manual Unraid bootstrap settings.

## v1.2.3

- Bumped template version to `1.2.3`.

## v1.2.1

- Bumped template version to `1.2.1`.

## v1.2.0

- Set working defaults that match a known Unraid setup:
  - Port `8080`
  - AppData `/mnt/maincache/appdata/fanbridge` → `/config`
  - Read‑only Unraid emhttp dir `/var/local/emhttp` → `/unraid`
  - Serial TTY and device mapping `/dev/ttyACM0` → `/dev/ttyACM0`
- Removed advanced/optional environment variables to reduce confusion.
- Kept an optional advanced bind for `/unraid/disks.ini` (ro).
- Added Support link (Unraid forum thread).
- Added Donate link (Ko‑fi).
- Bumped template version to `1.2.0`.
