"""Tests for src/system/config.py's update_config_yaml — the write side
used by the React app's Settings view."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.system.config import MANDATORY_CONFIRMATIONS, update_config_yaml  # noqa: E402


def _without_mandatory(result: dict) -> dict:
    """Every write now guarantees the mandatory confirmation gates exist
    (a safety invariant — see config.py:_enforce_mandatory_confirmations).
    Strip that injected key so these tests can assert on the rest."""
    assert set(MANDATORY_CONFIRMATIONS).issubset(set(result.get("require_confirmation_for", [])))
    return {k: v for k, v in result.items() if k != "require_confirmation_for"}


def test_update_config_yaml_merges_nested_patch(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"wake_word": "jarvis", "model": {"local": "old-model"}}))

    update_config_yaml({"model": {"local": "new-model"}}, config_path=config_path)

    result = yaml.safe_load(config_path.read_text())
    assert result["wake_word"] == "jarvis"  # untouched
    assert result["model"]["local"] == "new-model"


def test_update_config_yaml_creates_file_if_missing(tmp_path):
    config_path = tmp_path / "config.yaml"
    update_config_yaml({"voice": "af_heart"}, config_path=config_path)
    assert _without_mandatory(yaml.safe_load(config_path.read_text())) == {"voice": "af_heart"}


def test_update_config_yaml_adds_new_top_level_keys(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"wake_word": "jarvis"}))

    update_config_yaml({"hud": {"port": 9000}}, config_path=config_path)

    result = yaml.safe_load(config_path.read_text())
    assert _without_mandatory(result) == {"wake_word": "jarvis", "hud": {"port": 9000}}
