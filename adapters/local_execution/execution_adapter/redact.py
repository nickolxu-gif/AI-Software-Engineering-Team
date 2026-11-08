"""Redact credential-shaped text from captured output."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TOKEN = re.compile(r"(?:sk" + r"-|Bearer\s+)[A-Za-z0-9._\-]{8,}", re.IGNORECASE)
_HOME = re.compile(r"/" + r"(?:Users|home)/" + r"[^\s\"']+")


def redact(text: str) -> str:
    cleaned = _EMAIL.sub("[REDACTED]", text)
    cleaned = _TOKEN.sub("[REDACTED]", cleaned)
    cleaned = _HOME.sub("[REDACTED]", cleaned)
    return cleaned
