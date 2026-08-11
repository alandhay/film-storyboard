"""Artifact durability. Output URLs expire in 24-48h; persist what's worth keeping.

Stage-to-stage chaining still passes the remote URL (inside an ArtifactRef) - these
local copies are for durability and final delivery, not for re-upload between stages.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    def persist(self, url: str, *, key: str, suffix: str) -> str: ...


class NullArtifactStore:
    """No-op store (chaining by URL only). Default when durability isn't needed."""

    def persist(self, url: str, *, key: str, suffix: str) -> str:
        return ""


class LocalArtifactStore:
    """Copy outputs to ``root/<key>.<suffix>``.

    Uses ``urllib`` so ``http(s)://``, ``file://`` and ``data:`` URLs all work
    (the last makes durability testable offline).
    """

    def __init__(self, root: str | Path = "artifacts") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def persist(self, url: str, *, key: str, suffix: str) -> str:
        dest = self._root / f"{key}.{suffix.lstrip('.')}"
        with urllib.request.urlopen(url) as response:  # noqa: S310 - controlled URLs
            dest.write_bytes(response.read())
        return str(dest)
