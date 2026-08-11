from __future__ import annotations

from typing import Any

import pytest
from runway_gateway.api import RunwayAPI
from runway_gateway.api.fakes import FakeRunwayClient
from runway_gateway.core import Gateway, InMemoryCache, RetryPolicy

from runway_film import Storyboard


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fake_client() -> FakeRunwayClient:
    return FakeRunwayClient()


@pytest.fixture
def gateway(fake_client: FakeRunwayClient) -> Gateway:
    clock = FakeClock()
    api = RunwayAPI(fake_client, clock=clock.now, sleep=clock.sleep)
    return Gateway(
        api,
        InMemoryCache(),
        retry=RetryPolicy(base_delay=0.0, jitter=0.0),
        poll_interval=0.0,
        sleep=lambda _s: None,
        rand=lambda: 0.0,
    )


def storyboard_dict(s2_prompt: str = "@kade at the comms console") -> dict[str, Any]:
    return {
        "title": "Test Film",
        "ratio": "1280:720",
        "look": "warm grade",
        "characters": {
            "aria": {"description": "Aria, an engineer"},
            "kade": {"description": "Kade, a comms officer"},
        },
        "shots": [
            {"id": "s1", "prompt": "@aria at the panel", "characters": ["aria"], "duration": 5},
            {"id": "s2", "prompt": s2_prompt, "characters": ["kade"], "duration": 5},
            {
                "id": "s3",
                "prompt": "@aria and @kade brace",
                "characters": ["aria", "kade"],
                "duration": 6,
            },
        ],
    }


@pytest.fixture
def storyboard() -> Storyboard:
    return Storyboard.from_dict(storyboard_dict())
