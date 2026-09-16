# Wakey server image — non-root, slim, ≤200MB target (NFR-6).
# Multi-arch (amd64+arm64) builds come with the E1-T4 release workflow.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir ".[secrets,postgres]" pytest \
    && groupadd --system wakey \
    && useradd --system --gid wakey wakey \
    && mkdir -p /var/lib/wakey \
    && chown -R wakey:wakey /var/lib/wakey

USER wakey

ENV WAKEY_DATA_DIR=/var/lib/wakey \
    WAKEY_PORT=8477
EXPOSE 8477

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8477/healthz', timeout=4)"]

CMD ["python", "-m", "wakey", "serve"]
