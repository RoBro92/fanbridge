Version: 1.3.0

# Changelog

## 1.3.0 — 2026-07-14

Safety and reliability

- Moved temperature polling and PWM delivery into one dedicated control loop; HTTP health and status requests are now read-only.
- Reduced Gunicorn to one hardware-owning process and serialized transactions per physical serial controller.
- Changed the control policy to use the hottest assigned disk, with per-controller drive assignments.
- Added a 100% fail-safe for missing, corrupt, stale, or incomplete active-drive telemetry. Legitimately sleeping or unassigned disks use the configured idle fallback.
- Refreshes unchanged PWM commands within 30 seconds so the firmware's 60-second control lease remains valid.
- Added host compatibility and quarantine handling for the independent DIY firmware 2.3 safety protocol; DIY firmware changes are recorded separately in `fanbridge-link/CHANGELOG.md`.
- Added persistent full-UID controller binding, `DIY-RP2040-xxxx` recognition labels, and bounded pre-enrolment LED identification with DIY firmware source 2.5.0.
- Migrated legacy `official`/`fanbridge` labels to the existing one-channel DIY product while reserving schema-v2 `official` identity for the future six-channel board.
- Added a protected hardware-qualification gate to the DIY firmware release workflow and made watchdog failure hold the output at 100%.

Security and deployment

- Added atomic `0600` configuration, user, setup-token, and session-secret storage without destructive default rewrites.
- Added a one-time first-run setup token, password length enforcement, login throttling, same-origin redirect validation, session invalidation, JSON API authentication failures, and POST-only logout.
- Removed the legacy privileged in-app firmware updater and retained explicit disabled compatibility responses; updates remain a checksum-verified host operation until product-bound verification exists.
- Rebuilt the Docker image from an explicit file allowlist and a clean frontend build so local secrets, databases, logs, and stale bundles cannot be copied into releases.
- Updated Flask/Gunicorn dependencies, hardened CI, and converted the Unraid template to schema v2 with portable appdata and least-privilege device mappings.

Frontend and repository

- Replaced fabricated health, fan, power, log, update, and history values with real API data or explicit `Unknown`/`Unavailable` states.
- Reconnected settings, curves, assignments, password change, logs, history range, logout, and controller-scoped APIs; unsupported controls are disabled or removed.
- Fixed responsive layout, escaped telemetry interpolation, stored-DOM-XSS paths, session-expiry handling, and the missing login assets.
- Added backend control/security tests and frontend contract tests; removed tracked virtual environments and obsolete demo artifacts.
- Removed the duplicate dashboard poll-rate control; the browser refresh interval remains in Controller Settings. Official-controller status indicators are centred, while the smaller DIY set remains left-aligned.

Upgrade notes

- Existing single-port installations are migrated to the controller registry. Controller-scoped firmware/status calls now require `cid`.
- Configure Unraid's disk attribute polling to approximately 300 seconds; FanBridge treats data older than 600 seconds as unsafe by default.
- Firmware source is now 2.5.0, but no image is advertised in the manifest until a hardware-validated `fw-v2.5.0` release and SHA-256 digest are published.

## 1.2.3 — 2026-07-12
- Overhauled light and dark mode aesthetic, updating the dark theme to a high-contrast deep blue/purple and light theme to a soft grey.

## 1.2.2 — 2026-07-12
- Added safeguard to prevent UI hangs during firmware updates without proper Docker `/dev` mapping.
- Enhanced firmware STATUS parser to beautifully format new JSON diagnostics.

## 1.2.1 — 2026-07-12
- Split fanbridge-link and app release pipelines
- Fixed default manifest URL for monorepo structure

## 1.2.0 — 2026-07-11

Features
- **In-app RP2040 firmware updates**: Flash the latest firmware from the configured repo or upload a custom `.uf2` file directly from the FanBridge web UI (requires privileged container or CAP_SYS_ADMIN).
- **Local UF2 file upload**: New `POST /api/rp/flash_upload` endpoint accepts multipart file uploads for custom firmware builds.
- **Privileged/unprivileged UI split**: When the container is not privileged, flash buttons are replaced with a help link to the firmware update guide with Unraid terminal commands.
- **Firmware v2.0.0**: Bumped RP2040 firmware version for testing the update flow.

Infrastructure
- Added `util-linux` and `usbutils` packages to the Docker image for mount/umount and USB debugging support.
- Added optional `/dev/bus/usb` and `/dev/disk/by-label` mappings to the Unraid Docker template (advanced settings).
- Updated `fanbridge-link/README.md` with complete host-side update commands and troubleshooting guide.
- Updated privilege pill tooltips to explain firmware update implications.

## 1.1.6 — 2026-07-11

Enhancements
- Improved PWM & Temperature Graph UI: Made chart width uniform with config panels, added a timeframe selector (1h to 1m), and implemented smart data downsampling for longer time ranges to prevent browser overload.

## 1.1.5 — 2026-07-11

Fixes
- Fixed a javascript `ReferenceError` in the WebUI where polling functions attempted to access an undefined `sseData` variable, causing the top bar to incorrectly display a "serial error" even when the serial connection was healthy.

## 1.1.4 — 2026-07-11

Fixes
- Removed `/api/stream` SSE endpoint to fix Gunicorn timeouts and serial connection drops. Reverted to HTTP polling.
- Attached `RingBufferHandler` directly to the `fanbridge` logger to fix empty logs in the WebUI.
- Added serial TX/RX debug logs for easier diagnostics.
- Wrapped the PWM graph in a `<details>` tag to add a native collapsible UI arrow.
- Fixed the Drives table header background color not displaying correctly in dark mode.

## 1.1.3 — 2025-09-15

Fixes
- Ensure PWM auto‑apply continues when the WebUI is closed.
  - The Docker healthcheck (`/health`) now triggers the same status compute used by the UI poll, which performs auto‑apply when enabled.
  - Respects existing hysteresis and minimum‑interval safeguards; never fails the healthcheck on background errors.
  - If you want a faster cadence, tune the container healthcheck interval (e.g., `--health-interval=10s`).

Notes
- Auto‑apply remains opt‑in. Enable it in Settings or set `auto_apply: true` in `/config/config.yml`.

## 1.1.2 — 2025-09-13

Fixes
- Serial tab “Serial tools” pill could show “not opened” while PING/Test worked.
  - Tools endpoint now always performs a fast PING and treats a successful reply as connected, surfacing the active port and round‑trip time.
  - Ensures the UI reflects real connectivity even if a prior open‑probe failed.

Other
- Removed the extra Serial tools status pill; the main header serial status remains and avoids duplicate/confusing indicators.

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
