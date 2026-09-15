#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Insert/verify SPDX license headers in Wakey source files.

Used by ``make headers`` / ``make headers-check`` (CI). Idempotent: files
already carrying the marker are left untouched.

Usage:
    python3 scripts/add_spdx_headers.py            # add missing headers
    python3 scripts/add_spdx_headers.py --check    # exit 1 if any are missing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SPDX = "# SPDX-License-Identifier: Apache-2.0\n"
TARGETS = ("src", "tests", "scripts", "eval")
SHEBANG = "#!"


def has_header(path: Path) -> bool:
    with path.open(encoding="utf-8") as handle:
        head = handle.readlines()[:5]
    return any("SPDX-License-Identifier" in line for line in head)


def add_header(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    insert_at = 1 if lines and lines[0].startswith(SHEBANG) else 0
    lines.insert(insert_at, SPDX)
    path.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only verify, exit 1 on missing")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    missing: list[Path] = []
    fixed = 0
    for target in TARGETS:
        base = root / target
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if has_header(path):
                continue
            if args.check:
                missing.append(path)
            else:
                add_header(path)
                fixed += 1

    if args.check and missing:
        for path in missing:
            print(f"missing SPDX header: {path.relative_to(root)}", file=sys.stderr)
        print(f"{len(missing)} file(s) missing headers", file=sys.stderr)
        return 1
    summary = f"{len(missing)} missing" if args.check else f"added {fixed}"
    print(f"SPDX headers OK ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
