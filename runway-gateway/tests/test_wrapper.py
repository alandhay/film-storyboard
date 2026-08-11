from __future__ import annotations

import pytest

from runway_gateway.api import (
    GENERATION_ENDPOINTS,
    RECIPE_ENDPOINTS,
    FailureClass,
    RunwayAPI,
    TaskCancelled,
    TaskFailed,
    TaskState,
    TaskTimeout,
)
from runway_gateway.api.fakes import Behavior, FakeRunwayClient


class FakeClock:
    """Deterministic clock; ``sleep`` advances time so poll loops terminate."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def make_api(client: FakeRunwayClient) -> tuple[RunwayAPI, FakeClock]:
    clock = FakeClock()
    return RunwayAPI(client, clock=clock.now, sleep=clock.sleep), clock


def test_submit_returns_handle_with_id_and_params() -> None:
    client = FakeRunwayClient()
    api, _ = make_api(client)
    handle = api.submit("text_to_image", model="gen4_image", prompt_text="a cat", ratio="1:1")
    assert handle.task_id == "task-1"
    assert handle.endpoint == "text_to_image"
    assert handle.model == "gen4_image"
    assert handle.params["prompt_text"] == "a cat"
    assert client.create_count == 1


def test_submit_and_wait_success_returns_output() -> None:
    client = FakeRunwayClient(output=("https://fake.runway/img.png",))
    api, _ = make_api(client)
    result = api.submit_and_wait("text_to_image", model="gen4_image", prompt_text="x", ratio="1:1")
    assert result.status is TaskState.SUCCEEDED
    assert result.output == ("https://fake.runway/img.png",)


def test_wait_polls_until_settled() -> None:
    client = FakeRunwayClient(running_polls=2)
    api, _ = make_api(client)
    handle = api.submit("image_to_video", model="gen4_turbo", duration=5)
    result = api.wait(handle, poll_interval=1.0)
    assert result.status is TaskState.SUCCEEDED
    # 2 RUNNING polls + 1 SUCCEEDED
    assert client.retrieve_count == 3


def test_moderation_failure_raises_with_class() -> None:
    client = FakeRunwayClient(default_behavior=Behavior.FAIL_MODERATION)
    api, _ = make_api(client)
    handle = api.submit("text_to_image", model="gen4_image", prompt_text="nope", ratio="1:1")
    with pytest.raises(TaskFailed) as excinfo:
        api.wait(handle)
    assert excinfo.value.failure_class is FailureClass.MODERATION
    assert excinfo.value.failure_code == "SAFETY.INPUT.TEXT"


def test_permanent_failure_raises_with_class() -> None:
    client = FakeRunwayClient(default_behavior=Behavior.FAIL_PERMANENT)
    api, _ = make_api(client)
    handle = api.submit("image_to_video", model="gen4_turbo", duration=5)
    with pytest.raises(TaskFailed) as excinfo:
        api.wait(handle)
    assert excinfo.value.failure_class is FailureClass.PERMANENT


def test_hang_times_out() -> None:
    client = FakeRunwayClient(default_behavior=Behavior.HANG)
    api, _ = make_api(client)
    handle = api.submit("image_to_video", model="veo3.1", duration=8)
    with pytest.raises(TaskTimeout):
        api.wait(handle, timeout=10.0, poll_interval=2.0)


def test_cancelled_task_raises() -> None:
    client = FakeRunwayClient(default_behavior=Behavior.CANCELLED)
    api, _ = make_api(client)
    handle = api.submit("text_to_image", model="gen4_image", prompt_text="x", ratio="1:1")
    with pytest.raises(TaskCancelled):
        api.wait(handle)


def test_submit_rejects_non_generation_endpoint() -> None:
    api, _ = make_api(FakeRunwayClient())
    with pytest.raises(ValueError, match="not a generation endpoint"):
        api.submit("multi_shot_video")


def test_every_generation_endpoint_is_callable() -> None:
    client = FakeRunwayClient()
    api, _ = make_api(client)
    for endpoint in sorted(GENERATION_ENDPOINTS):
        handle = api.submit(endpoint, model="m", duration=5)
        assert handle.endpoint == endpoint
    assert client.create_count == len(GENERATION_ENDPOINTS)


def test_recipes_pass_through() -> None:
    client = FakeRunwayClient()
    api, _ = make_api(client)
    for name in sorted(RECIPE_ENDPOINTS):
        out = api.recipe(name, prompt="hi")
        assert out["id"] == f"recipe-{name}"
    with pytest.raises(ValueError, match="not a recipe"):
        api.recipe("text_to_image")


def test_upload_returns_runway_uri() -> None:
    client = FakeRunwayClient()
    api, _ = make_api(client)
    uri = api.upload(file="/tmp/cut.mp4")
    assert uri.startswith("runway://")
    assert client.upload_count == 1


def test_workflows_and_org_pass_through() -> None:
    api, _ = make_api(FakeRunwayClient())
    assert api.list_workflows() == [{"id": "workflow-1"}]
    assert api.run_workflow("wf1", input="x")["status"] == "PENDING"
    assert api.retrieve_workflow_invocation("i1")["status"] == "SUCCEEDED"
    assert "creditBalance" in api.retrieve_organization()


def test_estimate_cost_does_not_submit() -> None:
    client = FakeRunwayClient()
    api, _ = make_api(client)
    est = api.estimate_cost("image_to_video", model="gen4_turbo", duration=6)
    assert est.credits == 30.0
    assert client.create_count == 0
