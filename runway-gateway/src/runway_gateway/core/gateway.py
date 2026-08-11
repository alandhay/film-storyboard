"""``Gateway`` - policy over the transport wrapper.

Cache (content-addressed, depends_on-aware) + retry (TRANSIENT only) + budget
(fail-closed) + bounded fan-out + artifact durability. Blocking execution is the
spine; the poll-mode seam (``submit``/``poll``) is documented and thin.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..api.failures import (
    FailureClass,
    TaskCancelled,
    TaskFailed,
    TaskTimeout,
    classify_exception,
)
from ..api.pricing import CostEstimate, MissingCostParam, UnknownModelPricing
from ..api.wrapper import RunwayAPI
from .artifacts import ArtifactStore, NullArtifactStore
from .budget import Budget
from .cache import (
    ArtifactRef,
    CacheBackend,
    CachedGeneration,
    CacheKey,
    TaskRecord,
    collect_upstream,
    compute_cache_key,
    params_digest,
    resolve_refs,
)
from .retry import RetryPolicy

logger = logging.getLogger("runway_gateway.core")

_ENDPOINT_KIND: dict[str, str] = {
    "text_to_image": "image",
    "image_to_video": "video",
    "video_to_video": "video",
    "video_upscale": "video",
    "text_to_speech": "audio",
    "sound_effect": "audio",
}
_KIND_SUFFIX: dict[str, str] = {"image": "png", "video": "mp4", "audio": "wav"}


@dataclass(frozen=True)
class Generation:
    cache_key: CacheKey
    ref: ArtifactRef
    output_urls: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    estimated_credits: float
    from_cache: bool


@dataclass(frozen=True)
class GenerationError:
    index: int
    endpoint: str
    error: str
    failure_class: FailureClass | None


@dataclass(frozen=True)
class GenerateCall:
    endpoint: str
    params: Mapping[str, Any]
    depends_on: tuple[ArtifactRef, ...] = ()
    kind: str | None = None


@dataclass(frozen=True)
class Pending:
    cache_key: CacheKey
    task_id: str


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Gateway:
    def __init__(
        self,
        api: RunwayAPI,
        cache: CacheBackend,
        *,
        budget: Budget | None = None,
        store: ArtifactStore | None = None,
        retry: RetryPolicy | None = None,
        task_timeout: float = 600.0,
        poll_interval: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._api = api
        self._cache = cache
        self._budget = budget
        self._store: ArtifactStore = store if store is not None else NullArtifactStore()
        self._retry = retry if retry is not None else RetryPolicy()
        self._task_timeout = task_timeout
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._rand = rand

    # --- blocking generate ------------------------------------------------------

    def generate(
        self,
        endpoint: str,
        *,
        depends_on: Sequence[ArtifactRef] = (),
        kind: str | None = None,
        **params: Any,
    ) -> Generation:
        upstream = collect_upstream(params) + [ref.cache_key for ref in depends_on]
        key = compute_cache_key(endpoint, params, upstream=upstream)

        cached = self._cache.get(key)
        if cached is not None:
            logger.info("cache_hit", extra={"endpoint": endpoint, "cache_key": key})
            return self._to_generation(cached, from_cache=True, kind=kind)

        estimate = self._estimate_and_check_budget(endpoint, params)

        call_params = resolve_refs(dict(params))
        result = self._submit_with_retry(endpoint, call_params, key)

        artifact_paths = self._persist(key, endpoint, result.output, kind=kind)
        record = CachedGeneration(
            cache_key=key,
            endpoint=endpoint,
            task_id=result.task_id,
            output_urls=result.output,
            artifact_paths=artifact_paths,
            model=params.get("model"),
            estimated_credits=estimate.credits if estimate else 0.0,
            created_at=_now(),
        )
        self._cache.put(record)
        if self._budget is not None and estimate is not None:
            self._budget.record(estimate)
        return self._to_generation(record, from_cache=False, kind=kind)

    # --- bounded fan-out --------------------------------------------------------

    def map(
        self, calls: Sequence[GenerateCall], *, max_workers: int = 4
    ) -> list[Generation | GenerationError]:
        """Run N generations concurrently, worker-capped. Results in input order;
        one failure is represented in place and never aborts the batch or discards
        already-paid-for successes."""
        results: list[Generation | GenerationError | None] = [None] * len(calls)

        def run(index: int, call: GenerateCall) -> None:
            try:
                results[index] = self.generate(
                    call.endpoint, depends_on=call.depends_on, kind=call.kind, **dict(call.params)
                )
            except Exception as exc:  # noqa: BLE001 - represent failure in place
                results[index] = GenerationError(
                    index=index,
                    endpoint=call.endpoint,
                    error=str(exc),
                    failure_class=getattr(exc, "failure_class", None),
                )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(run, i, call) for i, call in enumerate(calls)]
            for future in futures:
                future.result()  # run() never raises; this just joins

        return [r for r in results if r is not None]

    # --- poll-mode seam (thin; the store-and-return-Pending half is the stub) ----

    def submit(
        self,
        endpoint: str,
        *,
        depends_on: Sequence[ArtifactRef] = (),
        **params: Any,
    ) -> CacheKey:
        """Persist a TaskRecord and return its cache key without waiting. The
        reconcile-later half (``poll``) is stubbed; blocking ``generate`` is the
        implemented spine. See docs/design.md §3.5."""
        upstream = collect_upstream(params) + [ref.cache_key for ref in depends_on]
        key = compute_cache_key(endpoint, params, upstream=upstream)
        cached = self._cache.get(key)
        if cached is not None:
            return key
        if self._budget is not None:
            self._budget.check(endpoint, params)
        call_params = resolve_refs(dict(params))
        handle = self._api.submit(endpoint, **call_params)
        self._cache.put_task(
            TaskRecord(
                cache_key=key,
                task_id=handle.task_id,
                endpoint=endpoint,
                params_digest=params_digest(params),
                status=self._api.retrieve(handle.task_id).status,
                submitted_at=_now(),
            )
        )
        return key

    def poll(self, cache_key: CacheKey) -> Generation | Pending:  # pragma: no cover - stub
        raise NotImplementedError(
            "poll-mode reconciliation is stubbed; use generate() for blocking mode"
        )

    def estimate_cost(self, endpoint: str, **params: Any) -> CostEstimate:
        """Dry-run cost of a call without submitting it. Raises for unpriced models
        (fail-closed) - the CLI ``cost`` command surfaces that rather than lying."""
        return self._api.estimate_cost(endpoint, **params)

    def upload(self, file: Any) -> str:
        """Upload local bytes, returning a ``runway://`` URI (durability boundary,
        e.g. the assembled cut before the grade pass)."""
        return self._api.upload(file)

    # --- internals --------------------------------------------------------------

    def _estimate_and_check_budget(
        self, endpoint: str, params: Mapping[str, Any]
    ) -> CostEstimate | None:
        if self._budget is not None:
            return self._budget.check(endpoint, params)  # raises BudgetExceeded / pricing
        try:
            return self._api.estimate_cost(endpoint, **dict(params))
        except (UnknownModelPricing, MissingCostParam):
            return None

    def _submit_with_retry(
        self, endpoint: str, call_params: Mapping[str, Any], key: CacheKey
    ) -> Any:
        last_exc: BaseException | None = None
        for attempt in range(self._retry.max_attempts):
            try:
                handle = self._api.submit(endpoint, **dict(call_params))
                return self._api.wait(
                    handle, timeout=self._task_timeout, poll_interval=self._poll_interval
                )
            except TaskFailed as exc:
                if exc.failure_class in (FailureClass.MODERATION, FailureClass.PERMANENT):
                    logger.warning(
                        "task_failed_no_retry",
                        extra={"endpoint": endpoint, "class": exc.failure_class.value},
                    )
                    raise
                last_exc = exc
            except (TaskCancelled, TaskTimeout):
                raise
            except Exception as exc:  # noqa: BLE001 - classify transport errors
                if classify_exception(exc) is FailureClass.PERMANENT:
                    raise
                last_exc = exc
            self._sleep(self._retry.delay_for(attempt, self._rand))
            logger.info("retry", extra={"endpoint": endpoint, "attempt": attempt + 1})
        assert last_exc is not None
        raise last_exc

    def _persist(
        self, key: CacheKey, endpoint: str, urls: Sequence[str], *, kind: str | None
    ) -> tuple[str, ...]:
        if isinstance(self._store, NullArtifactStore):
            return ()
        resolved_kind = kind or _ENDPOINT_KIND.get(endpoint, "video")
        suffix = _KIND_SUFFIX.get(resolved_kind, "bin")
        paths: list[str] = []
        for i, url in enumerate(urls):
            try:
                paths.append(self._store.persist(url, key=f"{key}-{i}", suffix=suffix))
            except Exception as exc:  # noqa: BLE001 - durability is best-effort
                logger.warning("persist_failed", extra={"url": url, "error": str(exc)})
        return tuple(paths)

    def _to_generation(
        self, record: CachedGeneration, *, from_cache: bool, kind: str | None
    ) -> Generation:
        resolved_kind = kind or _ENDPOINT_KIND.get(record.endpoint, "video")
        primary_url = record.output_urls[0] if record.output_urls else ""
        return Generation(
            cache_key=record.cache_key,
            ref=ArtifactRef(cache_key=record.cache_key, url=primary_url, kind=resolved_kind),
            output_urls=record.output_urls,
            artifact_paths=record.artifact_paths,
            estimated_credits=record.estimated_credits,
            from_cache=from_cache,
        )
