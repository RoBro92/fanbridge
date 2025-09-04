# fanbridge (scaffold)

Minimal Dockerised Flask app to verify the scaffold for a future Unraid service.
Endpoints:
- `/` – hello
- `/health` – healthcheck
- `/api/status` – demo JSON with fake drive temps and a computed PWM recommendation

## Run locally (requires Docker Desktop)
```bash
docker compose up --build
# open http://localhost:8080
```

## Changelog

- See `fanbridge/RELEASE.md` for the canonical version and changelog.
  - Update the `Version:` line there for each release.
  - Append a new changelog section per release.
