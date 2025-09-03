FROM python:3.12-slim

# Base OS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl smartmontools \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# -------- Version handling ----------
# We want the app to show a version out-of-the-box.
# Build context is the repo root, so this copies the *root* .env into the image.
# Your app.py already loads /app/.env via python-dotenv.
COPY .env /app/.env
# ------------------------------------

# Python deps (copied first for better layer caching)
COPY container/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY container/ /app/

# Runtime env
ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_CONFIG=/config/config.yml

# Network / health
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

# Prod server
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "app:app"]