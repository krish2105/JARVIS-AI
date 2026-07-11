"""Loads config.yaml + .env into a single typed Config object.

Run directly (`python -m src.system.config`) to sanity-check the merged
config with secrets redacted.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass
class ModelConfig:
    # mlx-community model repo ids, downloaded from Hugging Face on first
    # use and cached locally — no API key, no per-token cost. 3B-class
    # models are NOT used by default: in testing, a 3B model fabricated
    # ("hallucinated") an entire fake tool execution — inventing a script,
    # a fake confirmation prompt, and a fake success message — without
    # ever emitting a real tool call. 8B is the smallest size that follows
    # the tool-call protocol reliably enough to trust with run_shell/
    # write_file.
    local: str = "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit"
    local_heavy: str = "mlx-community/Qwen2.5-14B-Instruct-4bit"


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    vad_silence_ms: int = 700
    max_record_seconds: int = 15
    wake_word_sensitivity: float = 0.6


@dataclass
class HudConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class Config:
    wake_word: str = "jarvis"
    voice: str = "am_liam"
    model: ModelConfig = field(default_factory=ModelConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    filesystem_allowlist: list[str] = field(default_factory=list)
    require_confirmation_for: list[str] = field(default_factory=list)
    hud: HudConfig = field(default_factory=HudConfig)

    # populated from environment, not config.yaml — optional path to a custom
    # openWakeWord model file, if you ever train one beyond the bundled
    # "hey jarvis" model.
    wake_word_model_path: str | None = None

    def resolved_filesystem_allowlist(self) -> list[Path]:
        return [Path(p).expanduser().resolve() for p in self.filesystem_allowlist]

    def redacted(self) -> dict[str, Any]:
        data = copy.deepcopy(self.__dict__)
        data["model"] = vars(self.model)
        data["audio"] = vars(self.audio)
        data["hud"] = vars(self.hud)
        return data


def load_config(config_path: Path = CONFIG_PATH, env_path: Path = ENV_PATH) -> Config:
    load_dotenv(dotenv_path=env_path, override=False)

    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    model_raw = raw.get("model", {}) or {}
    audio_raw = raw.get("audio", {}) or {}
    hud_raw = raw.get("hud", {}) or {}

    cfg = Config(
        wake_word=os.getenv("JARVIS_WAKE_WORD", raw.get("wake_word", "jarvis")),
        voice=raw.get("voice", "am_liam"),
        model=ModelConfig(
            local=os.getenv("JARVIS_LOCAL_MODEL", model_raw.get("local", "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit")),
            local_heavy=os.getenv(
                "JARVIS_LOCAL_HEAVY_MODEL",
                model_raw.get("local_heavy", "mlx-community/Qwen2.5-14B-Instruct-4bit"),
            ),
        ),
        audio=AudioConfig(
            sample_rate=audio_raw.get("sample_rate", 16000),
            vad_silence_ms=audio_raw.get("vad_silence_ms", 700),
            max_record_seconds=audio_raw.get("max_record_seconds", 15),
            wake_word_sensitivity=audio_raw.get("wake_word_sensitivity", 0.6),
        ),
        filesystem_allowlist=raw.get("filesystem_allowlist", ["."]),
        require_confirmation_for=raw.get("require_confirmation_for", []),
        hud=HudConfig(
            host=hud_raw.get("host", "127.0.0.1"),
            port=hud_raw.get("port", 8765),
        ),
        wake_word_model_path=os.getenv("JARVIS_WAKE_WORD_MODEL_PATH") or None,
    )
    return cfg


def _deep_merge(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def update_config_yaml(patch: dict, config_path: Path = CONFIG_PATH) -> None:
    """Merges `patch` into config.yaml and writes it back. Used by the
    Settings view in the HUD app (src/hud/api.py).

    Known limitation: this rewrites the whole file via yaml.safe_dump,
    which does not preserve the comments in the original config.yaml.
    Acceptable trade-off for a user-editable settings file; a
    comment-preserving rewrite would need ruamel.yaml instead of PyYAML.
    """
    raw: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    _deep_merge(raw, patch)
    with open(config_path, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False)


if __name__ == "__main__":
    import json

    cfg = load_config()
    print(json.dumps(cfg.redacted(), indent=2, default=str))
