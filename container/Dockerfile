## Build the browser assets from the committed lockfile.  Generated files in
## container/static/dist are intentionally not trusted as release inputs.
FROM node:26-bookworm-slim@sha256:2d49d876e96237d76de412761cf05dbfe5aee325cc4406a4d41d5824c5bb8beb AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --ignore-scripts --prefix frontend
COPY frontend/index.html frontend/vite.config.js ./frontend/
COPY frontend/public/ ./frontend/public/
COPY frontend/src/ ./frontend/src/
RUN mkdir -p container/templates \
 && npm run build --prefix frontend


## FanBridge runtime image
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

LABEL org.opencontainers.image.title="fanbridge" \
      org.opencontainers.image.description="FanBridge - Unraid drive temperature to PWM fan-control bridge" \
      org.opencontainers.image.source="https://github.com/RoBroLabs/fanbridge" \
      org.opencontainers.image.licenses="MIT"

COPY container/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

# Runtime allowlist: do not replace this with `COPY container/ /app/`.  Local
# users, session/setup secrets, databases, backups, logs, demo controllers, and
# development configuration deliberately cannot enter the image.
COPY container/app.py ./app.py
COPY container/api/ ./api/
COPY container/core/ ./core/
COPY container/services/ ./services/
COPY container/templates/base.html container/templates/login.html ./templates/
COPY container/static/empty-state-dark.jpg container/static/empty-state-light.jpg ./static/
COPY container/static/fanbridge.png ./static/fanbridge.png
COPY --from=frontend-build /build/container/templates/index.html ./templates/index.html
COPY --from=frontend-build /build/container/static/dist/ ./static/dist/
COPY RELEASE.md ./RELEASE.md
COPY LICENSE /licenses/LICENSE

ENV PYTHONUNBUFFERED=1 \
    FANBRIDGE_CONFIG=/config/config.yml \
    FANBRIDGE_DISKS_STALE_WARN_SEC=600 \
    GUNICORN_THREADS=4 \
    GUNICORN_TIMEOUT=30

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"]

# The application keeps controller sessions and scheduling state in process.
# One threaded process serves concurrent UI requests without duplicating the
# process-local hardware owner.
CMD ["sh", "-c", "exec gunicorn --workers 1 --worker-class gthread --threads \"${GUNICORN_THREADS:-4}\" --timeout \"${GUNICORN_TIMEOUT:-30}\" --bind 0.0.0.0:8080 app:app"]
