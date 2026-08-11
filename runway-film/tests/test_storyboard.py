from __future__ import annotations

from pathlib import Path

import pytest

from conftest import storyboard_dict
from runway_film import Storyboard, StoryboardError


def test_valid_storyboard_loads() -> None:
    sb = Storyboard.from_dict(storyboard_dict())
    assert sb.title == "Test Film"
    assert len(sb.shots) == 3
    assert set(sb.characters) == {"aria", "kade"}


def test_undefined_tag_in_characters_fails_at_load() -> None:
    data = storyboard_dict()
    data["shots"][1]["characters"] = ["villain"]  # not defined
    with pytest.raises(StoryboardError, match="undefined character 'villain'"):
        Storyboard.from_dict(data)


def test_undefined_mention_in_prompt_fails_at_load() -> None:
    data = storyboard_dict()
    data["shots"][0]["prompt"] = "@ghost drifts past"  # @ghost not defined
    with pytest.raises(StoryboardError, match="undefined character @ghost"):
        Storyboard.from_dict(data)


def test_invalid_character_tag_rejected() -> None:
    data = storyboard_dict()
    data["characters"]["x"] = {"description": "too short a tag"}  # 1 char
    with pytest.raises(StoryboardError, match="invalid"):
        Storyboard.from_dict(data)


def test_duplicate_shot_id_rejected() -> None:
    data = storyboard_dict()
    data["shots"][1]["id"] = "s1"
    with pytest.raises(StoryboardError, match="duplicate shot id"):
        Storyboard.from_dict(data)


def test_non_positive_duration_rejected() -> None:
    data = storyboard_dict()
    data["shots"][0]["duration"] = 0
    with pytest.raises(StoryboardError, match="duration"):
        Storyboard.from_dict(data)


def test_example_storyboard_is_valid() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "storyboard.json"
    sb = Storyboard.load(example)
    assert len(sb.shots) == 3
    assert set(sb.characters) == {"aria", "kade"}
