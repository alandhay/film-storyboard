"""Budget ceiling checked before every (uncached) submission.

Fail-closed: an unknown model's cost raises ``UnknownModelPricing`` (from the
pricing book) rather than counting as zero, so the ceiling can't be slipped by an
unpriced model such as ``aleph2``. Reconciliation against
``organization.retrieve_usage`` is specified but stubbed (its shape is Unverified).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from ..api.pricing import CostEstimate, PricingBook, default_pricing_book


class BudgetExceeded(Exception):
    def __init__(self, spent: float, estimate: float, ceiling: float) -> None:
        self.spent = spent
        self.estimate = estimate
        self.ceiling = ceiling
        super().__init__(
            f"budget ceiling {ceiling:g} cr would be exceeded: "
            f"spent {spent:g} + next {estimate:g}"
        )


class Budget:
    def __init__(self, ceiling_credits: float, pricing: PricingBook | None = None) -> None:
        self._ceiling = ceiling_credits
        self._pricing = pricing if pricing is not None else default_pricing_book()
        self._spent = 0.0
        self._lock = threading.Lock()

    def check(self, endpoint: str, params: Mapping[str, Any]) -> CostEstimate:
        """Estimate the next call and raise ``BudgetExceeded`` if it would breach
        the ceiling. Returns the estimate so the caller can ``record`` it on
        success. Propagates ``UnknownModelPricing`` / ``MissingCostParam``."""
        estimate = self._pricing.estimate(endpoint, params.get("model"), params)
        with self._lock:
            if self._spent + estimate.credits > self._ceiling:
                raise BudgetExceeded(self._spent, estimate.credits, self._ceiling)
        return estimate

    def record(self, estimate: CostEstimate) -> None:
        with self._lock:
            self._spent += estimate.credits

    @property
    def spent_credits(self) -> float:
        return self._spent

    @property
    def ceiling_credits(self) -> float:
        return self._ceiling

    def reconcile(self, usage: Any) -> None:  # pragma: no cover - documented stub
        """Compare local estimate against ``organization.retrieve_usage`` output.

        STUB: the usage response shape is Unverified (see docs/api-surface.md), so
        this is specified but not implemented. Wire it once the shape is confirmed.
        """
        raise NotImplementedError(
            "Budget.reconcile is stubbed pending verification of retrieve_usage shape"
        )
