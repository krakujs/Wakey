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
    CMD ["python", "-c", "import os, urllib.request; port = os.environ.get('WAKEY_PORT') or os.environ.get('PORT') or '8477'; urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=4)"]

# 0.0.0.0 inside the container is standard and required for the published
# port to be reachable; the container network itself provides isolation
# (the loopback-by-default protection applies to local, non-container runs).
CMD ["python", "-m", "wakey", "serve", "--host", "0.0.0.0"]
