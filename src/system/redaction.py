"""Secret redaction + rotating log handlers.

Everything Jarvis writes to disk — the diagnostic log, the tool-call log, the
conversation transcript — can pick up a secret that happened to pass through a
tool argument or result (an API key in a file the user asked it to read, a
token in a shell command). `redact_secrets` scrubs the common shapes before
anything is persisted, and `rotating_handler` keeps the logs from growing
without bound.

Redaction is deliberately conservative about what counts as a secret: it
targets keys/tokens/passwords, NOT ordinary content like the user's own words
or email addresses, so the transcript stays useful.
"""

from __future__ import annotations

import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_REDACTED = "[REDACTED]"

# (pattern, replacement). Order matters — more specific first.
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Provider-shaped tokens with a recognizable prefix.
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
    # key=value / "key": "value" shapes for generic secret-ish names.
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?token)"
                r"(\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})(\3)"),
     lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{_REDACTED}{m.group(5)}"),
    # Authorization: Bearer <token>
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)(\S+)"),
     lambda m: f"{m.group(1)}{_REDACTED}"),
]


def redact_secrets(text: str) -> str:
    """Return `text` with recognizable secrets replaced by placeholders."""
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def rotating_handler(path: Path, max_bytes: int = 5_000_000, backup_count: int = 3):
    """A size-rotating file handler (default 5 MB × 3 backups) whose records
    are redacted before they hit disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.addFilter(_RedactingFilter())
    return handler


class _RedactingFilter:
    """logging filter that rewrites the formatted message in place."""

    def filter(self, record) -> bool:  # noqa: A003 - logging API name
        try:
            record.msg = redact_secrets(str(record.getMessage()))
            record.args = ()
        except Exception:  # noqa: BLE001 - logging must never raise
            pass
        return True
