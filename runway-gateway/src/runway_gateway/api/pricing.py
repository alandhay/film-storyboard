"""Cost estimation for the budget ceiling and the dry-run ``cost`` command.

Prices are from docs.dev.runwayml.com/guides/pricing (1 credit = $0.01), captured
in docs/api-surface.md. Two deliberate design choices:

* **Fail closed.** An unknown model raises ``UnknownModelPricing`` rather than
  costing zero. A budget check that silently under-counts is worse than one that
  refuses; ``aleph2`` (the grade stage) has *no published per-second price*, so it
  is genuinely unknown and must not be guessed.
* **Injectable.** ``PricingBook`` is passed into the wrapper, so a caller can
  register an override for a missing/updated price without editing this file.

Estimates are inherently approximate (image tiers, audio output-length) and are
documented as such per rule.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

CREDIT_USD: float = 0.01


class UnknownModelPricing(Exception):
    """No pricing rule for this model. Register one on the PricingBook to override."""

    def __init__(self, model: str | None) -> None:
        self.model = model
        super().__init__(
            f"no pricing rule for model {model!r}; register an override on the "
            f"PricingBook (its per-unit cost is unverified)"
        )


class MissingCostParam(Exception):
    """A param needed to estimate cost (e.g. ``duration``) was not supplied."""


@dataclass(frozen=True)
class CostEstimate:
    model: str | None
    credits: float
    usd: float
    detail: str


# A rule turns submitted params into (credits, human detail).
RuleFn = Callable[[Mapping[str, Any]], "tuple[float, str]"]


def _output_count(params: Mapping[str, Any]) -> int:
    return int(params.get("output_count", 1) or 1)


def _duration(params: Mapping[str, Any]) -> int:
    value = params.get("duration")
    if value is None:
        raise MissingCostParam("duration is required to estimate video cost")
    return int(value)


def _per_image(base: float) -> RuleFn:
    def rule(params: Mapping[str, Any]) -> tuple[float, str]:
        count = _output_count(params)
        return base * count, f"{base:g} cr/image x {count}"

    return rule


def _per_second(rate: float, *, note: str = "") -> RuleFn:
    def rule(params: Mapping[str, Any]) -> tuple[float, str]:
        seconds = _duration(params)
        suffix = f" ({note})" if note else ""
        return rate * seconds, f"{rate:g} cr/s x {seconds}s{suffix}"

    return rule


def _per_chars(rate_per_chunk: float, chunk: int) -> RuleFn:
    def rule(params: Mapping[str, Any]) -> tuple[float, str]:
        text = str(params.get("prompt_text", "") or "")
        chunks = max(1, math.ceil(len(text) / chunk))
        credits = rate_per_chunk * chunks
        return credits, f"{rate_per_chunk:g} cr / {chunk} chars x {chunks}"

    return rule


def _seed_audio(params: Mapping[str, Any]) -> tuple[float, str]:
    # 0.25 cr/s with a 5-credit minimum; output length is unknown pre-call, so we
    # report the minimum as a floor estimate.
    return 5.0, "5 cr minimum (output duration unknown pre-call)"


def _fixed(credits: float, detail: str) -> RuleFn:
    def rule(params: Mapping[str, Any]) -> tuple[float, str]:
        return credits, detail

    return rule


def _default_rules() -> dict[str, RuleFn]:
    return {
        # text_to_image
        "gen4_image": _per_image(5.0),  # 720p tier; 1080p is 8 (see api-surface)
        "gen4_image_turbo": _per_image(2.0),
        "seedream5_lite": _per_image(4.0),
        "seedream5_pro": _per_image(5.0),  # 1K tier; 2K is 9
        "gemini_2.5_flash": _per_image(5.0),
        "gemini_image3_pro": _per_image(20.0),  # 1K/2K tier; 4K is 40
        # image_to_video (per second of output)
        "gen4_turbo": _per_second(5.0),
        "gemini_omni_flash": _per_second(10.0),
        "veo3.1": _per_second(40.0, note="with audio"),
        "seedance2_5": _per_second(30.0, note="output only; +15 cr/s input not counted"),
        "grok_imagine_1_5": _per_second(10.0),
        # text_to_speech / sound_effect
        "seed_audio": _seed_audio,
        "eleven_v3": _per_chars(1.0, 50),
        "eleven_text_to_sound_v2": _fixed(2.0, "1-2 cr; using 2 (conservative)"),
    }


class PricingBook:
    """Model -> cost rule. Fail-closed: unknown models raise unless overridden."""

    def __init__(self, rules: Mapping[str, RuleFn] | None = None) -> None:
        self._rules: dict[str, RuleFn] = dict(rules) if rules is not None else _default_rules()

    def register(self, model: str, rule: RuleFn) -> None:
        """Override or add a pricing rule (e.g. for aleph2 once its price is known)."""
        self._rules[model] = rule

    def has(self, model: str | None) -> bool:
        return model is not None and model in self._rules

    def estimate(
        self, endpoint: str, model: str | None, params: Mapping[str, Any]
    ) -> CostEstimate:
        if model is None or model not in self._rules:
            raise UnknownModelPricing(model)
        credits, detail = self._rules[model](params)
        return CostEstimate(
            model=model, credits=credits, usd=round(credits * CREDIT_USD, 4), detail=detail
        )


def default_pricing_book() -> PricingBook:
    return PricingBook()
