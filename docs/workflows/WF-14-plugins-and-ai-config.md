# WF-14 — Plugin System & AI Configuration (any plugin, any AI tool)

> Behavioral contract for Wakey's extensibility layer. Features: EXT-6..10 (with ING-10, RCA-7, SEC-3, EXT-9/WPM-1) · Tasks: E16-*. Requirement (founder, 2026-09-15): **a user can add any plugin they want and configure any AI tool with Wakey.** Two halves, one spec: the plugin platform (A) and bring-your-own-AI (B).

## Principles

1. **Extensible, not forkable.** Users extend Wakey through declared extension points — never by patching core. Core stays upgradeable under any plugin load.
2. **Capability-scoped by default.** A plugin gets nothing it didn't declare and the user didn't grant. No secret access by default; network egress is allowlisted.
3. **Crash-isolated.** A broken plugin degrades only itself — the pipeline (ingest → fingerprint → ticket) must survive any plugin misbehaving (WF-11 ladder unaffected).
4. **AI is config, not code.** Any AI provider/model/tool binds to Wakey's tasks through named profiles. Swapping vendors is a config change; the model router (RCA-7) is the only component that speaks vendor protocols.
5. **Everything auditable.** Plugin and AI-profile changes are config mutations: validated, versioned, audited (SEC-5), and visible in the GUI/CLI.

## A. Plugin platform

### A1. Manifest & shape
Every plugin ships a `wakey-plugin.yml`:

```yaml
name: jira-bridge
version: 1.2.0
api: "^1.0"                      # wakey plugin-API compatibility range
capabilities: [notifier, enricher]        # which extension points it uses (A3)
entrypoint: server.js            # executable/WASM module per point type
permissions:
  events: read                   # read redacted events? write? none
  github: none                   # plugins never get GitHub credentials in v1
  network: [jira.corp.example]   # explicit egress allowlist
  secrets: []                    # named secret references, granted per-install
config:                          # user-facing settings, JSON-schema'd
  project: { type: string, default: WAKE }
```

### A2. Install & lifecycle
- Sources: local path / mounted volume, git URL (pinned tag), registry (P3, EXT-10). `wakey plugins install <source>` → manifest + capability review shown → **explicit user grant** of permissions → installed disabled → enable when ready.
- Lifecycle: `install → verify → disabled → enabled → (upgrade | disable | uninstall)`. Upgrades re-run the permission diff (new capabilities require re-grant). Everything hot-loadable — no restart (OPS-2 semantics), every transition audited.
- GUI: Settings → **Plugins** page (list, permissions view, enable/disable, per-plugin config forms from the schema). CLI: `wakey plugins list|install|enable|disable|remove|inspect`.

### A3. Extension points (EXT-7 taxonomy)
| Point | Hook | Capability needs | Conformance |
|---|---|---|---|
| `source` | new event adapters (Fly, Render, Nomad, anything) — same contract as ING-10 | ingest: write | adapter conformance kit |
| `enricher` | transform/annotate events pre-fingerprint (e.g., tag by customer tier) | events: read/write (redacted stream) | schema + redaction-preservation test |
| `detector` | custom fingerprint/policy rules (domain-specific error classes) | events: read | policy-gate simulation tests |
| `responder` | tools the RCA/fix agents may call (via MCP; e.g., query internal systems) | events: read + declared network | tool-allowlist injection suite |
| `notifier` | extra notification targets (NTF-2 contract) | notify: send | delivery/retry conformance |
| `panel` | dashboard panel cards (read-only, scoped data views) | events: read | a11y + design-token check (design.md) |
| `command` | `wakey <plugin-cmd>` CLI subcommands | none by default | exit-code/output contract |

### A4. Sandbox & isolation
- Plugins run out-of-process (worker boundary) with CPU/mem/time limits and the declared network allowlist; no filesystem beyond their data dir; no shell.
- **Failure isolation**: a plugin crash/timeout/slowdown trips its circuit breaker — the point it hooks is bypassed (event flows continue un-enriched, notifications skip that target) with a dashboard warning + metric. Never blocks ingest or tickets (NFR-4 semantics).
- **Security gates**: manifest capability review at install (WF-09 §6 checklist applies); enrichers must pass the redaction-preservation corpus (they may not de-redact); `responder` tools join the prompt-injection allowlist tests; registry plugins are signed + compatibility-badged (EXT-10).

## B. Bring-your-own-AI configuration

### B1. AI profiles
```yaml
# wakey.yml (or GUI Settings / `wakey ai`)
ai:
  profiles:
    fast:   { provider: openai-compatible, base_url: "http://ollama.local:11434/v1",
              model: llama3.1-8b, budget_usd_month: 20 }
    strong: { provider: anthropic, model: <model-id>, env: ANTHROPIC_API_KEY,
              budget_usd_month: 200 }
    litellm-proxy: { provider: openai-compatible, base_url: "https://gw.corp/v1", env: GW_KEY }
  tasks:                     # RCA-7 task→tier routing, user-editable
    triage: fast
    rca: strong
    fix: strong
    verify: fast
```

- **Any provider**: first-class adapters for major vendors + **any OpenAI-compatible endpoint** (covers Ollama, vLLM, LiteLLM, corporate gateways, most local tooling) — SEC-3's local-first promise generalizes to "any AI, your choice."
- **Any task mapping**: triage/RCA/fix/verify each bind to any profile; per-profile budgets, rate limits, and health probes feed the degradation ladder (L1/L2) and cost metering (ANA-1).
- **Validation on save**: connectivity probe, one structured-output smoke test per bound task, budget sanity; a bad profile can't take the router down (last-good config kept, banner shown).
- **Agent-side tools**: `wakey ai profiles list|test|set`, GUI Settings → **AI** page (profiles, task routing table, live spend per profile).

### B2. Any AI *tool*, not just models
- Coding agents/IDEs integrate via open standards (hooks/MCP/stdio) — WPM-1/EXT-9; agents can also *consume* Wakey as MCP context (EXT-3). The gate for "supported AI tool" is the open contract, not our vendor list; community adapters land as plugins (A).

## State written

`plugins` (manifest, permissions grants, state, per-plugin config), `ai_profiles` (bindings, budgets, probe results), audit lines for every lifecycle/routing change, metrics (`wakey_plugin_breakers_total{plugin,point}`, per-profile spend). GitHub state untouched.

## Failure modes

| Failure | Expected behavior |
|---|---|
| Plugin crashes / hangs / over-limits | Circuit breaker isolates it; its point degrades gracefully; pipeline unaffected; warning + metric + audit |
| Plugin requests new capabilities on upgrade | Upgrade pauses pending explicit re-grant; old version keeps running until granted or cancelled |
| Enricher breaks redaction (leaks `[REDACTED]` handling or de-redacts) | Conformance test blocks at install; runtime canary detects and disables plugin + security alert |
| AI provider outage / budget exhausted | Existing degradation ladder (L1/L2) — unaffected by which profile failed; per-profile health shown in GUI/`wakey ai profiles` |
| Bad AI profile saved (bad key/model) | Save-time probe rejects; router keeps last-good binding; banner with the exact failure |
| Plugin API version mismatch (`api:` range) | Refuses to enable with clear upgrade message; other plugins unaffected |
| Registry plugin unsigned (P3) | Refuse unless `--allow-unsigned` + loud warning + audit |

## Acceptance criteria

1. Lifecycle e2e: install (from git+local) → permission grant → enable → upgrade with capability diff → disable → uninstall; every transition audited and hot-applied (no restart) with a real demo plugin.
2. Isolation: chaos plugin (crash/loop/slow) on each point → pipeline latency unaffected beyond breaker threshold, warnings correct (integration).
3. Capability enforcement: every undeclared access attempt (network, fs, secrets) blocked + audited (property test).
4. Redaction preservation: all bundled + conformance enrichers pass the SEC-1 corpus; runtime canary disables a violating plugin (security suite).
5. BYO-AI: swap `strong` from vendor A to vendor B / to a local Ollama profile by config only — eval bench re-runs and reports per-profile results; no code change (e2e + bench).
6. Profile save-time probe: bad endpoint/key rejected with actionable error; router holds last-good (unit + integration).
7. Plugin/CLI parity: every plugin & AI action available in GUI *and* CLI with same audit shape (parity test per WF-13 §AC1).
