# FanBridge engineering handover and repository audit

Date: 2026-07-13
Merged to `main`: 2026-07-14 in `b7e8c16`

This is the point-in-time engineering handover for the 1.3.0 hardening work. The temporary review branch has been removed after merge. Use the root `README.md`, `RELEASE.md`, and target-specific firmware guide for current operating instructions.

## Executive assessment

FanBridge is a monorepo containing two separate deliverables and a third, future target:

| Deliverable | Current state | Release boundary |
|---|---|---|
| Unraid/Docker application | Repaired and automated-test ready; real Unraid and hardware qualification remains mandatory | Application tags `v*`, Docker image |
| One-channel DIY RP2040-Zero firmware | Source updated to 2.5.0 and builds successfully; physical qualification has not occurred | DIY tags `fw-v*`, `fanbridge-rp2040-*.uf2` |
| Six-channel custom RP2040 PCB and firmware | Requirements/concept only; no production schematic, PCB, firmware target, channel protocol, or qualified image exists | A new target, identity, artifact, and version stream must be defined later |

The six-channel engineering hold applies only to the custom product. It must not block the existing DIY product. Conversely, the DIY global one-number PWM protocol must never be treated as a six-channel protocol. The host can recognise the reserved custom-board identity but deliberately refuses to actuate it until a channel-specific adapter exists.

The repaired software is substantially safer than the inherited tree, but it is not yet reasonable to call the complete system production-qualified. The remaining release blockers require a physical DIY circuit and a real Unraid server; source inspection and simulated serial tests cannot prove PWM electrical behaviour, USB lifecycle behaviour, disk-state timing, or actual fan response.

## Intended system behaviour

The application should implement this control chain:

1. Unraid writes current array/device state to `/var/local/emhttp/disks.ini`.
2. The container receives `/var/local/emhttp` as a read-only directory at `/unraid`; mapping the directory permits Unraid's atomic file replacement to remain visible.
3. A single dedicated control thread parses and validates `disks.ini`, checks its modification age, classifies disks, and distinguishes active missing temperatures from legitimate spun-down disks.
4. Each disk is assigned to one specific controller or left unassigned. There is no global/all-controllers assignment; deleted or unknown controller assignments are removed rather than silently redirected.
5. Each controller's demand is based on the hottest assigned HDD/SSD, its applicable curve, and the independent single-drive override.
6. Missing, malformed, stale, incomplete, or contradictory active-drive telemetry demands 100%. All assigned drives legitimately asleep (or no drives assigned) may use the configured idle fallback.
7. Automatic transmission is opt-in. The host periodically refreshes a verified setpoint even when it is unchanged.
8. The DIY firmware accepts only protocol-2, one-channel commands, boots at 100%, returns an exact acknowledgement, expires its control lease to 100% after 60 seconds, and uses a 4-second hardware watchdog. A failed watchdog start prevents commands below 100%.
9. Disabling automatic output, deleting/reconfiguring a controller, losing telemetry, or losing host communication must converge on maximum cooling.

This design intentionally layers host fail-safe policy, a firmware command lease, the MCU watchdog, and an external transistor bias. None is a substitute for the others.

## Important findings resolved in this branch

| Severity | Inherited/rebuild fault | Resolution |
|---|---|---|
| Critical safety | Valid YAML such as `auto_apply: "false"` was truthy and could authorise hardware output; `controllers: null` could crash startup | Complete schema normalisation now accepts only a literal boolean for actuation, bounds every safety/control value, drops unknown keys, and safely handles wrong YAML types |
| Critical safety | Published DIY 2.1/2.2 firmware booted at 0% and renewed its unsafe lease for diagnostic/unknown commands | The host identifies the legacy version, sends and verifies one 100% command, then quarantines it without repeated commands; 2.3 boots at 100% and only numeric setpoints renew the lease |
| High safety | The old policy could use average temperatures and could continue with stale/missing telemetry | Control uses the hottest assigned disk; stale/missing/corrupt source data and active missing temperatures force 100% |
| High safety | UI/health could report an old successful snapshot as current after the control worker failed | Stalled/dead/error control state invalidates status and readiness with HTTP 503; source age is recalculated rather than frozen |
| High compatibility | Old one-channel Pico records used `official` or `fanbridge`, but the rebuild reused `official` for the future six-channel product | Pre-schema-2 labels migrate to `diy`; schema-2 `official` remains reserved for the future board |
| High safety | Controller replacement/reconnect was not identity-checked and any non-empty response could count as PWM success | Reconnect identity is verified, protocol-2 DIY controllers bind by full persistent UID, custom identity is rejected, and PWM requires the exact requested acknowledgement |
| High safety | Disabling/deleting/reconfiguring a controller or gracefully stopping the container could leave its prior low lease active | A best-effort verified 100% safe-stop runs first/on graceful exit; removal clears that controller's drive assignments, while the firmware lease covers crashes/SIGKILL |
| High reliability | HTTP requests and health checks performed hardware control, and multiple Gunicorn processes could own the same serial board | One dedicated control thread owns scheduling; HTTP is read-only; production uses one `gthread` Gunicorn process; physical serial locks cover aliases/process overlap |
| High security | The inherited privileged updater downloaded/mounted/copied UF2 files inside the container and accepted runtime update configuration | The implementation was removed. Compatibility routes return 403, the deployment grants no mount capability or block mappings, and manual checksum-verified flashing is the only supported flow |
| High security | First-run administrator creation, session persistence, CSRF, login throttling, redirects, and API authentication had weak or inconsistent edges | One-time setup token, atomic private secret/user files, password rules, CSRF, bounded throttling, session-version invalidation, same-origin redirects, POST logout, JSON 401s, and response security headers are enforced |
| Medium reliability | Controller creation persisted even if serial registration failed; canonical aliases could duplicate a physical port | Registration precedes persistence, failures roll back, and canonical device paths are de-duplicated |
| Medium safety | Unraid `spundown=1` could win over a live sysfs state during wake-up and suppress cooling | The disagreement is treated as active with missing telemetry, which forces fail-safe until a fresh temperature arrives |
| Medium integrity | Settings and curves were saved in two requests, allowing partial safety configuration | `/api/config` validates the complete settings/curve candidate and commits it atomically |
| Medium security | Raw serial/manual PWM and hardware-heavy GET endpoints could be abused by a logged-in browser | Raw/manual commands require explicit maintenance mode; hardware GETs are rate-limited; firmware commands are denied; serial operations are physically locked |
| Medium data quality | Demo values and fabricated fan/power/history/update states made the UI look healthy | Production demo sources were removed; the UI renders real API values or explicit unknown/stale/unavailable/fail-safe states |
| Medium safety | Explicit simulation mode could still transmit temperature-derived PWM if automatic output was enabled | Simulation is now non-actuating and safe-stops connected hardware at 100%; the UI continues to label the synthetic source |
| Medium supply chain | Images/actions/platform/dependencies were loose and a tracked virtual environment dominated the repository | Container base images and Actions are digest/SHA pinned, PlatformIO is pinned, lockfiles are used, dependency audits run, and tracked virtual environments/demo artifacts were removed |

## Product and protocol boundaries

### DIY one-channel product

- Identity: `FANBRIDGE_DIY protocol=2 board=rp2040-zero channels=1 uid=<16-hex-character-id>`.
- Human recognition label: `DIY-RP2040-xxxx`; the four-character suffix is display-only and the complete UID remains the settings binding key.
- Wire command: one decimal percentage `0..100` followed by newline.
- Required response: `Set fan to N%`, exactly matching the request.
- Non-control commands (`ID?`, `VERSION`, `PING`, `STATUS`) do not renew the 60-second lease.
- `IDENTIFY` flashes the onboard GPIO16 WS2812 for ten seconds without renewing the control lease; the host exposes it only for a verified, unregistered DIY controller.
- `RPM?` truthfully reports unsupported; the DIY circuit has no tach input.
- Pico GP15 drives an NPN open-collector interface. The external base-to-emitter resistor is a mandatory reset/BOOTSEL safety component.
- Firmware 2.5.0 is source/build ready, not hardware-qualified. The manifest remains empty until qualification and a checksummed release exist.

### Future six-channel custom product

The custom target cannot be completed by changing a channel-count constant. It needs, at minimum:

- an approved schematic/PCB and six independently verified PWM/tach electrical channels;
- a unique production identity and a different PlatformIO target;
- channel-addressed, bounded commands and acknowledgements rather than the DIY global percentage;
- per-channel tach/RPM validity, zero-timeout, fault and watchdog telemetry;
- an explicit rule for mapping drive/controller demand onto six outputs;
- aggregate timing analysis so one bad channel/device cannot starve control leases;
- a separate HIL matrix, artifact name, tag convention, manifest namespace and update compatibility check;
- host tests proving that custom images cannot reach DIY boards and DIY images cannot reach custom boards.

Until those exist, custom devices are visible as planned hardware but cannot be added or actuated by the current host.

## Unraid integration assessment

The current `disks.ini` integration is the correct compatibility path for older and current Unraid installations. Official Unraid documentation confirms that a spun-down disk does not expose a temperature in the WebGUI, so an absent temperature must be interpreted together with spin state rather than automatically treated as a sensor failure. Directory-level read-only mounting and AppData persistence also match Unraid's documented container model.

Unraid 7.2 and later include a GraphQL API. It is a useful future source adapter, especially for typed disk identity/telemetry, but replacing `disks.ini` now would drop older-version compatibility and require API-key lifecycle/network handling. If added, it should be a second adapter with the same normalized source-quality contract, not mixed directly into PWM policy.

The real-host test must cover array data disks, parity, pools/NVMe, spun-down HDDs, wake transitions, empty slots, boot/flash media, disabled/missing disks, disk replacement, stale polling, and the configured `poll_attributes` interval. UI polling does not make Unraid's underlying temperature sample fresher.

References:

- [Unraid container mappings and least-privilege guidance](https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/)
- [Unraid array health and spun-down temperature behaviour](https://docs.unraid.net/unraid-os/using-unraid-to/manage-storage/array/array-health-and-maintenance/)
- [Unraid Community Applications lifecycle](https://docs.unraid.net/unraid-os/manual/applications/)
- [Unraid 7.2 built-in API announcement](https://docs.unraid.net/unraid-os/release-notes/7.2.0/)

## Security posture and remaining weaknesses

The current deployment is deliberately unprivileged at the Docker boundary: `no-new-privileges`, all capabilities dropped, a read-only root filesystem in Compose, a small no-exec `/tmp`, read-only Unraid telemetry, and only explicitly selected serial devices. The build copies an explicit runtime allowlist and rebuilds the frontend, preventing local secrets/databases/demo files/stale bundles from entering the image.

Remaining concerns:

1. The Web UI terminates plain HTTP itself. Keep it on a trusted LAN or place it behind an authenticated HTTPS reverse proxy; enable secure cookies only on an HTTPS path.
2. The process currently runs as root inside the capability-dropped container because Unraid serial-device group IDs vary. Moving to non-root is desirable, but requires a documented `group-add`/device-permission strategy that works across Unraid hosts.
3. Metrics use the normal browser session and have no dedicated read-only token. Do not expose `/api/metrics` publicly merely to satisfy a scraper.
4. Serial work is bounded and locked but not yet handled by a dedicated queue. Many controllers or a repeatedly slow device can increase cycle latency; the firmware lease safely falls back to 100%, but control quality can degrade. Add a deadline-aware serial-owner queue before claiming a large controller count.
5. History is aggregate, not per-controller/per-drive. It is suitable for broad trend display but cannot yet prove which controller/channel produced a historical cooling action.
6. `disks.ini` exposes a file modification time, not an authoritative timestamp for each individual temperature sample. FanBridge can reject an old file, but it cannot prove that every value in a recently replaced file was freshly sampled. Treat the stale threshold as a fail-safe ceiling, not as a real-time sensor guarantee.
7. Request throttling intentionally keys the direct peer address and does not trust forwarded headers. Behind a reverse proxy this means clients share the proxy's bucket unless a specific trusted-proxy configuration is implemented; this fails safely but can cause nuisance throttling.
8. Static scanning reports no high-severity issue. Remaining low findings are mostly best-effort logging/diagnostic exception guards; maintainers should narrow broad exceptions as modules are refactored.
9. `container/app.py` remains a roughly 2,000-line inherited entry module with a substantial cosmetic PEP 8 backlog and some intentionally defensive broad exception guards. Correctness/security lint is enforced, but a staged module split and formatter pass should precede another large feature rebuild rather than being mixed into the current safety repair.

## Repository hygiene and historical exposure

The active tree removes the tracked virtual environment, dummy controller/data generators, obsolete UI copies, screenshots/demo assets, a GPS-bearing phone photograph, stale bundles, and runtime example data that no longer matched the schema. Synthetic fixtures remain only inside tests.

Normal deletion does not remove history. Earlier commits contain a Flask session key, an administrator password hash, runtime history databases, environment/config files, and the GPS-bearing photograph. Before making the repository public or sharing it more widely:

1. rotate every deployed session secret and any reused administrator password;
2. coordinate a push freeze and backup;
3. use `git filter-repo` in a fresh mirror to remove the sensitive/runtime/demo paths from all branches and tags;
4. force-push the rewritten refs and require collaborators to reclone;
5. remember that forks, old clones and caches may retain old objects.

Ignored local AppData, databases, logs and tool environments were deliberately not deleted. Local configuration/users/history files should remain mode `0600`.

## Verification completed

Automated checks at merge include:

- 75 Python safety, security, control, migration, and frontend-contract tests;
- Python compile and correctness lint checks with no undefined names or stale imports;
- a successful pinned PlatformIO Pico build;
- Python and npm dependency audits with no reported vulnerabilities;
- Bandit/static review with zero high-severity findings, plus a Trivy image scan reporting zero fixed HIGH/CRITICAL vulnerabilities and no embedded secrets;
- workflow syntax/action validation, XML/YAML/Compose parsing, Docker build/smoke, read-only/capability checks, and image allowlist checks as final packaging gates.

These checks prove source contracts and packaging, not physical cooling.

## Release gates and recommended order

### P0 — before a DIY 2.5.0 firmware release

1. Execute and record every DIY HIL case in `fanbridge-link/README.md`, including scoped PWM polarity/frequency, full reset/BOOTSEL interval, watchdog reset, 60-second lease expiry, malformed commands, real fan start/run behaviour and USB re-enumeration.
2. Test an upgraded 2.5 board and a legacy 2.1/2.2 board against the host. Confirm the legacy board receives one verified 100% command and remains quarantined. Verify the onboard LED identify command selects only the requested unregistered RP2040-Zero and does not renew the fan-control lease.
3. Set protected repository variable `DIY_FIRMWARE_HIL_APPROVED_VERSION=2.5.0` only after review. The release workflow otherwise refuses publication.
4. Publish the UF2 and SHA-256 companion, verify the release download independently, then add the exact approved artifact/digest to the manifest.

### P0 — before an application production release

1. Run the container on a real supported Unraid version with live `disks.ini`, stable `/dev/serial/by-id` mappings and at least one qualified DIY controller.
2. Exercise missing/stale/corrupt source, spin-up disagreement, sensor loss, controller unplug/replug, container restart/stop, automatic-control disable, controller deletion and AppData migration.
3. Confirm the fan goes to the safe physical speed—not merely that the software reports 100%—for every fault.
4. Rotate credentials and decide whether Git history must be rewritten before distribution.

### P1 — after the safe DIY path is released

1. Re-submit/migrate the Community Applications listing and test install, update, previous-app restore and template device mappings on Unraid.
2. Add deadline-aware serial scheduling and controller-count/load tests.
3. Add per-controller/per-drive history and an authenticated read-only metrics credential if external monitoring is a goal.
4. Consider a typed Unraid 7.2+ API source adapter while retaining the `disks.ini` fallback.
5. Split authentication/configuration/control lifecycle out of `container/app.py`, narrow defensive exception scopes, then enable full-project formatting/style enforcement.

### Separate custom-PCB programme

Complete the hardware requirements and review, then add a separate custom firmware directory/build/release lane and a channel-specific host adapter. Do not reuse the DIY UF2, global command semantics, artifact name, or HIL approval.
