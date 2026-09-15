# Security Policy

Wakey watches production logs and holds credentials (forge tokens, AI keys).
We treat security reports as the highest-priority work in the project.

## Reporting a vulnerability

**Do not open a public issue or discussion for security problems.**

Report privately via GitHub Security Advisories ("Report a vulnerability" on
this repository) or directly to the maintainers (contact to be published with
the first public release — see docs/STATE.md).

Please include: affected version/commit, the workflow or component
(`docs/workflows/WF-*.md` reference), a minimal reproduction, and your
assessment of impact. If the report involves log content, use synthetic data —
never real secrets or customer logs.

## Scope — what we consider security-relevant

- Redaction failures: any path where log-derived secrets/PII survive storage
  or prompts (`WF-09`, SEC-1).
- Prompt injection: log content influencing agent tool use (`WF-09` §4, SEC-2).
- Privilege/credential handling: forge tokens, service keys, master key (SEC-4).
- Autonomy violations: anything that could merge, force-push, exceed caps, or
  act outside the configured autonomy level (SKILL.md invariants).
- Sandbox escapes in the fix/test runners (FIX-10).

## Supported versions

| Version | Supported |
|---|---|
| 0.1.0.dev (pre-release, main branch) | ✅ fixes land on main |

## Response targets

- Acknowledgement: within 3 days.
- Triage & severity: within 7 days.
- Fix or mitigation for high/critical: next patch release, targeted ≤30 days.
- We will credit reporters in the release notes unless you prefer otherwise.

## Security posture references

The controls these reports map to are specified in `docs/workflows/WF-09-security-redaction.md`
and enforced by `tests/security/` (redaction corpus, prompt-injection corpus,
capability tests). See also `docs/engineering-standards.md` §6.
