"""Failure taxonomy and classifiers.

Callers branch on *why* something failed:

* ``MODERATION`` - input/output refused. Retrying is pure waste.
* ``TRANSIENT`` - 429/5xx or a retryable task failure. Back off and retry.
* ``PERMANENT`` - bad params / unsupported model / invalid asset. Fix the code.
* ``TIMEOUT`` - never settled in the allowed window.

The task-failure codes are an *open* set (docs.dev.runwayml.com/errors/task-failures
gives prefixes and examples, not a closed enum), so we classify on dotted-segment
prefixes with a TRANSIENT default, and treat anything in a ``SAFETY`` namespace as
MODERATION. Transport errors are classified from HTTP status / SDK exception type.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


class FailureClass(Enum):
    MODERATION = "moderation"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    TIMEOUT = "timeout"


# --- task-level failure_code classification -------------------------------------

_PERMANENT_PREFIXES: Final[tuple[str, ...]] = ("ASSET.INVALID",)
_TRANSIENT_PREFIXES: Final[tuple[str, ...]] = (
    "INTERNAL.BAD_OUTPUT",
    "INPUT_PREPROCESSING.INTERNAL",
    "THIRD_PARTY.UNAVAILABLE",
)
_TRANSIENT_EXACT: Final[frozenset[str]] = frozenset({"INTERNAL"})


def classify_failure_code(code: str | None) -> FailureClass:
    """Classify a task ``failure_code`` from a FAILED task.

    Unknown/absent codes default to TRANSIENT (a retry is cheap relative to
    silently discarding a possibly-recoverable failure).
    """
    if not code:
        return FailureClass.TRANSIENT
    normalized = code.strip().upper()
    segments = normalized.split(".")
    # Any SAFETY segment => moderation, regardless of surrounding namespace:
    #   SAFETY.INPUT.TEXT, SAFETY.OUTPUT.*, INPUT_PREPROCESSING.SAFETY.TEXT
    if "SAFETY" in segments:
        return FailureClass.MODERATION
    if any(normalized.startswith(p) for p in _PERMANENT_PREFIXES):
        return FailureClass.PERMANENT
    if any(normalized.startswith(p) for p in _TRANSIENT_PREFIXES):
        return FailureClass.TRANSIENT
    if normalized in _TRANSIENT_EXACT:
        return FailureClass.TRANSIENT
    return FailureClass.TRANSIENT


# --- transport-level exception classification -----------------------------------

_PERMANENT_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        "BadRequestError",
        "NotFoundError",
        "UnprocessableEntityError",
        "PermissionDeniedError",
        "AuthenticationError",
        "ConflictError",
    }
)
_TRANSIENT_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
        "APIResponseValidationError",
    }
)


def classify_exception(exc: BaseException) -> FailureClass:
    """Classify an exception raised by the transport (SDK ``*.create`` etc.).

    Prefers HTTP status when present (``429`` / ``5xx`` -> TRANSIENT, other
    ``4xx`` -> PERMANENT), then the SDK exception class name, defaulting to
    TRANSIENT.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status == 429 or status >= 500:
            return FailureClass.TRANSIENT
        if 400 <= status < 500:
            return FailureClass.PERMANENT
    name = type(exc).__name__
    if name in _PERMANENT_EXCEPTIONS:
        return FailureClass.PERMANENT
    if name in _TRANSIENT_EXCEPTIONS:
        return FailureClass.TRANSIENT
    return FailureClass.TRANSIENT


# --- exceptions the wrapper raises ----------------------------------------------


class GatewayError(Exception):
    """Base class for gateway/wrapper errors."""


class TaskFailed(GatewayError):
    """A task reached FAILED status. Carries the classified reason."""

    def __init__(
        self,
        task_id: str,
        *,
        failure: str | None,
        failure_code: str | None,
        failure_class: FailureClass,
    ) -> None:
        self.task_id = task_id
        self.failure = failure
        self.failure_code = failure_code
        self.failure_class = failure_class
        super().__init__(
            f"task {task_id} FAILED ({failure_class.value}): "
            f"{failure_code or 'None'} - {failure or 'no detail'}"
        )


class TaskCancelled(GatewayError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"task {task_id} was CANCELLED")


class TaskTimeout(GatewayError):
    """A task did not settle within the allowed window."""

    failure_class: Final[FailureClass] = FailureClass.TIMEOUT

    def __init__(self, task_id: str, *, waited_seconds: float) -> None:
        self.task_id = task_id
        self.waited_seconds = waited_seconds
        super().__init__(f"task {task_id} did not settle within {waited_seconds:.0f}s")
