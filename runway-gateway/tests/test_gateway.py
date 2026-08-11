from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from runway_gateway.api import RunwayAPI
from runway_gateway.api.failures import TaskFailed
from runway_gateway.api.fakes import Behavior, FakeRunwayClient
from runway_gateway.core import (
    ArtifactRef,
    Budget,
    BudgetExceeded,
    Gateway,
    GenerateCall,
    GenerationError,
    InMemoryCache,
    LocalArtifactStore,
    RetryPolicy,
    SqliteCache,
    compute_cache_key,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def make_gateway(
    client: FakeRunwayClient,
    *,
    cache: Any = None,
    budget: Budget | None = None,
    store: Any = None,
) -> Gateway:
    clock = FakeClock()
    api = RunwayAPI(client, clock=clock.now, sleep=clock.sleep)
    return Gateway(
        api,
        cache if cache is not None else InMemoryCache(),
        budget=budget,
        store=store,
        retry=RetryPolicy(max_attempts=4, base_delay=0.0, jitter=0.0),
        poll_interval=0.0,
        sleep=lambda _s: None,
        rand=lambda: 0.0,
    )


def _run_two_stage_chain(gw: Gateway) -> None:
    """text_to_image -> image_to_video (child depends on parent via ArtifactRef)."""
    keyframe = gw.generate("text_to_image", model="gen4_image", prompt_text="a hero", ratio="1:1")
    gw.generate(
        "image_to_video",
        model="gen4_turbo",
        duration=5,
        prompt_image=keyframe.ref,
        depends_on=(keyframe.ref,),
    )


def test_second_run_of_identical_chain_makes_zero_calls() -> None:
    client = FakeRunwayClient()
    cache = InMemoryCache()
    gw = make_gateway(client, cache=cache)

    _run_two_stage_chain(gw)
    after_first = client.create_count
    assert after_first == 2  # keyframe + clip

    _run_two_stage_chain(gw)
    assert client.create_count == after_first  # zero new API calls on re-run


def test_changing_parent_regenerates_it_and_descendants_only() -> None:
    client = FakeRunwayClient()
    cache = InMemoryCache()
    gw = make_gateway(client, cache=cache)

    # An independent stage that must NOT regenerate when the chain's parent changes.
    gw.generate("text_to_image", model="gen4_image", prompt_text="independent", ratio="1:1")
    keyframe = gw.generate("text_to_image", model="gen4_image", prompt_text="v1", ratio="1:1")
    gw.generate("image_to_video", model="gen4_turbo", duration=5, prompt_image=keyframe.ref,
                depends_on=(keyframe.ref,))
    baseline = client.create_count
    assert baseline == 3

    # Change the parent keyframe's prompt: parent + its clip regenerate (2), the
    # independent stage stays cached (0).
    keyframe2 = gw.generate("text_to_image", model="gen4_image", prompt_text="v2", ratio="1:1")
    gw.generate("image_to_video", model="gen4_turbo", duration=5, prompt_image=keyframe2.ref,
                depends_on=(keyframe2.ref,))
    assert client.create_count == baseline + 2


def test_rotated_upstream_url_does_not_bust_cache() -> None:
    # Same cache_key, different (rotated) URL -> identical downstream key.
    r1 = ArtifactRef(cache_key="k1", url="https://signed/one", kind="image")
    r2 = ArtifactRef(cache_key="k1", url="https://signed/two-rotated", kind="image")
    k1 = compute_cache_key("image_to_video", {"prompt_image": r1}, upstream=["k1"])
    k2 = compute_cache_key("image_to_video", {"prompt_image": r2}, upstream=["k1"])
    assert k1 == k2


def test_moderation_failure_raises_immediately_without_retry() -> None:
    client = FakeRunwayClient(default_behavior=Behavior.FAIL_MODERATION)
    gw = make_gateway(client)
    with pytest.raises(TaskFailed):
        gw.generate("text_to_image", model="gen4_image", prompt_text="x", ratio="1:1")
    assert client.create_count == 1  # submitted once, never retried


def test_429_then_success_retries_and_returns() -> None:
    client = FakeRunwayClient(transient_creates=1)
    gw = make_gateway(client)
    gen = gw.generate("text_to_image", model="gen4_image", prompt_text="x", ratio="1:1")
    assert gen.output_urls  # succeeded after retry
    assert client.create_count == 1  # the one successful create
    assert len(client.calls) == 2  # first (429) + retry


def test_budget_ceiling_raises_before_any_call() -> None:
    client = FakeRunwayClient()
    budget = Budget(ceiling_credits=1.0)  # gen4_image costs 5
    gw = make_gateway(client, budget=budget)
    with pytest.raises(BudgetExceeded):
        gw.generate("text_to_image", model="gen4_image", prompt_text="x", ratio="1:1")
    assert client.create_count == 0


def test_one_failure_in_fanout_of_five_leaves_four_intact() -> None:
    def selector(endpoint: str, params: Mapping[str, Any]) -> Behavior:
        return Behavior.FAIL_PERMANENT if params.get("prompt_text") == "bad" else Behavior.SUCCEED

    client = FakeRunwayClient(behavior_selector=selector)
    gw = make_gateway(client)
    calls = [
        GenerateCall(
            "text_to_image",
            {"model": "gen4_image", "prompt_text": ("bad" if i == 2 else f"p{i}"), "ratio": "1:1"},
        )
        for i in range(5)
    ]
    results = gw.map(calls, max_workers=3)

    assert len(results) == 5
    assert isinstance(results[2], GenerationError)
    successes = [r for r in results if not isinstance(r, GenerationError)]
    assert len(successes) == 4  # already-paid-for successes are kept


def test_artifact_durability_persists_output(tmp_path: Any) -> None:
    # A data: URL makes persistence testable offline. base64 "aGVsbG8=" == "hello".
    data_url = "data:text/plain;base64,aGVsbG8="
    client = FakeRunwayClient(output=(data_url,))
    store = LocalArtifactStore(tmp_path)
    gw = make_gateway(client, store=store)
    gen = gw.generate("text_to_image", model="gen4_image", prompt_text="x", ratio="1:1")
    assert gen.artifact_paths
    from pathlib import Path

    persisted = Path(gen.artifact_paths[0])
    assert persisted.exists()
    assert persisted.read_bytes() == b"hello"


def test_sqlite_cache_round_trips(tmp_path: Any) -> None:
    client = FakeRunwayClient()
    cache = SqliteCache(tmp_path / "c.sqlite")
    gw = make_gateway(client, cache=cache)
    _run_two_stage_chain(gw)
    assert client.create_count == 2
    _run_two_stage_chain(gw)
    assert client.create_count == 2  # served from SQLite on re-run
