"""Retry policy: exponential backoff with full jitter. Only TRANSIENT is retried."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.5  # fraction of the delay subject to jitter

    def delay_for(self, attempt: int, rand: Callable[[], float]) -> float:
        """Delay before retry ``attempt`` (0-indexed). Full-jitter within ``jitter``."""
        capped = min(self.max_delay, self.base_delay * (2.0**attempt))
        return capped * (1.0 - self.jitter * rand())
