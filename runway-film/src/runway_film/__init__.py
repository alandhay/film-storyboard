"""runway-film: a storyboard-to-film pipeline on top of runway-gateway.

Contains storyboards, character bibles, the approval gate, and assembly. Zero HTTP,
zero retry, zero cache logic - all of that lives in runway-gateway.
"""

from __future__ import annotations

from .gate import ApprovalError, ApprovedKeyframe, Keyframe, approve
from .models import Audio, Clip, FilmResult, GradedVideo, LocalVideo
from .pipeline import (
    CostLine,
    animate,
    build_character_bible,
    generate_clips,
    generate_keyframes,
    plan_cost,
    run_film,
    run_keyframes,
)
from .storyboard import Character, Shot, Storyboard, StoryboardError

__version__ = "0.1.0"

__all__ = [
    "ApprovalError",
    "ApprovedKeyframe",
    "Audio",
    "Character",
    "Clip",
    "CostLine",
    "FilmResult",
    "GradedVideo",
    "Keyframe",
    "LocalVideo",
    "Shot",
    "Storyboard",
    "StoryboardError",
    "animate",
    "approve",
    "build_character_bible",
    "generate_clips",
    "generate_keyframes",
    "plan_cost",
    "run_film",
    "run_keyframes",
]
