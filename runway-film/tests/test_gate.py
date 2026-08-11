from __future__ import annotations

import pytest
from runway_gateway.core import ArtifactRef, Gateway

from runway_film import Storyboard
from runway_film.gate import ApprovalError, ApprovedKeyframe, Keyframe, approve
from runway_film.pipeline import animate


def _keyframe() -> Keyframe:
    return Keyframe(shot_id="s1", ref=ArtifactRef(cache_key="k", url="https://x", kind="image"))


def test_unapproved_keyframe_cannot_be_animated(gateway: Gateway, storyboard: Storyboard) -> None:
    shot = storyboard.shots[0]
    kf = _keyframe()
    with pytest.raises(TypeError):
        animate(gateway, storyboard, shot, kf)  # type: ignore[arg-type]


def test_approved_keyframe_animates(gateway: Gateway, storyboard: Storyboard) -> None:
    shot = storyboard.shots[0]
    approved = approve(_keyframe())
    clip = animate(gateway, storyboard, shot, approved)
    assert clip.shot_id == "s1"
    assert clip.ref.kind == "video"


def test_approved_keyframe_cannot_be_fabricated() -> None:
    kf = _keyframe()
    with pytest.raises(ApprovalError):
        ApprovedKeyframe(shot_id=kf.shot_id, ref=kf.ref, _token=object())


def test_only_approve_produces_approved_keyframe() -> None:
    approved = approve(_keyframe())
    assert isinstance(approved, ApprovedKeyframe)
