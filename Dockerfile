# Wakey server image — non-root, slim, ≤200MB target (NFR-6).
# Multi-arch (amd64+arm64) builds come with the E1-T4 release workflow.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir . \
    && groupadd --system wakey \
    && useradd --system --gid wakey wakey \
    && mkdir -p /var/lib/wakey \
    && chown -R wakey:wakey /var/lib/wakey

USER wakey

ENV WAKEY_DATA_DIR=/var/lib/wakey
EXPOSE 8477

# Pre-M0: reports version and exits; becomes the real server CMD at E2-T4.
CMD ["python", "-m", "wakey"]
