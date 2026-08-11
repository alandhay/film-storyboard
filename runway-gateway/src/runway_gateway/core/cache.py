"""Content-addressed cache and the ``depends_on`` key-derivation problem.

Upstream artifacts appear in a child's params as signed URLs that rotate between
runs; hashing the URL makes every re-run miss. ``ArtifactRef`` carries both the
stable ``cache_key`` (used for hashing) and the current ``url`` (used for the live
call), so a re-run is fully cached despite rotation. See docs/design.md §3.1.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..api.models import TaskState

CacheKey = str  # sha256 hex


@dataclass(frozen=True)
class ArtifactRef:
    """A resolved upstream output. Hash by ``cache_key``; call the API with ``url``."""

    cache_key: CacheKey
    url: str
    kind: str = "video"  # "image" | "video" | "audio"


@dataclass(frozen=True)
class CachedGeneration:
    cache_key: CacheKey
    endpoint: str
    task_id: str
    output_urls: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    model: str | None
    estimated_credits: float
    created_at: str


@dataclass(frozen=True)
class TaskRecord:
    """Persisted at submission time; the spine both execution modes share."""

    cache_key: CacheKey
    task_id: str
    endpoint: str
    params_digest: str
    status: TaskState
    submitted_at: str


# --- canonicalization / key derivation ------------------------------------------


def _canonicalize(value: Any) -> Any:
    if isinstance(value, ArtifactRef):
        return {"__artifact_ref__": value.cache_key}
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def compute_cache_key(
    endpoint: str, params: Mapping[str, Any], *, upstream: Sequence[CacheKey] = ()
) -> CacheKey:
    """sha256 over (endpoint, params-with-refs-as-keys, deduped sorted upstream)."""
    payload = {
        "endpoint": endpoint,
        "params": _canonicalize(dict(params)),
        "upstream": sorted(set(upstream)),
    }
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()


def collect_upstream(params: Mapping[str, Any]) -> list[CacheKey]:
    """Every ArtifactRef.cache_key reachable in params (any nesting depth)."""
    found: list[CacheKey] = []

    def walk(value: Any) -> None:
        if isinstance(value, ArtifactRef):
            found.append(value.cache_key)
        elif isinstance(value, Mapping):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(params)
    return found


def resolve_refs(value: Any) -> Any:
    """Replace every ArtifactRef with its ``url`` for the live API call."""
    if isinstance(value, ArtifactRef):
        return value.url
    if isinstance(value, Mapping):
        return {k: resolve_refs(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [resolve_refs(v) for v in value]
    return value


def params_digest(params: Mapping[str, Any]) -> str:
    return hashlib.sha256(_dumps(_canonicalize(dict(params))).encode("utf-8")).hexdigest()[:16]


# --- backends -------------------------------------------------------------------


class CacheBackend(Protocol):
    def get(self, key: CacheKey) -> CachedGeneration | None: ...
    def put(self, value: CachedGeneration) -> None: ...
    def get_task(self, key: CacheKey) -> TaskRecord | None: ...
    def put_task(self, record: TaskRecord) -> None: ...


@dataclass
class InMemoryCache(CacheBackend):
    """Trivial dict-backed cache (tests, and the Postgres-later Protocol demo)."""

    _gens: dict[CacheKey, CachedGeneration] = field(default_factory=dict)
    _tasks: dict[CacheKey, TaskRecord] = field(default_factory=dict)

    def get(self, key: CacheKey) -> CachedGeneration | None:
        return self._gens.get(key)

    def put(self, value: CachedGeneration) -> None:
        self._gens[value.cache_key] = value

    def get_task(self, key: CacheKey) -> TaskRecord | None:
        return self._tasks.get(key)

    def put_task(self, record: TaskRecord) -> None:
        self._tasks[record.cache_key] = record


class SqliteCache(CacheBackend):
    """SQLite backend (WAL). Single connection + lock so fan-out threads share it.

    A Postgres implementation can satisfy the same ``CacheBackend`` Protocol later.
    """

    def __init__(self, path: str | Path = "runway-cache.sqlite") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS generations (
                cache_key TEXT PRIMARY KEY, endpoint TEXT, task_id TEXT,
                output_urls TEXT, artifact_paths TEXT, model TEXT,
                estimated_credits REAL, created_at TEXT)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS task_records (
                cache_key TEXT PRIMARY KEY, task_id TEXT, endpoint TEXT,
                params_digest TEXT, status TEXT, submitted_at TEXT)"""
        )
        self._conn.commit()

    def get(self, key: CacheKey) -> CachedGeneration | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT cache_key, endpoint, task_id, output_urls, artifact_paths, "
                "model, estimated_credits, created_at FROM generations WHERE cache_key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return CachedGeneration(
            cache_key=row[0],
            endpoint=row[1],
            task_id=row[2],
            output_urls=tuple(json.loads(row[3])),
            artifact_paths=tuple(json.loads(row[4])),
            model=row[5],
            estimated_credits=row[6],
            created_at=row[7],
        )

    def put(self, value: CachedGeneration) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO generations VALUES (?,?,?,?,?,?,?,?)",
                (
                    value.cache_key,
                    value.endpoint,
                    value.task_id,
                    json.dumps(list(value.output_urls)),
                    json.dumps(list(value.artifact_paths)),
                    value.model,
                    value.estimated_credits,
                    value.created_at,
                ),
            )
            self._conn.commit()

    def get_task(self, key: CacheKey) -> TaskRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT cache_key, task_id, endpoint, params_digest, status, submitted_at "
                "FROM task_records WHERE cache_key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return TaskRecord(
            cache_key=row[0],
            task_id=row[1],
            endpoint=row[2],
            params_digest=row[3],
            status=TaskState(row[4]),
            submitted_at=row[5],
        )

    def put_task(self, record: TaskRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO task_records VALUES (?,?,?,?,?,?)",
                (
                    record.cache_key,
                    record.task_id,
                    record.endpoint,
                    record.params_digest,
                    record.status.value,
                    record.submitted_at,
                ),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
