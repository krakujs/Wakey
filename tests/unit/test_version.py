# SPDX-License-Identifier: Apache-2.0
"""Version sanity: the package must import and report a semver-ish version."""

from __future__ import annotations

import re

import wakey


def test_version_is_semver_like() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:\.[\w-]+)?", wakey.__version__), wakey.__version__


def test_version_is_pre_m0_dev() -> None:
    assert wakey.__version__.startswith("0.1.0"), "project is pre-M0; version must reflect that"
