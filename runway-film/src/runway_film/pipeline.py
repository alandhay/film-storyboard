"""Stage orchestration: character bible -> keyframes -> [gate] -> clips.

This module contains the film's model choices and prompt assembly. It calls
``gateway.generate`` / ``gateway.map`` and never touches HTTP, cache, or retry.
Audio, assembly, and grade live in their own modules (audio.py is a stub).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from runway_gateway.api.pricing import (
    MissingCostParam,
    PricingBook,
    UnknownModelPricing,
)
from runway_gateway.core import ArtifactRef, Gateway, GenerateCall, GenerationError

from .gate import ApprovedKeyframe, Keyframe, approve
from .models import Clip, FilmResult
from .storyboard import Shot, Storyboard


def build_character_bible(gw: Gateway, sb: Storyboard) -> dict[str, ArtifactRef]:
    """One reference still per character (text_to_image), cached and reusable."""
    bible: dict[str, ArtifactRef] = {}
    for tag, character in sb.characters.items():
        params: dict[str, Any] = {
            "model": "gen4_image",
            "prompt_text": character.description,
            "ratio": sb.ratio,
        }
        if character.reference_image is not None:
            params["reference_images"] = [{"uri": character.reference_image, "tag": tag}]
        bible[tag] = gw.generate("text_to_image", kind="image", **params).ref
    return bible


def generate_keyframes(
    gw: Gateway, sb: Storyboard, bible: Mapping[str, ArtifactRef]
) -> list[Keyframe]:
    """A still per shot. Character bible refs are passed as tagged reference_images,
    so @tags in the prompt bind to consistent characters; depends_on = those refs."""
    keyframes: list[Keyframe] = []
    for shot in sb.shots:
        refs = tuple(bible[tag] for tag in shot.character_tags)
        params: dict[str, Any] = {
            "model": shot.keyframe_model,
            "prompt_text": shot.prompt,
            "ratio": shot.keyframe_ratio or sb.ratio,
        }
        if refs:
            params["reference_images"] = [
                {"uri": bible[tag], "tag": tag} for tag in shot.character_tags
            ]
        params.update(shot.keyframe_extra)  # model-specific extras (e.g. output_count)
        gen = gw.generate("text_to_image", depends_on=refs, kind="image", **params)
        keyframes.append(Keyframe(shot_id=shot.id, ref=gen.ref))
    return keyframes


def _clip_params(sb: Storyboard, shot: Shot, keyframe: ArtifactRef) -> dict[str, Any]:
    return {
        "model": shot.clip_model,
        "prompt_image": keyframe,
        "prompt_text": shot.clip_prompt,
        "duration": shot.duration_seconds,
        "ratio": sb.ratio,
    }


def animate(gw: Gateway, sb: Storyboard, shot: Shot, approved: ApprovedKeyframe) -> Clip:
    """Animate ONE approved keyframe (image_to_video). The ``ApprovedKeyframe`` type
    is the gate: a raw ``Keyframe`` cannot be passed here."""
    if not isinstance(approved, ApprovedKeyframe):
        raise TypeError("animate() requires an ApprovedKeyframe; approve the keyframe first")
    gen = gw.generate(
        "image_to_video",
        depends_on=(approved.ref,),
        kind="video",
        **_clip_params(sb, shot, approved.ref),
    )
    return Clip(shot_id=shot.id, ref=gen.ref)


def generate_clips(
    gw: Gateway, sb: Storyboard, approved: Sequence[ApprovedKeyframe], *, max_workers: int = 3
) -> tuple[list[Clip], list[GenerationError]]:
    """Fan out image_to_video across approved keyframes. One failure leaves the rest
    intact (gateway.map). The input type (ApprovedKeyframe) is the gate."""
    shots_by_id = {shot.id: shot for shot in sb.shots}
    calls = [
        GenerateCall(
            endpoint="image_to_video",
            params=_clip_params(sb, shots_by_id[ak.shot_id], ak.ref),
            depends_on=(ak.ref,),
            kind="video",
        )
        for ak in approved
    ]
    results = gw.map(calls, max_workers=max_workers)
    clips: list[Clip] = []
    errors: list[GenerationError] = []
    for ak, result in zip(approved, results, strict=True):
        if isinstance(result, GenerationError):
            errors.append(result)
        else:
            clips.append(Clip(shot_id=ak.shot_id, ref=result.ref))
    return clips, errors


def run_keyframes(gw: Gateway, sb: Storyboard) -> tuple[dict[str, ArtifactRef], list[Keyframe]]:
    bible = build_character_bible(gw, sb)
    return bible, generate_keyframes(gw, sb, bible)


def run_film(
    gw: Gateway, sb: Storyboard, approved_shot_ids: Collection[str]
) -> FilmResult:
    """Full pipeline up to clips. Only shots whose ids are in ``approved_shot_ids``
    are animated - the gate applied to a whole storyboard."""
    _bible, keyframes = run_keyframes(gw, sb)
    approved = [approve(kf) for kf in keyframes if kf.shot_id in approved_shot_ids]
    clips, errors = generate_clips(gw, sb, approved)
    return FilmResult(
        keyframes_by_shot={kf.shot_id: kf.ref for kf in keyframes},
        clips=tuple(clips),
        errors=tuple(errors),
    )


# --- dry-run cost planning ------------------------------------------------------


@dataclass(frozen=True)
class CostLine:
    stage: str
    endpoint: str
    model: str
    credits: float | None  # None = unpriced (surfaced, not hidden)
    detail: str


def plan_cost(pricing: PricingBook, sb: Storyboard) -> list[CostLine]:
    """Estimate every call the full pipeline WOULD make, without submitting any.

    Depends only on a PricingBook - so ``cost`` needs no API client and no key.
    """
    lines: list[CostLine] = []

    def add(stage: str, endpoint: str, model: str, **params: Any) -> None:
        try:
            est = pricing.estimate(endpoint, model, params)
            lines.append(CostLine(stage, endpoint, model, est.credits, est.detail))
        except (UnknownModelPricing, MissingCostParam) as exc:
            lines.append(CostLine(stage, endpoint, model, None, f"unpriced: {exc}"))

    for tag, character in sb.characters.items():
        add(f"bible:{tag}", "text_to_image", "gen4_image", prompt_text=character.description)
    for shot in sb.shots:
        add(f"keyframe:{shot.id}", "text_to_image", shot.keyframe_model, prompt_text=shot.prompt)
    for shot in sb.shots:
        add(
            f"clip:{shot.id}",
            "image_to_video",
            shot.clip_model,
            duration=shot.duration_seconds,
        )
    return lines
