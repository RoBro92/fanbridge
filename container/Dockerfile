FROM python:3.12-slim

# Base OS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl smartmontools \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# -------- Version handling ----------
# Inject version at build time via ARG (fallback to "dev").
ARG FANBRIDGE_VERSION=dev
ENV FANBRIDGE_VERSION=${FANBRIDGE_VERSION}
# ------------------------------------

# Python deps (copied first for better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . /app/

# Runtime env
ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_CONFIG=/config/config.yml

# Network / health
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

# Prod server
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "app:app"]
