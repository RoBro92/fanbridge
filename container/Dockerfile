FROM python:3.12-slim

ARG VERSION=dev
ENV FANBRIDGE_VERSION=${VERSION}
LABEL org.opencontainers.image.version="${VERSION}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl smartmontools \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . /app/

ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_VERSION=0.1.0
ENV FANBRIDGE_CONFIG=/config/config.yml

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["gunicorn","-w","2","-b","0.0.0.0:8080","app:app"]