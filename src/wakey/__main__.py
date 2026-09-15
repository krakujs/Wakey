# SPDX-License-Identifier: Apache-2.0
"""Entry point for ``python -m wakey``.

Reports the running version until the wakeyd server lands (E2-T4); the
container ``CMD`` and quickstart depend on this staying truthful.
"""

from __future__ import annotations

import argparse

from wakey import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wakey",
        description="Wakey — the self-hosted AI SRE that watches your services.",
    )
    parser.add_argument("--version", action="version", version=f"wakey {__version__}")
    parser.parse_args(argv)
    print(f"wakey {__version__} — server not started (development pre-M0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
