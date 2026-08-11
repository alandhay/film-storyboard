"""Value types normalized out of the SDK's response models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskState(Enum):
    PENDING = "PENDING"
    THROTTLED = "THROTTLED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
)


@dataclass(frozen=True)
class TaskHandle:
    """A submitted task: enough to poll it, and to cost/cache it.

    ``params`` is the exact submitted param dict; ``model`` is lifted out of it
    for convenience (pricing/logging). Both are retained so the Phase 2 cache can
    derive its content-addressed key without re-deriving anything.
    """

    task_id: str
    endpoint: str
    model: str | None
    params: Mapping[str, Any]


@dataclass(frozen=True)
class TaskResult:
    """A polled task snapshot, normalized across the SDK's status union."""

    task_id: str
    status: TaskState
    output: tuple[str, ...] = ()
    failure: str | None = None
    failure_code: str | None = None
    progress: float | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @classmethod
    def from_task(cls, raw: Any) -> TaskResult:
        """Build from an SDK ``TaskRetrieveResponse`` union member (or any object
        exposing the same attributes - the fake client does too)."""
        output_raw = getattr(raw, "output", None) or ()
        return cls(
            task_id=str(raw.id),
            status=TaskState(str(raw.status)),
            output=tuple(str(u) for u in output_raw),
            failure=getattr(raw, "failure", None),
            failure_code=getattr(raw, "failure_code", None),
            progress=getattr(raw, "progress", None),
        )
