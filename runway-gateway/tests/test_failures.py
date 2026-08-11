from __future__ import annotations

import pytest

from runway_gateway.api.failures import (
    FailureClass,
    classify_exception,
    classify_failure_code,
)
from runway_gateway.api.fakes import FakeAPIStatusError


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("SAFETY.INPUT.TEXT", FailureClass.MODERATION),
        ("SAFETY.OUTPUT.VIDEO", FailureClass.MODERATION),
        ("INPUT_PREPROCESSING.SAFETY.TEXT", FailureClass.MODERATION),
        ("ASSET.INVALID", FailureClass.PERMANENT),
        ("INTERNAL.BAD_OUTPUT.01", FailureClass.TRANSIENT),
        ("INPUT_PREPROCESSING.INTERNAL", FailureClass.TRANSIENT),
        ("THIRD_PARTY.UNAVAILABLE", FailureClass.TRANSIENT),
        ("INTERNAL", FailureClass.TRANSIENT),
        ("SOMETHING.WE.HAVE.NOT.SEEN", FailureClass.TRANSIENT),  # unknown -> transient
        (None, FailureClass.TRANSIENT),
        ("", FailureClass.TRANSIENT),
    ],
)
def test_classify_failure_code(code: str | None, expected: FailureClass) -> None:
    assert classify_failure_code(code) is expected


def test_classify_failure_code_is_case_insensitive() -> None:
    assert classify_failure_code("safety.input.text") is FailureClass.MODERATION


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, FailureClass.TRANSIENT),
        (500, FailureClass.TRANSIENT),
        (503, FailureClass.TRANSIENT),
        (400, FailureClass.PERMANENT),
        (404, FailureClass.PERMANENT),
        (422, FailureClass.PERMANENT),
    ],
)
def test_classify_exception_by_status(status: int, expected: FailureClass) -> None:
    assert classify_exception(FakeAPIStatusError(status)) is expected


def test_classify_exception_unknown_defaults_transient() -> None:
    assert classify_exception(RuntimeError("boom")) is FailureClass.TRANSIENT
