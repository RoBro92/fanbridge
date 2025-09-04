FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl smartmontools \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# OCI image labels (no embedded version; app reads from RELEASE.md at runtime)
LABEL org.opencontainers.image.title="fanbridge" \
      org.opencontainers.image.description="FanBridge – Unraid drive temp → PWM fan control bridge" \
      org.opencontainers.image.source="https://github.com/RoBro92/fanbridge" \
      org.opencontainers.image.licenses="MIT"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/

ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_CONFIG=/config/config.yml
ENV GUNICORN_WORKERS=2

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["bash", "-lc", "gunicorn -w ${GUNICORN_WORKERS:-2} -b 0.0.0.0:8080 app:app"]
