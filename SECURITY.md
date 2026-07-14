# Security policy

## Supported versions

Security fixes are made on the latest released FanBridge version. Update to the newest stable container tag before reporting an issue against an older deployment.

## Reporting a vulnerability

Please use the repository's **Security → Report a vulnerability** form to open a private GitHub Security Advisory. Do not disclose credentials, setup tokens, user files, configuration archives, device identifiers, or an exploitable issue in a public ticket. Include the affected version, deployment type, reproduction steps, and impact when possible.

If private reporting is unavailable, contact the maintainers before publishing technical details. We will acknowledge a complete report, investigate it, and coordinate a fix and disclosure timeline based on severity.

## Deployment boundary

FanBridge controls physical cooling hardware. Keep its Web UI on a trusted network or behind an authenticated HTTPS reverse proxy and pass through only explicitly selected serial devices. In-container firmware flashing is hard-disabled; use the documented checksum-verified host workflow. Treat `/config` as sensitive because it contains the user database, session secret, setup token, configuration, and history.
