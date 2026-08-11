"""Declarative storyboard: JSON in, validated dataclasses out.

Validation happens at load, before any generation - an unknown character tag
referenced in a shot prompt fails here, not after forty generations. JSON (not
YAML) keeps the film package stdlib-only; ``load`` is format-agnostic so YAML can
slot in behind it later. See docs/design.md §4.1.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Runway reference-tag rule: 3-16 chars, starts with a letter.
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,15}$")
# @mentions inside a prompt.
_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]*)")


class StoryboardError(ValueError):
    """Raised for any invalid storyboard, with a message that names the offender."""


@dataclass(frozen=True)
class Character:
    tag: str
    description: str
    reference_image: str | None = None


@dataclass(frozen=True)
class Shot:
    id: str
    prompt: str
    character_tags: tuple[str, ...]
    duration_seconds: int
    motion: str | None = None
    keyframe_model: str = "gen4_image"
    clip_model: str = "gen4_turbo"

    @property
    def clip_prompt(self) -> str:
        return self.motion or self.prompt


@dataclass(frozen=True)
class Storyboard:
    title: str
    ratio: str
    characters: Mapping[str, Character]
    shots: tuple[Shot, ...]
    look: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> Storyboard:
        raw = Path(path).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoryboardError(f"invalid JSON: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Any) -> Storyboard:
        if not isinstance(data, Mapping):
            raise StoryboardError("storyboard must be a JSON object")

        title = _require_str(data, "title")
        ratio = _require_str(data, "ratio")
        look = data.get("look")
        if look is not None and not isinstance(look, str):
            raise StoryboardError("'look' must be a string if present")

        characters = _parse_characters(data.get("characters"))
        shots = _parse_shots(data.get("shots"), characters)
        return cls(title=title, ratio=ratio, characters=characters, shots=shots, look=look)


def _require_str(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise StoryboardError(f"'{field}' is required and must be a non-empty string")
    return value


def _parse_characters(raw: Any) -> dict[str, Character]:
    if not isinstance(raw, Mapping) or not raw:
        raise StoryboardError("'characters' must be a non-empty object")
    characters: dict[str, Character] = {}
    for tag, body in raw.items():
        if not _TAG_RE.match(str(tag)):
            raise StoryboardError(
                f"character tag {tag!r} is invalid (must be 3-16 chars, start with a letter)"
            )
        if not isinstance(body, Mapping):
            raise StoryboardError(f"character {tag!r} must be an object")
        description = body.get("description")
        if not isinstance(description, str) or not description.strip():
            raise StoryboardError(f"character {tag!r} needs a non-empty 'description'")
        reference_image = body.get("reference_image")
        if reference_image is not None and not isinstance(reference_image, str):
            raise StoryboardError(f"character {tag!r} 'reference_image' must be a string")
        characters[str(tag)] = Character(str(tag), description, reference_image)
    return characters


def _parse_shots(raw: Any, characters: Mapping[str, Character]) -> tuple[Shot, ...]:
    if not isinstance(raw, list) or not raw:
        raise StoryboardError("'shots' must be a non-empty array")
    shots: list[Shot] = []
    seen_ids: set[str] = set()
    for i, body in enumerate(raw):
        if not isinstance(body, Mapping):
            raise StoryboardError(f"shot #{i} must be an object")
        shot_id = body.get("id")
        if not isinstance(shot_id, str) or not shot_id.strip():
            raise StoryboardError(f"shot #{i} needs a non-empty 'id'")
        if shot_id in seen_ids:
            raise StoryboardError(f"duplicate shot id {shot_id!r}")
        seen_ids.add(shot_id)

        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise StoryboardError(f"shot {shot_id!r} needs a non-empty 'prompt'")

        duration = body.get("duration")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise StoryboardError(f"shot {shot_id!r} 'duration' must be a positive integer")

        character_tags = tuple(str(t) for t in body.get("characters", []))
        motion = body.get("motion")
        if motion is not None and not isinstance(motion, str):
            raise StoryboardError(f"shot {shot_id!r} 'motion' must be a string")

        # Every declared tag AND every @mention in the prompt must be defined.
        for tag in character_tags:
            if tag not in characters:
                raise StoryboardError(
                    f"shot {shot_id!r} references undefined character {tag!r}"
                )
        for mention in _MENTION_RE.findall(prompt):
            if mention not in characters:
                raise StoryboardError(
                    f"shot {shot_id!r} prompt references undefined character @{mention}"
                )

        shot = Shot(
            id=shot_id,
            prompt=prompt,
            character_tags=character_tags,
            duration_seconds=duration,
            motion=motion,
            keyframe_model=str(body.get("keyframe_model", "gen4_image")),
            clip_model=str(body.get("clip_model", "gen4_turbo")),
        )
        shots.append(shot)
    return tuple(shots)
