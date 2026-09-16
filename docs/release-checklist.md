# SPDX-License-Identifier: Apache-2.0
# Release checklist — publishing Wakey v0.1.0

Everything is committed and verified locally. The steps below are the
founder-gated actions that take the repository from local to public.

## 1. Create the public repository (no remote is configured by design)

```bash
gh repo create wakey-ai/wakey --public --source . --push
# or: git remote add origin <url> && git push -u origin main --tags
```

## 2. Repository settings

- Branch protection on `main`: require PR, require the `ci` checks
  (lint / typecheck / test / image).
- Enable: secret scanning + push protection, Dependabot alerts.
- Add repo secrets (only if Docker Hub publishing is wanted):
  `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`.

## 3. Tag the release

```bash
git tag -a v0.1.0 -m "Wakey 0.1.0 — first open-source release"
git push origin v0.1.0
```

The `release` workflow builds multi-arch images (SBOM + provenance) and
pushes to ghcr.io. Docker Hub publishing activates once its secrets are
set. PyPI is intentionally not wired yet.

## 4. Rotate credentials used during development

- GCP service account `wakey-glm@wakey-ai`: create a new key, replace
  `~/.config/wakey/gcp-service-account.json`, delete the old key
  (the current key transited chat during deployment).
- GitHub PAT in `.env` and the `WAKEY_WEBHOOK_SECRET` (rotate in the
  GitHub App settings + `.env` together).

## 5. Post-release

- Watch the first `release` workflow run end-to-end.
- `docker pull ghcr.io/wakey-ai/wakey:0.1.0` and smoke-test.
- Announce: README quickstart is the canonical copy — keep it working.
