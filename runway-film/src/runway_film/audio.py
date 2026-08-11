"""Audio stage - STUB.

Phase 0 resolved the parameters (both TTS variants and sound_effect use
``prompt_text``; TTS voice is either ``voice.preset_id`` for eleven_multilingual_v2
or ``voice.audio_uri`` for seed_audio cloning). What is NOT in Phase 2 scope is
per-shot dialogue/score authoring, so this stage is deliberately unimplemented
rather than confidently wrong. See docs/api-surface.md §4-5 and docs/design.md §5.
"""

from __future__ import annotations

from collections.abc import Sequence

from runway_gateway.core import Gateway

from .models import Audio, Clip
from .storyboard import Storyboard


def generate_audio(gw: Gateway, sb: Storyboard, clips: Sequence[Clip]) -> list[Audio]:
    raise NotImplementedError(
        "audio stage is stubbed for Phase 2. Params are known "
        "(text_to_speech/sound_effect use prompt_text; voice via preset_id or "
        "audio_uri) but per-shot dialogue authoring is out of scope. Wire "
        "gw.generate('text_to_speech', ...) per shot here."
    )
