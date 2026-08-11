"""The approval gate as a type, not a boolean.

Stills are cheap and fast; video is expensive and slow. An unapproved shot must
not be able to reach ``image_to_video``. That's enforced by construction here: only
``approve()`` can mint an ``ApprovedKeyframe``, and ``animate`` (the only caller of
image_to_video) takes an ``ApprovedKeyframe``. Passing a raw ``Keyframe`` is a type
error at the call site and a runtime error if attempted dynamically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from runway_gateway.core import ArtifactRef

# A process-unique token that only this module holds; an ApprovedKeyframe is only
# valid if constructed with it, which only approve() does.
_APPROVAL_TOKEN = object()


class ApprovalError(PermissionError):
    """Raised if an ApprovedKeyframe is fabricated without going through approve()."""


@dataclass(frozen=True)
class Keyframe:
    """A generated still for a shot. Cheap. Not yet cleared to animate."""

    shot_id: str
    ref: ArtifactRef


@dataclass(frozen=True)
class ApprovedKeyframe:
    """A keyframe a human has approved for animation. Constructible ONLY via
    :func:`approve` - the ``_token`` guard makes direct construction fail."""

    shot_id: str
    ref: ArtifactRef
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _APPROVAL_TOKEN:
            raise ApprovalError(
                "ApprovedKeyframe cannot be constructed directly; use approve()"
            )


def approve(keyframe: Keyframe) -> ApprovedKeyframe:
    """The one and only factory for an ApprovedKeyframe."""
    return ApprovedKeyframe(shot_id=keyframe.shot_id, ref=keyframe.ref, _token=_APPROVAL_TOKEN)
