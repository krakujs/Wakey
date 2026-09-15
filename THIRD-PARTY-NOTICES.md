# Third-Party Notices

Wakey depends on excellent open-source work. This file lists third-party
software bundled with or shipped in Wakey distributions, per the Apache-2.0
requirement to retain attribution notices (LICENSE §4(c)-(d)).

**Policy:** at every release, a CI job regenerates the full list from the
installed dependency metadata (license + copyright of every package in the
image), and the supply-chain review (engineering-standards §2) rejects any
dependency whose license is incompatible with Apache-2.0 (GPL-1/2/3,
AGPL, SSPL, or "no license"). The table below is the **planned baseline** at
planning stage — entries are verified automatically once the dependency set
exists (task E1-T1/E1-T4).

| Component | License | Use |
|---|---|---|
| FastAPI / Starlette | MIT / BSD-3-Clause | HTTP server & ingest API |
| Uvicorn | BSD-3-Clause | ASGI server |
| Pydantic | MIT | models & config validation |
| PyYAML | MIT | `wakey.yml` parsing |
| httpx | BSD-3-Clause | forge/LLM API clients |
| orjson | MIT (or Apache-2.0) | hot-path JSON |
| Inter (font) | SIL OFL 1.1 | UI typeface (self-hosted) |
| JetBrains Mono (font) | SIL OFL 1.1 | code/log typeface (self-hosted) |

Fonts are embedded in images/dashboard assets under OFL, which permits
bundling; reserve names are not used as Wakey's own trademarks.

Trademark note: GitHub®, GitLab®, Docker®, and all other third-party product
names are trademarks of their respective owners — used in Wakey's docs only
to describe compatibility, which constitutes nominative fair use and implies
no affiliation or endorsement.
