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