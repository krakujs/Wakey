# Contributing to Wakey

Thanks for helping build the AI teammate that watches production. Short version:
**Apache-2.0 in, Apache-2.0 out, DCO sign-off, keep it boring and well-structured.**

## Setup

```bash
git clone <your fork> && cd wakey
make install        # venv + wakey + dev tools
make check          # what every commit must pass: format, lint, types, tests
```

Python 3.12+ required (3.14 used by the team). All state is local — no cloud
accounts needed to develop.

## How we work

- Read `SKILL.md` first — it is the operating manual (invariants, session
  protocol, phase gates).
- Claim work from `docs/TASKS.md`; behavior is defined by the
  `docs/workflows/WF-*.md` specs. If code and spec disagree, both get fixed
  in the same change.
- One logical change per commit, Conventional Commits style (`feat(detect): …`).
- Commits require a **DCO sign-off** (`git commit -s`): you certify the
  contribution is yours to submit under Apache-2.0. No CLA.
- Parallel work follows the lanes & worktree protocol in
  `docs/execution-plan.md` — never edit `docs/STATE.md`/`docs/TASKS.md` from
  a lane branch.
- Use the PR template — it includes the Wakey invariants checklist
  (suggest-don't-merge, redaction, audit lines).

## Quality bar (enforced, see docs/engineering-standards.md)

- `make check` green: ruff format+lint, **mypy strict**, pytest with timeout
  and ≤4 workers, SPDX headers on every file.
- Coverage floor **≥ 90 %**, enforced in CI — drop below and the build fails.
- Every acceptance criterion has a test; every bug fix ships with its
  regression test.
- Resource budgets (NFR-6..8) are regression-tested; the fix-executor sandbox
  gate runs in a capped container (`make gate-sandbox`).

## Reporting bugs & security issues

Bugs: open an issue with the workflow spec reference and a minimal
reproduction. Security: see [`SECURITY.md`](SECURITY.md) and
[`docs/threat-model.md`](docs/threat-model.md) — please do not open public
issues for vulnerabilities.

## Licensing

Apache-2.0 (see [`LICENSE`](LICENSE)). By contributing you agree your work is
licensed under Apache-2.0 and you provide the DCO sign-off. Third-party
attribution lives in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).
