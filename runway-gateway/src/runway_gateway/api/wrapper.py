"""``RunwayAPI`` - the full-surface transport wrapper.

This layer is deliberately *thin*: it dispatches to the SDK, normalizes responses
into gateway value types, owns a poll loop with a timeout, and classifies
failures. It does **not** implement retries, caching, budgets, or fan-out - that
policy lives in ``runway_gateway.core`` (Phase 2) on top of this. Keeping them
separate means the policy layer can be tested against the same fake this uses.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, cast

from .endpoints import GENERATION_ENDPOINTS, RECIPE_ENDPOINTS
from .failures import (
    TaskCancelled,
    TaskFailed,
    TaskTimeout,
    classify_failure_code,
)
from .models import TaskHandle, TaskResult, TaskState
from .pricing import CostEstimate, PricingBook, default_pricing_book
from .protocols import RunwayClient

Clock = Callable[[], float]
Sleep = Callable[[float], None]

logger = logging.getLogger("runway_gateway.api")

DEFAULT_TIMEOUT = 600.0
DEFAULT_POLL_INTERVAL = 2.0


class RunwayAPI:
    """Uniform submit/poll/wait over every Runway endpoint.

    The SDK client is injected (a real ``RunwayML`` or a fake) so nothing here is
    coupled to the network. ``clock``/``sleep`` are injectable to make the poll
    loop deterministic under test.
    """

    def __init__(
        self,
        client: RunwayClient,
        *,
        pricing: PricingBook | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._client = client
        self._pricing = pricing if pricing is not None else default_pricing_book()
        self._clock = clock
        self._sleep = sleep

    @classmethod
    def from_env(
        cls,
        *,
        api_key: str | None = None,
        pricing: PricingBook | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> RunwayAPI:
        """Construct against the live SDK.

        The secret is resolved from ``api_key`` or, failing that, the environment /
        ``.env`` (``RUNWAYML_API_SECRET`` or ``RUNWAY_API_KEY``). Imported lazily so
        the package is usable (and testable with a fake) without a key set.
        """
        from runwayml import RunwayML  # local import: only needed for live use

        from ..config import resolve_api_secret

        secret = api_key if api_key is not None else resolve_api_secret()
        # The concrete SDK client is structurally compatible with RunwayClient
        # (read-only cached-property resources vs. our settable-attr protocol); the
        # cast asserts that at this single injection boundary so the rest of the
        # module - and the injected fake - stay fully type-checked.
        client = cast(RunwayClient, RunwayML(api_key=secret))
        return cls(client, pricing=pricing, clock=clock, sleep=sleep)

    # --- submission -------------------------------------------------------------

    def submit(self, endpoint: str, **params: Any) -> TaskHandle:
        """Submit a generation task; returns a handle carrying its id + params."""
        if endpoint not in GENERATION_ENDPOINTS:
            raise ValueError(
                f"{endpoint!r} is not a generation endpoint; "
                f"expected one of {sorted(GENERATION_ENDPOINTS)}"
            )
        resource = getattr(self._client, endpoint)
        model = params.get("model")
        logger.info("submit", extra={"endpoint": endpoint, "model": model})
        response = resource.create(**params)
        handle = TaskHandle(
            task_id=str(response.id), endpoint=endpoint, model=model, params=dict(params)
        )
        logger.info("submitted", extra={"endpoint": endpoint, "task_id": handle.task_id})
        return handle

    # thin per-endpoint conveniences (identical semantics to submit())
    def text_to_image(self, **params: Any) -> TaskHandle:
        return self.submit("text_to_image", **params)

    def image_to_video(self, **params: Any) -> TaskHandle:
        return self.submit("image_to_video", **params)

    def video_to_video(self, **params: Any) -> TaskHandle:
        return self.submit("video_to_video", **params)

    def text_to_speech(self, **params: Any) -> TaskHandle:
        return self.submit("text_to_speech", **params)

    def sound_effect(self, **params: Any) -> TaskHandle:
        return self.submit("sound_effect", **params)

    def video_upscale(self, **params: Any) -> TaskHandle:
        return self.submit("video_upscale", **params)

    # --- polling ----------------------------------------------------------------

    def retrieve(self, task_id: str) -> TaskResult:
        """One poll of ``tasks.retrieve`` -> normalized state."""
        raw = self._client.tasks.retrieve(task_id)
        return TaskResult.from_task(raw)

    def wait(
        self,
        target: TaskHandle | str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> TaskResult:
        """Poll until terminal. Returns on SUCCEEDED; raises on FAILED / CANCELLED
        / timeout. Retry policy is *not* applied here - see the core gateway."""
        task_id = target.task_id if isinstance(target, TaskHandle) else target
        deadline = self._clock() + timeout
        while True:
            result = self.retrieve(task_id)
            if result.status is TaskState.SUCCEEDED:
                return result
            if result.status is TaskState.FAILED:
                raise TaskFailed(
                    task_id,
                    failure=result.failure,
                    failure_code=result.failure_code,
                    failure_class=classify_failure_code(result.failure_code),
                )
            if result.status is TaskState.CANCELLED:
                raise TaskCancelled(task_id)
            if self._clock() >= deadline:
                raise TaskTimeout(task_id, waited_seconds=timeout)
            self._sleep(poll_interval)

    def submit_and_wait(
        self,
        endpoint: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        **params: Any,
    ) -> TaskResult:
        """Blocking convenience: submit then wait. Pipeline code uses this shape;
        the core gateway wraps it with caching/retry/budget under the same name."""
        handle = self.submit(endpoint, **params)
        return self.wait(handle, timeout=timeout, poll_interval=poll_interval)

    def cancel(self, task_id: str) -> None:
        self._client.tasks.delete(task_id)

    # --- assets -----------------------------------------------------------------

    def upload(self, file: Any) -> str:
        """Upload local bytes; returns a ``runway://`` URI (valid ~24h).

        Used only when local bytes must enter Runway's network (e.g. the assembled
        cut before the grade pass). Stage-to-stage chaining passes remote output
        URLs directly instead.
        """
        response = self._client.uploads.create_ephemeral(file=file)
        return str(response.uri)

    # --- recipes / workflows pass-through (black boxes; exposed, not used) -------

    def recipe(self, name: str, **params: Any) -> Any:
        if name not in RECIPE_ENDPOINTS:
            raise ValueError(
                f"{name!r} is not a recipe; expected one of {sorted(RECIPE_ENDPOINTS)}"
            )
        return getattr(self._client.recipes, name)(**params)

    def run_workflow(self, workflow_id: str, **params: Any) -> Any:
        return self._client.workflows.run(workflow_id, **params)

    def list_workflows(self) -> Any:
        return self._client.workflows.list()

    def retrieve_workflow(self, workflow_id: str) -> Any:
        return self._client.workflows.retrieve(workflow_id)

    def retrieve_workflow_invocation(self, invocation_id: str) -> Any:
        return self._client.workflow_invocations.retrieve(invocation_id)

    # --- organization / reconciliation ------------------------------------------

    def retrieve_organization(self) -> Any:
        return self._client.organization.retrieve()

    def retrieve_usage(self, **params: Any) -> Any:
        """Reconciliation path for budgets. Exact params/response shape are
        unverified (see docs/api-surface.md) - pass-through for now."""
        return self._client.organization.retrieve_usage(**params)

    # --- cost -------------------------------------------------------------------

    def estimate_cost(self, endpoint: str, **params: Any) -> CostEstimate:
        """Estimate spend for a call *without submitting it*. Raises
        ``UnknownModelPricing`` for a model with no rule (fail-closed)."""
        model = params.get("model")
        return self._pricing.estimate(endpoint, model, params)

    @property
    def pricing(self) -> PricingBook:
        return self._pricing
