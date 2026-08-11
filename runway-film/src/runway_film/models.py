"""Film-domain value types."""

from __future__ import annotations

from dataclasses import dataclass, field

from runway_gateway.core import ArtifactRef, GenerationError


@dataclass(frozen=True)
class Clip:
    shot_id: str
    ref: ArtifactRef


@dataclass(frozen=True)
class Audio:
    shot_id: str
    ref: ArtifactRef


@dataclass(frozen=True)
class LocalVideo:
    path: str


@dataclass(frozen=True)
class GradedVideo:
    """Result of the grade stage. ``graded=False`` means the ungraded cut is
    returned (grade failed) - work is never lost."""

    video: LocalVideo
    graded: bool
    warning: str | None = None


@dataclass(frozen=True)
class FilmResult:
    keyframes_by_shot: dict[str, ArtifactRef] = field(default_factory=dict)
    clips: tuple[Clip, ...] = ()
    errors: tuple[GenerationError, ...] = ()
    estimated_credits: float = 0.0
