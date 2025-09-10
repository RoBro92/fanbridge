Version: 1.0.0

# Changelog

## 1.0.0 — 2025-09-10

Highlights
- Unprivileged by default on Unraid: use Device mapping for your USB serial controller (prefer `/dev/serial/by-id/...`). No privileged/cgroup rules required.
- Link Updates panel restored (view-only): shows controller version, manifest URL, and latest available firmware. In-container flashing remains disabled.
- New `/metrics` endpoint (Prometheus text) with counters for HTTP requests, serial commands, and serial open failures.
- Logs page defaults the download window to the last 24 hours.
- Persistent footer across tabs with GitHub and Donate links.
- Optional secure cookies via `FANBRIDGE_SECURE_COOKIES=1`; standard security headers and CSP applied.
- Clean Ko‑fi support: lightweight floating button opens an in‑app modal with a Ko‑fi iframe (no external overlay scripts).

UI & UX
- Top-right FanBridge logo integrated into the header (not floating overlay).
- Login page logo sizing and spacing refined.
- Added favicon/apple-touch icons (uses `static/fanbridge.png`).
- Theme-consistent Ko‑fi modal and backgrounds; compact modal width.
- Footer now: GitHub | Donate | API | Health | Support; persists across tabs.

API & Backend
- `/api/rp/status` reports `privileged`, controller version, repo/board, manifest URL, `latest`, and `update_available`.
- Privilege detection fixed to check CAP_SYS_ADMIN precisely (no false “privileged: yes”).
- New `/metrics` endpoint with counters:
  - `fanbridge_http_requests_total{method,code}`
  - `fanbridge_serial_commands_total{kind,status}`
  - `fanbridge_serial_open_failures_total`
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and CSP (iframe ko‑fi.com allowed).
- Cookie hardening toggle: `FANBRIDGE_SECURE_COOKIES=1` for HTTPS deployments.

Container & Templates
- Dockerfile: configurable Gunicorn timeout via `GUNICORN_TIMEOUT` (default 30s); `GUNICORN_WORKERS` env respected.
- Unraid template migrated to schema-style `<Container>` with nested Networking/Data; device mapping recommended via UI.
- Template advises mapping `/dev/serial/by-id` (ro) and setting `FANBRIDGE_SERIAL_PORT` to a by-id path (optional).

Developer Experience
- Added dev requirements and VS Code settings to fix editor import warnings (Flask/Werkzeug/dotenv/PyYAML) via a local venv.

Fixes
- Serial status and pills reliability improved; permission errors now include clearer hints for device mapping.
- Various UI polish and accessibility tweaks on modals.

## 0.2.0-dev — 2025-09-05
- Added serial communication support for Arduino/RP2040 (with test commands).
- Implemented detection and configuration of USB serial devices via template.xml.
- Added logging improvements for serial and API errors in docker logs.
- Improved UI pills layout: combined Change Password, Logout, Theme toggle into a single dropdown menu.
- Made "Last update" a pill and added tooltips explaining refresh/disks.ini times.
- Themed modals for reset to defaults and refresh interval to match UI.
- Added Reset to Defaults button for overrides and fan curves.
- Integrated unsaved changes and validation error pills into configuration panel.

## 0.1.1-dev — 2025-09-04
- Added CSRF token meta tag in index.html for security.
- Enhanced styling for warning and error messages in index.html.
- Implemented a refresh interval modal in index.html to allow users to set polling frequency.
- Added functionality for changing user password and logging out in index.html.
- Created a reset to defaults modal for restoring fan curves and overrides in index.html.
- Added Login screen and Session Cookies
- Added enforcement and thresholds on PWM values

## 0.1.0-dev — 2025-09-04
- Introduce single release file with version + changelog.
- Wire Flask app to read version from this file.

