FROM python:3.12-slim

# Version now set from environment at runtime
ENV FANBRIDGE_VERSION=${FANBRIDGE_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl smartmontools \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Include version defaults so the app shows a version out of the box
COPY .env /app/.env

# Install deps first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . /app/

ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_CONFIG=/config/config.yml

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["gunicorn","-w","2","-b","0.0.0.0:8080","app:app"]