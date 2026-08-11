"""An injectable fake RunwayML client. No network, deterministic, call-counted.

Implements the same structural surface as the real SDK (``RunwayClient``
protocol) with injectable behaviour: succeed, fail-moderation, fail-permanent,
fail-transient (task-level), 429-then-succeed (transport-level), and hang. This is
the single fake used by both the wrapper tests and the Phase 2 gateway tests -
tests assert on ``create_count`` / ``retrieve_count`` to prove caching and retry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Behavior(Enum):
    SUCCEED = "succeed"
    FAIL_MODERATION = "fail_moderation"  # task FAILED, code SAFETY.INPUT.TEXT
    FAIL_PERMANENT = "fail_permanent"  # task FAILED, code ASSET.INVALID
    FAIL_TRANSIENT = "fail_transient"  # task FAILED, code INTERNAL
    CANCELLED = "cancelled"  # task reaches CANCELLED
    HANG = "hang"  # task never leaves RUNNING


class FakeAPIStatusError(Exception):
    """Mimics ``runwayml.APIStatusError`` (carries ``status_code``)."""

    def __init__(self, status_code: int, message: str = "fake api error") -> None:
        self.status_code = status_code
        super().__init__(f"[{status_code}] {message}")


_FAILURE_CODES: dict[Behavior, str] = {
    Behavior.FAIL_MODERATION: "SAFETY.INPUT.TEXT",
    Behavior.FAIL_PERMANENT: "ASSET.INVALID",
    Behavior.FAIL_TRANSIENT: "INTERNAL",
}


@dataclass
class _FakeTask:
    """Duck-types an SDK ``TaskRetrieveResponse`` union member."""

    id: str
    status: str
    output: list[str] | None = None
    failure: str | None = None
    failure_code: str | None = None
    progress: float | None = None


@dataclass
class _FakeCreateResponse:
    id: str


@dataclass
class _FakeUploadResponse:
    uri: str


@dataclass
class _TaskRecord:
    behavior: Behavior
    polls: int = 0


BehaviorSelector = Callable[[str, Mapping[str, Any]], Behavior]


class FakeRunwayClient:
    """Configurable fake. See module docstring.

    Parameters
    ----------
    default_behavior:
        Behaviour for every submitted task unless overridden by ``behavior_selector``.
    behavior_selector:
        ``(endpoint, params) -> Behavior`` to route per-call (e.g. route a specific
        prompt to moderation for the fan-out test).
    output:
        Output URLs returned on SUCCEEDED.
    running_polls:
        Number of RUNNING responses before a task settles (0 = settle on first poll).
    transient_creates:
        Number of times ``create`` raises a 429 before succeeding (transport retry).
    """

    def __init__(
        self,
        *,
        default_behavior: Behavior = Behavior.SUCCEED,
        behavior_selector: BehaviorSelector | None = None,
        output: tuple[str, ...] | None = None,
        running_polls: int = 0,
        transient_creates: int = 0,
    ) -> None:
        self._default_behavior = default_behavior
        self._behavior_selector = behavior_selector
        # None => generate a distinct URL per task (realistic; lets callers tell
        # stages apart). A provided tuple is returned verbatim for every task.
        self._output = list(output) if output is not None else None
        self._running_polls = running_polls
        self._transient_creates = transient_creates

        # observability for assertions
        self.create_count = 0
        self.retrieve_count = 0
        self.upload_count = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

        self._tasks: dict[str, _TaskRecord] = {}
        self._task_seq = 0
        self._upload_seq = 0
        self._transient_seen = 0

        # resource facades (attribute names match the SDK surface)
        self.text_to_image = _CreateFacade(self, "text_to_image")
        self.image_to_video = _CreateFacade(self, "image_to_video")
        self.video_to_video = _CreateFacade(self, "video_to_video")
        self.text_to_speech = _CreateFacade(self, "text_to_speech")
        self.sound_effect = _CreateFacade(self, "sound_effect")
        self.video_upscale = _CreateFacade(self, "video_upscale")
        self.tasks = _TasksFacade(self)
        self.uploads = _UploadsFacade(self)
        self.recipes = _RecipesFacade(self)
        self.workflows = _WorkflowsFacade(self)
        self.workflow_invocations = _WorkflowInvocationsFacade(self)
        self.organization = _OrganizationFacade(self)

    # --- internals used by the facades ------------------------------------------

    def _create(self, endpoint: str, params: Mapping[str, Any]) -> _FakeCreateResponse:
        self.calls.append((endpoint, dict(params)))
        if self._transient_creates > self._transient_seen:
            self._transient_seen += 1
            raise FakeAPIStatusError(429, "rate limited (fake)")
        self.create_count += 1
        self._task_seq += 1
        task_id = f"task-{self._task_seq}"
        behavior = (
            self._behavior_selector(endpoint, params)
            if self._behavior_selector is not None
            else self._default_behavior
        )
        self._tasks[task_id] = _TaskRecord(behavior=behavior)
        return _FakeCreateResponse(id=task_id)

    def _retrieve(self, task_id: str) -> _FakeTask:
        self.retrieve_count += 1
        record = self._tasks.get(task_id)
        if record is None:
            raise FakeAPIStatusError(404, f"unknown task {task_id}")
        record.polls += 1
        if record.behavior is Behavior.HANG:
            return _FakeTask(id=task_id, status="RUNNING", progress=0.5)
        if record.polls <= self._running_polls:
            return _FakeTask(id=task_id, status="RUNNING", progress=0.5)
        if record.behavior is Behavior.SUCCEED:
            output = list(self._output) if self._output is not None else [
                f"https://fake.runway/{task_id}.out"
            ]
            return _FakeTask(id=task_id, status="SUCCEEDED", output=output)
        if record.behavior is Behavior.CANCELLED:
            return _FakeTask(id=task_id, status="CANCELLED")
        code = _FAILURE_CODES[record.behavior]
        return _FakeTask(
            id=task_id, status="FAILED", failure=f"fake {record.behavior.value}", failure_code=code
        )


@dataclass
class _CreateFacade:
    client: FakeRunwayClient
    endpoint: str

    def create(self, **params: Any) -> _FakeCreateResponse:
        return self.client._create(self.endpoint, params)


@dataclass
class _TasksFacade:
    client: FakeRunwayClient

    def retrieve(self, id: str) -> _FakeTask:
        return self.client._retrieve(id)

    def delete(self, id: str) -> None:
        self.client._tasks.pop(id, None)


@dataclass
class _UploadsFacade:
    client: FakeRunwayClient

    def create_ephemeral(self, *, file: Any) -> _FakeUploadResponse:
        self.client.upload_count += 1
        self.client._upload_seq += 1
        return _FakeUploadResponse(uri=f"runway://fake-upload-{self.client._upload_seq}")


@dataclass
class _RecipesFacade:
    client: FakeRunwayClient

    def _run(self, name: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.client.calls.append((f"recipe:{name}", dict(params)))
        return {"id": f"recipe-{name}", "status": "PENDING"}

    def ad_localization(self, **params: Any) -> dict[str, Any]:
        return self._run("ad_localization", params)

    def marketing_stock_image(self, **params: Any) -> dict[str, Any]:
        return self._run("marketing_stock_image", params)

    def multi_shot_video(self, **params: Any) -> dict[str, Any]:
        return self._run("multi_shot_video", params)

    def product_ad(self, **params: Any) -> dict[str, Any]:
        return self._run("product_ad", params)

    def product_campaign_image(self, **params: Any) -> dict[str, Any]:
        return self._run("product_campaign_image", params)

    def product_swap(self, **params: Any) -> dict[str, Any]:
        return self._run("product_swap", params)

    def product_ugc(self, **params: Any) -> dict[str, Any]:
        return self._run("product_ugc", params)


@dataclass
class _WorkflowsFacade:
    client: FakeRunwayClient

    def retrieve(self, id: str) -> dict[str, Any]:
        return {"id": id, "status": "ACTIVE"}

    def list(self) -> list[dict[str, Any]]:
        return [{"id": "workflow-1"}]

    def run(self, id: str, **params: Any) -> dict[str, Any]:
        self.client.calls.append((f"workflow:{id}", dict(params)))
        return {"id": f"invocation-{id}", "status": "PENDING"}


@dataclass
class _WorkflowInvocationsFacade:
    client: FakeRunwayClient

    def retrieve(self, id: str) -> dict[str, Any]:
        return {"id": id, "status": "SUCCEEDED"}


@dataclass
class _OrganizationFacade:
    client: FakeRunwayClient

    def retrieve(self) -> dict[str, Any]:
        return {"creditBalance": 100000}

    def retrieve_usage(self, **params: Any) -> dict[str, Any]:
        return {"results": [], "params": dict(params)}
