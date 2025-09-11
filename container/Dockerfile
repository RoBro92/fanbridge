## FanBridge runtime image
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

LABEL org.opencontainers.image.title="fanbridge" \
      org.opencontainers.image.description="FanBridge – Unraid drive temp → PWM fan control bridge" \
      org.opencontainers.image.source="https://github.com/RoBro92/fanbridge" \
      org.opencontainers.image.licenses="MIT"

COPY container/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY container/ /app/
# Include version/changelog for runtime version display
COPY RELEASE.md /app/RELEASE.md

ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_CONFIG=/config/config.yml
ENV GUNICORN_WORKERS=2
ENV GUNICORN_TIMEOUT=30

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["bash", "-lc", "gunicorn -w ${GUNICORN_WORKERS:-2} --timeout ${GUNICORN_TIMEOUT:-30} -b 0.0.0.0:8080 app:app"]
