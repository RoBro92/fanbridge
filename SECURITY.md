# Security policy

## Supported versions

Security fixes are made on the latest released FanBridge version. FanBridge 1.3.x is the currently supported application line; older application and DIY firmware versions should be upgraded before reporting an issue that may already be resolved.

## Reporting a vulnerability

Please use the repository's **Security → Report a vulnerability** form to open a private GitHub Security Advisory. Do not disclose credentials, setup tokens, user files, configuration archives, device identifiers, or an exploitable issue in a public ticket. Include the affected version, deployment type, reproduction steps, and impact when possible.

If private reporting is unavailable, contact the maintainers before publishing technical details. We will acknowledge a complete report, investigate it, and coordinate a fix and disclosure timeline based on severity.

## Deployment boundary

FanBridge controls physical cooling hardware. Keep its Web UI on a trusted network or behind an authenticated HTTPS reverse proxy and pass through only explicitly selected serial devices. In-container firmware flashing is hard-disabled; use the documented checksum-verified host workflow. Treat `/config` as sensitive because it contains the user database, session secret, setup token, configuration, and history.

Do not attach configuration archives, database files, setup tokens, controller UIDs, serial numbers, or raw logs to a public issue. Redact host paths and hardware identifiers from diagnostic excerpts unless the maintainer handling a private report specifically needs them.
