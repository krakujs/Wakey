# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the configuration system (E2-T2)."""

from __future__ import annotations

import pytest

from wakey.core.config import Autonomy, ConfigError, Settings, load_wakey_yaml

VALID_YAML = """
services:
  payments-api:
    path: services/payments-api
    autonomy: fix
    redact:
      - "sk_live_[0-9a-zA-Z]{20,}"
  checkout-web:
    autonomy: observe
"""
ERR_YAML = """
services:
  payments-api:
    autonomy: do-everything
    confidence_floor: 7.5
"""
BAD_REGEX_YAML = """
services:
  payments-api:
    redact:
      - "([a-z+"
"""


def test_settings_defaults_and_env_overrides() -> None:
    defaults = Settings.from_env({})
    assert defaults.port == 8477
    assert defaults.data_dir == __import__("pathlib").Path("wakey-data")

    tuned = Settings.from_env({"WAKEY_PORT": "9000", "WAKEY_DATA_DIR": "/var/lib/wakey"})
    assert tuned.port == 9000
    assert str(tuned.data_dir) == "/var/lib/wakey"


def test_settings_rejects_bad_port_and_log_level_together() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env({"WAKEY_PORT": "abc", "WAKEY_LOG_LEVEL": "LOUD"})
    messages = " | ".join(excinfo.value.problems)
    assert "WAKEY_PORT" in messages and "WAKEY_LOG_LEVEL" in messages


def test_load_valid_yaml_with_defaults() -> None:
    parsed = load_wakey_yaml(VALID_YAML)
    assert set(parsed.services) == {"payments-api", "checkout-web"}
    assert parsed.services["payments-api"].autonomy is Autonomy.FIX
    assert parsed.services["checkout-web"].autonomy is Autonomy.OBSERVE
    assert parsed.services["checkout-web"].rate_per_min == 5  # default
    assert parsed.services["payments-api"].redact  # pattern kept for redaction engine


def test_invalid_values_reported_with_field_names() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_wakey_yaml(ERR_YAML)
    joined = " | ".join(excinfo.value.problems)
    assert "autonomy" in joined and "confidence_floor" in joined


def test_bad_redact_regex_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_wakey_yaml(BAD_REGEX_YAML)
    assert "invalid redact regex" in " | ".join(excinfo.value.problems)


def test_yaml_syntax_error_reports_line() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_wakey_yaml("services: [unclosed")
    assert "invalid YAML" in excinfo.value.problems[0]


def test_non_mapping_rejected() -> None:
    with pytest.raises(ConfigError):
        load_wakey_yaml("- just\n- a\n- list\n")
