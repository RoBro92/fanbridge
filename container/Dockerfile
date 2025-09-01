FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Provide a default config inside the image
RUN mkdir -p /config
COPY config.example.yml /config/config.yml

COPY . /app/

ENV PYTHONUNBUFFERED=1
ENV FANBRIDGE_VERSION=0.0.1
# Optional: allow overriding config path later
ENV FANBRIDGE_CONFIG=/config/config.yml

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["python","/app/app.py"]