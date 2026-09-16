# SPDX-License-Identifier: Apache-2.0
"""CI version of the M0 rehearsal — simulated GitHub, no live calls."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "demo"))

from m0_gate_rehearsal import main  # noqa: E402


def test_m0_gate_rehearsal_with_simulated_github() -> None:
    assert main() == 0
