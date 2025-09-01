FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app (includes config.example.yml which lives at /app/config.example.yml)
COPY . /app/

ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_VERSION=0.0.1
# App reads /config/config.yml, but will fall back to /app/config.example.yml if missing
ENV FANBRIDGE_CONFIG=/config/config.yml

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["python","/app/app.py"]