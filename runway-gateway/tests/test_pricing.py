from __future__ import annotations

import pytest

from runway_gateway.api.pricing import (
    CREDIT_USD,
    MissingCostParam,
    PricingBook,
    UnknownModelPricing,
    default_pricing_book,
)


def test_per_image_estimate() -> None:
    book = default_pricing_book()
    est = book.estimate("text_to_image", "gen4_image", {"prompt_text": "x", "ratio": "1920:1080"})
    assert est.credits == 5.0
    assert est.usd == pytest.approx(0.05)


def test_output_count_multiplies() -> None:
    book = default_pricing_book()
    est = book.estimate("text_to_image", "gen4_image", {"output_count": 3})
    assert est.credits == 15.0


def test_per_second_video_estimate() -> None:
    book = default_pricing_book()
    est = book.estimate("image_to_video", "veo3.1", {"duration": 8})
    assert est.credits == 320.0
    assert est.usd == pytest.approx(3.20)


def test_video_requires_duration() -> None:
    book = default_pricing_book()
    with pytest.raises(MissingCostParam):
        book.estimate("image_to_video", "gen4_turbo", {})


def test_unknown_model_fails_closed() -> None:
    book = default_pricing_book()
    # aleph2 (the grade stage) has no published per-second price - must NOT be
    # silently costed at zero.
    with pytest.raises(UnknownModelPricing):
        book.estimate("video_to_video", "aleph2", {"duration": 5})


def test_pricing_is_injectable_override() -> None:
    book = default_pricing_book()
    book.register("aleph2", lambda p: (20.0 * int(p["duration"]), "override"))
    est = book.estimate("video_to_video", "aleph2", {"duration": 5})
    assert est.credits == 100.0


def test_credit_usd_constant() -> None:
    assert CREDIT_USD == 0.01


def test_custom_book_starts_empty_of_defaults() -> None:
    book = PricingBook(rules={})
    assert not book.has("gen4_image")
    with pytest.raises(UnknownModelPricing):
        book.estimate("text_to_image", "gen4_image", {})
