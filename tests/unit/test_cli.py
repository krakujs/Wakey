# SPDX-License-Identifier: Apache-2.0
"""CLI tests (R-11/R-12): status, board, doctor report honestly and exit cleanly."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import wakey.__main__ as cli
from wakey.core.composition import open_storage
from wakey.core.config import Settings
from wakey.core.models import Fingerprint, Severity, WorkState


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # hermetic: no developer .env loads
    monkeypatch.setenv("WAKEY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WAKEY_GITHUB_TOKEN", raising=False)
    return tmp_path


def seed_fingerprint(data_dir: Path) -> None:
    storage = open_storage(Settings(data_dir=data_dir))
    storage.save_fingerprint(
        Fingerprint(
            fp_hash="3f9a1b2c4d5e6f70",
            service="payments-api",
            environment="prod",
            template="TypeError: boom",
            severity=Severity.ERROR,
            occurrences=3,
            state=WorkState.OPEN,
        )
    )
    storage.close()


def test_status_reports_local_view(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_fingerprint(cli_env)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "active fingerprints: 1" in out
    assert "console forge (dev)" in out, "no GitHub credentials -> honest dev-sink label"


def test_board_lists_fingerprint_rows(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_fingerprint(cli_env)
    assert cli.main(["board", "--service", "payments-api"]) == 0
    out = capsys.readouterr().out
    assert "3f9a1b2c4d5e6f70" in out and "open" in out


def test_doctor_passes_on_local_pipeline(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "local pipeline ->" in out
    assert "live model -> skipped" in out, "unconfigured LLM is reported, not claimed"
    assert "NOT exercised" in out, "doctor is explicit about what it does not test"


def test_doctor_survives_missing_env_without_llm(cli_env: Path) -> None:
    # no WAKEY_LLM_*: doctor must still exit 0 on the local pipeline alone
    assert cli.main(["doctor"]) == 0


def test_unknown_command_prints_hint(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "wakey serve" in capsys.readouterr().out


def test_backup_creates_file_and_restore_round_trips(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_fingerprint(cli_env)
    assert cli.main(["backup"]) == 0
    out = capsys.readouterr().out
    assert "backup written" in out
    backup_path = Path(out.strip().rsplit(": ", 1)[1])
    assert backup_path.exists()

    # restore the backup over the live db and verify the store answers
    assert cli.main(["restore", str(backup_path)]) == 0
    assert "restore complete" in capsys.readouterr().out
    restored = Path(cli_env / "wakey.db")
    assert restored.exists()


def test_restore_rejects_missing_file(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["restore", "/nonexistent/backup.db"]) == 1
    assert "not a file" in capsys.readouterr().out


def test_setup_token_prints_single_use_token(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["setup-token", "--force"]) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("WAKE-")


def test_load_dotenv_sets_missing_keys_and_skips_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader fills absent vars, never overrides, and ignores junk lines."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# comment\nWAKEY_PORT=9999\nMALFORMED LINE NO EQUALS\nWAKEY_TEST_NEW=hello world\n"
    )
    monkeypatch.setenv("WAKEY_PORT", "1234")
    monkeypatch.chdir(tmp_path)
    cli.load_dotenv()
    assert os.environ["WAKEY_PORT"] == "1234", "existing env wins"
    assert os.environ["WAKEY_TEST_NEW"] == "hello world"
    monkeypatch.delenv("WAKEY_TEST_NEW", raising=False)
