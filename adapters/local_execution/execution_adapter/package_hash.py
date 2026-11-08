"""Stable hash of the distributable source tree."""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"SOURCE_HASH.txt"}


def source_files(root: Path | None = None) -> list[Path]:
    base = root or PACKAGE_ROOT
    files = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(base).as_posix()
        if relative in EXCLUDED or "__pycache__" in relative or relative.endswith(".pyc"):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(base).as_posix())


def source_hash(root: Path | None = None) -> str:
    base = root or PACKAGE_ROOT
    digest = hashlib.sha256()
    for path in source_files(base):
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
