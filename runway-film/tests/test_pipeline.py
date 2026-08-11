from __future__ import annotations

from runway_gateway.api.fakes import FakeRunwayClient
from runway_gateway.api.pricing import default_pricing_book
from runway_gateway.core import Gateway

from conftest import storyboard_dict
from runway_film import Storyboard, plan_cost, run_film


def _approve_all(sb: Storyboard) -> set[str]:
    return {shot.id for shot in sb.shots}


def test_second_run_of_identical_film_makes_zero_calls(
    gateway: Gateway, fake_client: FakeRunwayClient, storyboard: Storyboard
) -> None:
    run_film(gateway, storyboard, _approve_all(storyboard))
    first = fake_client.create_count
    # 2 characters (bible) + 3 keyframes + 3 clips
    assert first == 8

    run_film(gateway, storyboard, _approve_all(storyboard))
    assert fake_client.create_count == first  # fully cached on re-run


def test_changing_one_shot_regenerates_that_shot_and_its_clip_only(
    gateway: Gateway, fake_client: FakeRunwayClient
) -> None:
    sb1 = Storyboard.from_dict(storyboard_dict())
    run_film(gateway, sb1, _approve_all(sb1))
    baseline = fake_client.create_count
    assert baseline == 8

    # Change only shot s2's prompt. Bible (same characters) and s1/s3 keyframes and
    # clips stay cached; only s2's keyframe + s2's clip regenerate.
    sb2 = Storyboard.from_dict(storyboard_dict(s2_prompt="@kade slams the transmit switch"))
    run_film(gateway, sb2, _approve_all(sb2))
    assert fake_client.create_count == baseline + 2


def test_only_approved_shots_are_animated(
    gateway: Gateway, fake_client: FakeRunwayClient, storyboard: Storyboard
) -> None:
    # Approve just s1: bible(2) + keyframes(3) + 1 clip = 6 creates.
    result = run_film(gateway, storyboard, {"s1"})
    assert len(result.clips) == 1
    assert result.clips[0].shot_id == "s1"
    assert fake_client.create_count == 6


def test_plan_cost_lists_every_stage_without_spending(storyboard: Storyboard) -> None:
    lines = plan_cost(default_pricing_book(), storyboard)
    # 2 bible + 3 keyframes + 3 clips = 8 lines
    assert len(lines) == 8
    priced = [line for line in lines if line.credits is not None]
    assert len(priced) == 8  # gen4_image / gen4_turbo are all priced
