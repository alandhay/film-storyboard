"""Full-surface transport wrapper over the RunwayML SDK."""

from __future__ import annotations

from .endpoints import (
    GENERATION_ENDPOINTS,
    RECIPE_ENDPOINTS,
    is_generation_endpoint,
    is_recipe,
)
from .failures import (
    FailureClass,
    GatewayError,
    TaskCancelled,
    TaskFailed,
    TaskTimeout,
    classify_exception,
    classify_failure_code,
)
from .models import TERMINAL_STATES, TaskHandle, TaskResult, TaskState
from .pricing import (
    CREDIT_USD,
    CostEstimate,
    MissingCostParam,
    PricingBook,
    UnknownModelPricing,
    default_pricing_book,
)
from .wrapper import RunwayAPI

__all__ = [
    "CREDIT_USD",
    "CostEstimate",
    "FailureClass",
    "GatewayError",
    "GENERATION_ENDPOINTS",
    "MissingCostParam",
    "PricingBook",
    "RECIPE_ENDPOINTS",
    "RunwayAPI",
    "TaskCancelled",
    "TaskFailed",
    "TaskHandle",
    "TaskResult",
    "TaskState",
    "TaskTimeout",
    "TERMINAL_STATES",
    "UnknownModelPricing",
    "classify_exception",
    "classify_failure_code",
    "default_pricing_book",
    "is_generation_endpoint",
    "is_recipe",
]
