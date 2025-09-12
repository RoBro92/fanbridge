Version: 1.1.2

# Changelog

## 1.1.2 — 2025-09-13

Fixes
- Serial tab “Serial tools” pill could show “not opened” while PING/Test worked.
  - Tools endpoint now always performs a fast PING and treats a successful reply as connected, surfacing the active port and round‑trip time.
  - Ensures the UI reflects real connectivity even if a prior open‑probe failed.

## 1.1.1 — 2025-09-13

Fixes
- Restore Logs page functionality after modularization refactor.
  - Ensure API blueprints are registered when running under Gunicorn (app:app), so `/api/logs*` routes are present.
  - Make logging ring buffer resilient to server reconfiguration and re‑attach handler at startup.
  - Logs API now returns `last_id` and current `level`, and accepts `format=` as well as `fmt` for downloads.
  - UI uses `last_id` to advance cursor and shows runtime level correctly.
 
Other
- Footer order updated and Support link points to the Unraid forum thread.

## 1.1.0 — 2025-09-11

Highlights
- Modularized codebase for maintainability: extracted core logging/metrics, services for disks/serial, and API blueprints.
- Added app factory (`create_app`) while retaining `app:app` for Gunicorn compatibility.
- Production “sim” mode removed (local dev only). Container still runs without Unraid mounts and without a serial device.
- Sensible defaults in Docker: preferred serial port defaults to `/dev/ttyACM0` (RP2040). Missing device no longer blocks startup; an error is logged and surfaced in the UI.

API & Backend
- New blueprints: `/api/serial/*`, `/api/app/version`, `/api/metrics`, `/api/logs*` organized by responsibility.
- Logs API refactored to use a ring buffer handler in `core.logging_setup`.
- Version and release checks moved to `core.appver` with a minimal HTTP helper.
- Metrics implemented in `core.metrics` and exposed at `/api/metrics` (Prometheus text).

Templates & Docs
- Unraid template clarifies serial device field is intentionally left blank by default; recommended device is `/dev/ttyACM0` (RP2040). Preferred port env defaults to `/dev/ttyACM0`.
- README simplified for production with a clear project overview and a concise project structure outline.

Container & Build
- Removed unused `smartmontools` from the image to keep it lean.
- Comments and hints tightened for production clarity.

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
