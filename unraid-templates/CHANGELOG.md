# Changelog

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

