# Distribution & Quickstart (Docker) — public install design

> Requirement (founder, 2026-09-15): anyone should be able to **pull Wakey from our Docker repository and start using it almost immediately** — with the YAML ready for the GitHub README. Status: content prepared now (planning mode); it becomes the live README quickstart at v1.0.0. Registry org `wakey-ai` / image name are placeholders pending the branding check (STATE open decisions).

## 1. Registries & tags

- **Primary**: `ghcr.io/wakey-ai/wakey` (GitHub Container Registry — pairs with the repo, OIDC-authenticated publishing, no extra secrets).
- **Mirror**: `docker.io/wakeyai/wakey` (Docker Hub — the "just docker pull" habit). Both pushed by the same release workflow (E1-T4).
- **Tags**: `X.Y.Z` (immutable releases) · `X.Y` (latest patch of a minor) · `1` (latest stable major — what the quickstart pins) · `sha-<commit>` (CI builds). No `latest` in quickstart: predictable upgrades beat shiny.
- **Image contents**: `wakeyd` (server) + `wakey` CLI + `wakey-agent` sidecar entrypoints in one image; runs as **non-root**; `HEALTHCHECK` → `/healthz`; size ≤200MB (NFR-6); multi-arch (amd64 + arm64 — the Raspberry-Pi story, NFR-8).
- **Supply chain** (eng-standards §4): SBOM + provenance attestation attached; cosign signatures (P2); the compose file in the README is byte-identical to `/docker-compose.yml` in the repo — **CI boots the README quickstart on every release** (drift test: docs can't promise what we don't ship).

## 2. The GitHub README quickstart (final copy, ready to paste)

### 2.1 One-liner (60-second path)

```bash
docker run -d --name wakey \
  -p 8477:8477 \
  -v wakey-data:/var/lib/wakey \
  -e WAKEY_MASTER_KEY=$(openssl rand -hex 32) \
  --restart unless-stopped \
  ghcr.io/wakey-ai/wakey:1

# then: docker logs wakey   ← shows the dashboard URL + one-time setup token
open http://localhost:8477
```

### 2.2 `docker-compose.yml` (the recommended path — this exact file lives in the repo root)

```yaml
# docker-compose.yml — Wakey, self-hosted AI SRE
services:
  wakey:
    image: ghcr.io/wakey-ai/wakey:1
    container_name: wakey
    restart: unless-stopped
    ports:
      - "8477:8477"                     # dashboard + ingest (WF-12 default)
    environment:
      WAKEY_MASTER_KEY: ${WAKEY_MASTER_KEY:?set WAKEY_MASTER_KEY in .env}
      # any AI provider — Wakey routes tasks per your ai: profiles (WF-14)
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-}   # optional: run AI fully locally
    volumes:
      - wakey-data:/var/lib/wakey       # all state stays on your machine
    healthcheck:
      test: ["CMD", "wakey", "healthcheck"]
      interval: 30s
      timeout: 5s
      retries: 3

  # optional — Postgres for bigger estates; SQLite by default (NFR-5)
  postgres:
    image: postgres:16-alpine
    profiles: ["postgres"]
    restart: unless-stopped
    environment:
      POSTGRES_DB: wakey
      POSTGRES_USER: wakey
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
    volumes:
      - pg-data:/var/lib/postgresql/data

volumes:
  wakey-data:
  pg-data:
```

### 2.3 `.env` (next to the compose file)

```bash
# encryption key for stored credentials — generate once, keep safe
WAKEY_MASTER_KEY=run-openssl-rand-hex-32-and-paste-here

# bring your own AI — any provider, any mix (WF-14 §B); local models welcome
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
OLLAMA_BASE_URL=              # e.g. http://host.docker.internal:11434/v1

# only needed with --profile postgres
# POSTGRES_PASSWORD=
```

### 2.4 The first five minutes (what the README promises)

1. `docker compose up -d` → `docker logs wakey` shows: dashboard URL + one-time setup token.
2. Open `http://localhost:8477`, paste the token → setup wizard.
3. **Connect GitHub** (self-owned GitHub App, 2 clicks) — GitLab & other forges via the same wizard as they ship.
4. **Add a service**: pick a repo → `wakey.yml` defaults applied → copy the ingest snippet into your log pipeline (GCP recipe / generic webhook / docker sidecar).
5. `wakey doctor` (button or CLI) → synthetic error → your first ticket appears. Watching has started; autonomy is `triage` until you say otherwise.

### 2.5 Standing promises printed under the quickstart

- Your logs and state stay on your machine; only redacted prompts go to the AI provider *you* configured (or none, with local models).
- Wakey never merges — every fix is a draft PR for human review.
- Runs quietly: idle ≤120MB, ~0% CPU; budget numbers enforced in CI (NFR-6).

## 3. Wiring & rules

- **ONB-1** (one-command install) is the feature; **E1-T3** builds the image/compose; **E1-T4** builds dual-registry publishing — E1-T4's AC extended to include Docker Hub + the README-drift test.
- The quickstart is **release-gated**: `make gate-m3` and the release checklist include "README quickstart boots green on a clean machine" (fresh-install drill already in execution-plan §5).
- Naming: registry org/image is placeholder until the branding/trademark check (docs/02 decision list) — rename is one variable in the release workflow.

## 4. Acceptance criteria (distribution)

1. Fresh machine (docker only): compose quickstart → wizard → doctor green, ≤10 minutes, no other installs (gate M0 manual AC; compose smoke automated in CI).
2. Release workflow publishes ghcr.io **and** Docker Hub with `X.Y.Z`/`X.Y`/`1`/`sha-*` tags, SBOM + provenance; images run as non-root; multi-arch manifest (amd64/arm64).
3. README compose block is byte-identical to repo `docker-compose.yml` (CI drift test).
4. `docker logs wakey` shows the WF-12 banner (URL + setup token) on first boot; setup token single-use (WF-12 §1).
5. Postgres profile: enabling it migrates/uses Postgres per OPS-5; disabling returns to SQLite path cleanly documented.
