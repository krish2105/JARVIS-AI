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

_SECRET_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "PICOVOICE_ACCESS_KEY",
    "GMAIL_MCP_TOKEN",
    "GDRIVE_MCP_TOKEN",
)


@dataclass
class ModelConfig:
    default: str = "claude-sonnet-5"
    heavy: str = "claude-opus-4-8"
    fast: str = "claude-haiku-4-5-20251001"


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
    corner: str = "bottom-right"


@dataclass
class Config:
    wake_word: str = "jarvis"
    voice: str = "am_liam"
    model: ModelConfig = field(default_factory=ModelConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    filesystem_allowlist: list[str] = field(default_factory=list)
    tool_allowlist: list[str] = field(default_factory=list)
    require_confirmation_for: list[str] = field(default_factory=list)
    hud: HudConfig = field(default_factory=HudConfig)

    # populated from environment, not config.yaml
    anthropic_api_key: str | None = None
    picovoice_access_key: str | None = None
    wake_word_model_path: str | None = None
    gmail_mcp_url: str | None = None
    gmail_mcp_token: str | None = None
    gdrive_mcp_url: str | None = None
    gdrive_mcp_token: str | None = None

    def resolved_filesystem_allowlist(self) -> list[Path]:
        return [Path(p).expanduser().resolve() for p in self.filesystem_allowlist]

    def redacted(self) -> dict[str, Any]:
        data = copy.deepcopy(self.__dict__)
        for key in (
            "anthropic_api_key",
            "picovoice_access_key",
            "gmail_mcp_token",
            "gdrive_mcp_token",
        ):
            if data.get(key):
                data[key] = "***redacted***"
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
            default=model_raw.get("default", "claude-sonnet-5"),
            heavy=model_raw.get("heavy", "claude-opus-4-8"),
            fast=model_raw.get("fast", "claude-haiku-4-5-20251001"),
        ),
        audio=AudioConfig(
            sample_rate=audio_raw.get("sample_rate", 16000),
            vad_silence_ms=audio_raw.get("vad_silence_ms", 700),
            max_record_seconds=audio_raw.get("max_record_seconds", 15),
            wake_word_sensitivity=audio_raw.get("wake_word_sensitivity", 0.6),
        ),
        filesystem_allowlist=raw.get("filesystem_allowlist", ["."]),
        tool_allowlist=raw.get("tool_allowlist", []),
        require_confirmation_for=raw.get("require_confirmation_for", []),
        hud=HudConfig(
            host=hud_raw.get("host", "127.0.0.1"),
            port=hud_raw.get("port", 8765),
            corner=hud_raw.get("corner", "bottom-right"),
        ),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        picovoice_access_key=os.getenv("PICOVOICE_ACCESS_KEY") or None,
        wake_word_model_path=os.getenv("JARVIS_WAKE_WORD_MODEL_PATH") or None,
        gmail_mcp_url=os.getenv("GMAIL_MCP_URL") or None,
        gmail_mcp_token=os.getenv("GMAIL_MCP_TOKEN") or None,
        gdrive_mcp_url=os.getenv("GDRIVE_MCP_URL") or None,
        gdrive_mcp_token=os.getenv("GDRIVE_MCP_TOKEN") or None,
    )
    return cfg


if __name__ == "__main__":
    import json

    cfg = load_config()
    print(json.dumps(cfg.redacted(), indent=2, default=str))
