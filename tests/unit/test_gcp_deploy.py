# SPDX-License-Identifier: Apache-2.0
"""Regression tests for environment loading in the GCP deployment script."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gcp_deploy import load_env_pairs  # noqa: E402


def test_load_env_pairs_allows_missing_dotenv_in_ci(tmp_path) -> None:
    pairs = load_env_pairs(
        tmp_path / ".env",
        "postgresql://managed-db",
        {"WAKEY_MASTER_KEY": "from-ci", "IGNORED": "value"},
    )

    assert pairs == {
        "WAKEY_MASTER_KEY": "from-ci",
        "WAKEY_DATABASE_URL": "postgresql://managed-db",
    }


def test_load_env_pairs_overlays_optional_dotenv(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text('WAKEY_MASTER_KEY="from-file"\n# ignored\n')

    pairs = load_env_pairs(dotenv, "", {"WAKEY_MASTER_KEY": "from-ci"})

    assert pairs == {"WAKEY_MASTER_KEY": "from-file"}
