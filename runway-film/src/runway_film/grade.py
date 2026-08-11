"""Grade stage: one aleph2 video_to_video pass over the stitched cut.

Independently generated shots drift in colour and lighting; one Aleph pass with a
single look note is what makes the sequence read as one film. This MUST degrade
gracefully: if the grade fails, return the ungraded cut with a warning - never lose
the assembled work.

Pricing caveat: ``aleph2`` has no published per-second price (api-surface §Unverified).
If the gateway has a Budget, register an aleph2 price override first, or the budget
will (correctly) refuse the call as unpriced.
"""

from __future__ import annotations

import logging

from runway_gateway.api.failures import TaskCancelled, TaskFailed, TaskTimeout
from runway_gateway.api.pricing import UnknownModelPricing
from runway_gateway.core import Gateway

from .models import GradedVideo, LocalVideo

logger = logging.getLogger("runway_film.grade")


def grade(gw: Gateway, cut: LocalVideo, look: str | None) -> GradedVideo:
    """Upload the cut and run one aleph2 pass with ``look`` as the note.

    On any generation failure (moderation/permanent/transient-exhausted/timeout) or
    unpriced-model refusal, log a warning and return the ungraded cut.
    """
    if look is None:
        return GradedVideo(video=cut, graded=False, warning="no look note; skipped grade")
    try:
        uploaded = gw.upload(cut.path)  # local bytes -> runway:// (durability boundary)
        gen = gw.generate("video_to_video", kind="video", model="aleph2",
                          video_uri=uploaded, prompt_text=look)
        return GradedVideo(video=LocalVideo(path=gen.output_urls[0]), graded=True)
    except (TaskFailed, TaskCancelled, TaskTimeout, UnknownModelPricing) as exc:
        logger.warning("grade_failed", extra={"error": str(exc)})
        return GradedVideo(video=cut, graded=False, warning=f"grade failed: {exc}")
