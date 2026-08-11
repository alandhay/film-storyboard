# Design — `runway-gateway` + `runway-film`

Phase 1 spec. Prose and interface signatures, no implementation. Grounded in the
verified facts of [api-surface.md](api-surface.md). Where the brief's design is
wrong or over-engineered, it's argued in **[Disagreements](#disagreements)** rather
than silently changed.

Status note: the `runway_gateway.api` layer described in §2 is **already built and
tested** (that was the "full API wrapper" step). Everything in §3 (`core`) and §4
(`runway-film`) is Phase 2 work this doc specifies.

---

## 1. Package boundary

Two installable packages in one repo:

```
runway-gateway/   domain-agnostic. tasks, caching, retries, budgets, artifacts.
                  Knows nothing about films, shots, characters, or ffmpeg.
runway-film/      depends on runway-gateway. storyboards, character bibles,
                  the approval gate, assembly. Zero HTTP, zero retry, zero cache.
```

The boundary test is mechanical and enforced in CI-of-the-mind:

```bash
grep -ri "shot\|film\|character\|ffmpeg" runway-gateway/src   # must return nothing
```

A second, unrelated pipeline (e.g. a product-ad generator driving `recipes.product_ad`)
must be able to sit on the same gateway with **no gateway change**. Concretely that
means the gateway's `generate()` takes an opaque `(endpoint, params, depends_on)` and
never inspects param *meaning*.

### Layering inside the gateway

```
runway_gateway.api    (BUILT)  transport: submit/poll/wait, failure taxonomy,
                               pricing, the fake. No policy.
runway_gateway.core   (PHASE2) policy on top of api: cache, depends_on key
                               derivation, retry/backoff, budget ceiling,
                               bounded fan-out, artifact durability.
```

`core` depends on `api`; `api` depends on neither `core` nor the SDK's meaning of
any model. This is what lets the Phase 2 tests exercise policy against the same
injected fake the wrapper uses.

---

## 2. The `api` layer (built — recap of the contract `core` builds on)

- `RunwayAPI(client, *, pricing, clock, sleep)` — client injected (real or fake).
- `submit(endpoint, **params) -> TaskHandle` — one async create; `{id}` only.
- `retrieve(task_id) -> TaskResult` — one poll, normalized across the status union.
- `wait(handle|id, *, timeout, poll_interval) -> TaskResult` — poll to terminal;
  raises `TaskFailed` (carrying a `FailureClass`), `TaskCancelled`, or `TaskTimeout`.
- `estimate_cost(endpoint, **params) -> CostEstimate` — fail-closed on unknown model.
- `upload(file) -> str` (`runway://…`), plus recipe/workflow/org pass-throughs.

Every generation is submit → id → poll. **`core` treats this task record as the
spine** (see §3.5), so blocking and poll-and-reconcile are the same object seen at
two times, not two code paths.

---

## 3. Gateway `core` (Phase 2 design)

### 3.1 Content-addressed cache + the `depends_on` problem

Every generation is keyed by `sha256` over a canonical encoding of
`(endpoint, params, upstream_keys)`. Identical calls resolve from cache and are
never paid for twice.

**The subtlety the brief calls out is real and central.** Upstream artifacts appear
in a child's params as *signed URLs that rotate between runs*. Hashing the URL makes
every second-run key miss. The fix is to key a child on its parents' **cache keys**,
not their URLs.

I propose to solve this with an explicit reference type rather than a bare
`depends_on: list[str]` — and I argue for it in [Disagreements](#disagreements):

```python
CacheKey = str  # sha256 hex

@dataclass(frozen=True)
class ArtifactRef:
    """A resolved upstream output. Hashing uses `cache_key`; the live API call
    uses `url`. This is what keeps a re-run fully cached despite URL rotation."""
    cache_key: CacheKey
    url: str            # possibly-expired remote URL, refreshed on cache-miss only
    kind: str = "video"  # "image" | "video" | "audio" — for routing/validation
```

Params may contain plain JSON scalars **or** `ArtifactRef`s. Key derivation:

```python
def compute_cache_key(
    endpoint: str,
    params: Mapping[str, Any],
    *,
    upstream: Sequence[CacheKey] = (),
) -> CacheKey:
    """Canonicalize params with every ArtifactRef replaced by its cache_key,
    JSON-encode with sorted keys, append sorted upstream keys, sha256."""
```

Because an `ArtifactRef` carries its own `cache_key`, the set of upstream keys is
*derived* from the params — `depends_on` becomes something the gateway computes, not
something the caller must remember to pass in parallel with the URL. (A bare
`depends_on` list is still accepted for cases where a dependency isn't expressed as a
param.) The test the brief demands — *two-stage chain fully cached on re-run* — is
exactly `assert fake.create_count == 0` on the second `film` run.

**Backend behind an interface** (SQLite now, Postgres later):

```python
@dataclass(frozen=True)
class CachedGeneration:
    cache_key: CacheKey
    endpoint: str
    task_id: str
    output_urls: tuple[str, ...]     # remote (may be stale)
    artifact_paths: tuple[str, ...]  # local persisted copies (durable)
    model: str | None
    estimated_credits: float
    created_at: str

class CacheBackend(Protocol):
    def get(self, key: CacheKey) -> CachedGeneration | None: ...
    def put(self, value: CachedGeneration) -> None: ...
    def get_task(self, key: CacheKey) -> TaskRecord | None: ...   # poll-mode, §3.5
    def put_task(self, record: TaskRecord) -> None: ...

class SqliteCache(CacheBackend): ...    # stdlib sqlite3, one file, WAL
```

SQLite is the right call for now (see Disagreements — I agree with the brief).

### 3.2 Failure taxonomy (built in `api`, consumed by `core`)

`FailureClass ∈ {MODERATION, TRANSIENT, PERMANENT, TIMEOUT}`, derived from the real
codes in `/errors/task-failures` by dotted-segment prefix (not substring), with a
`SAFETY`-segment ⇒ MODERATION rule and a TRANSIENT default for unknown codes.
Transport errors classify from HTTP status / SDK exception type. `core`'s retry
policy branches on this and **only** retries `TRANSIENT`.

### 3.3 Retry with backoff + jitter

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.5            # full-jitter fraction
    def delay_for(self, attempt: int) -> float: ...   # exp backoff + jitter
```

Applied to both transport failures (429/5xx on `create`/`retrieve`) and task-level
`TRANSIENT` failures. `MODERATION`/`PERMANENT` raise immediately — the brief's "don't
retry a refusal" is enforced by the classifier, tested directly (a moderation failure
must raise without a second `create`).

### 3.4 Budget ceiling + reconciliation

```python
class Budget:
    def __init__(self, ceiling_credits: float, pricing: PricingBook) -> None: ...
    def would_exceed(self, endpoint: str, params: Mapping[str, Any]) -> bool: ...
    def check(self, endpoint: str, params: Mapping[str, Any]) -> None:
        """Raise BudgetExceeded BEFORE any submission. Fail-closed: an unknown
        model's cost is UnknownModelPricing, which is treated as 'refuse', not '0'."""
    def record(self, estimate: CostEstimate) -> None: ...
    @property
    def spent_credits(self) -> float: ...

class BudgetExceeded(Exception): ...
```

Checked before *each* submission (cache hits cost nothing and skip the check).
Reconciliation compares local estimate against reality via
`api.retrieve_usage(...)` — **left as a documented seam**: the method exists but its
params/response shape are Unverified (api-surface §Unverified), so `reconcile()` is
specified now and stubbed until confirmed.

### 3.5 Two execution modes, one interface — task record as the spine

Because every call is submit → id → poll, `core` persists a `TaskRecord` the moment a
task is submitted, and treats blocking as *polling that same record to terminal*:

```python
@dataclass(frozen=True)
class TaskRecord:
    cache_key: CacheKey
    task_id: str
    endpoint: str
    params_digest: str
    status: TaskState
    submitted_at: str

class Gateway:
    # BLOCKING (implemented now): submit, then poll to terminal, persist, return.
    def generate(self, endpoint: str, *, depends_on: Sequence[ArtifactRef] = (),
                 **params: Any) -> Generation: ...

    # POLL-MODE SEAM (specified now, thin impl later): the same submission, split.
    def submit(self, endpoint: str, *, depends_on: Sequence[ArtifactRef] = (),
               **params: Any) -> CacheKey: ...           # persists TaskRecord, returns key
    def poll(self, cache_key: CacheKey) -> Generation | Pending: ...   # reconcile later
```

Pipeline code calls `generate()` and is identical under both modes: the blocking path
is `submit()` + `poll()`-until-done behind one method. This is a small strengthening
of the brief (it said "leave the seam explicit"); here the seam *is* the spine, so
poll-mode is a thin later addition, not a refactor. Blocking is implemented in Phase 2;
`poll()`'s store-and-return-Pending half is the documented stub.

```python
@dataclass(frozen=True)
class Generation:
    cache_key: CacheKey
    ref: ArtifactRef                 # feeds a downstream stage directly
    output_urls: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    estimated_credits: float
    from_cache: bool
```

### 3.6 Bounded fan-out

```python
def map(self, calls: Sequence[GenerateCall], *, max_workers: int
        ) -> list[Generation | GenerationError]: ...
```

N independent generations, worker-capped (stdlib `concurrent.futures.ThreadPoolExecutor`
— the SDK is I/O-bound HTTP, threads are fine, no new dep). **Results in input order;
one failure represented in place, never aborting the batch or discarding
already-paid-for successes.** Tested: one failure in five leaves four intact. Note the
account tier caps concurrency per model (e.g. `veo3.1` = 1 concurrent) — `max_workers`
is our cap; the API's own 429s are absorbed by retry.

### 3.7 Artifact durability

Output URLs expire in 24–48h (api-surface §9). On completion, `core` copies outputs to
a store and records local paths on the `CachedGeneration`:

```python
class ArtifactStore(Protocol):
    def persist(self, url: str, *, key: CacheKey, suffix: str) -> str: ...  # -> local path
class LocalArtifactStore(ArtifactStore): ...      # ./artifacts/<key>.<suffix>
# ObjectStore later, same Protocol.
```

**Chaining passes the remote URL** (inside an `ArtifactRef`), not re-uploaded bytes —
keeping data in Runway's network. Local copies are for durability/final delivery, and
to refresh an `ArtifactRef.url` if it went stale before a downstream miss.

---

## 4. `runway-film` (Phase 2 design)

Stages: **character bible → keyframes → approval gate → shot clips → audio →
assembly → grade.** The film package holds model choices, prompt assembly, and the
gate; it calls `gateway.generate(...)` and never touches HTTP/cache/retry.

### 4.1 Storyboard: declarative input, validated on load

**Format: JSON** (not YAML). Justification: `json` is stdlib (ground rule: no new deps
without asking; `pyyaml` would be one), parse errors are precise, and the schema is
small. YAML's ergonomic win (comments, less punctuation) is real but not worth a
dependency for Phase 2; a YAML loader can be added behind the same `Storyboard.load`
later. (Called out in Disagreements as a genuine trade-off, not a slam dunk.)

```python
@dataclass(frozen=True)
class Character:
    tag: str                 # 3–16 chars, starts with a letter (Runway reference-tag rule)
    description: str
    reference_image: str | None = None

@dataclass(frozen=True)
class Shot:
    id: str
    prompt: str              # may reference @tags
    character_tags: tuple[str, ...]
    duration_seconds: int
    keyframe_model: str = "gen4_image"
    clip_model: str = "gen4_turbo"

@dataclass(frozen=True)
class Storyboard:
    title: str
    ratio: str
    characters: Mapping[str, Character]
    shots: tuple[Shot, ...]
    look: str | None = None          # single grade note

    @classmethod
    def load(cls, path: str | Path) -> "Storyboard": ...
```

`load()` **validates at parse time, before any generation**:
- every `@tag` referenced in a shot prompt / `character_tags` is defined → else
  `StoryboardError("shot s2 references undefined character @villain")`;
- tags satisfy Runway's reference-tag rule (3–16, leading letter);
- durations, ratio, non-empty prompts.

This is the brief's "fail at parse time, not after forty generations." Tested directly.

### 4.2 The approval gate as a type, not a boolean

Stills are cheap (~5 cr / $0.05); clips are dollars (an 8s `veo3.1` = 320 cr / $3.20).
Generating all keyframes, approving, then animating only approved shots is the whole
cost-control story. So the gate is enforced **by construction**:

```python
@dataclass(frozen=True)
class Keyframe:
    shot_id: str
    ref: ArtifactRef                 # a generated still

@dataclass(frozen=True)
class ApprovedKeyframe:
    shot_id: str
    ref: ArtifactRef
    _token: object = field(repr=False)   # only approve() holds the token

def approve(keyframe: Keyframe) -> ApprovedKeyframe: ...        # the ONLY factory

def animate(gateway: Gateway, shot: Shot, approved: ApprovedKeyframe) -> Clip: ...
```

`animate()` — the only function that calls `image_to_video` — takes `ApprovedKeyframe`,
which nothing but `approve()` can construct. An unapproved `Keyframe` is a *type error*
at the call site, not a runtime `if`. Test: attempting to animate a `Keyframe` fails to
type-check / raises; there is no path from unapproved still to clip.

### 4.3 Stage signatures

```python
def build_character_bible(gw: Gateway, sb: Storyboard) -> dict[str, ArtifactRef]: ...
    # one keyframe-quality reference still per character (text_to_image), cached.

def generate_keyframes(gw: Gateway, sb: Storyboard,
                       bible: Mapping[str, ArtifactRef]) -> list[Keyframe]: ...
    # text_to_image per shot; character refs passed as reference_images (tagged),
    # so @tags in the prompt bind to the bible. depends_on = the bible refs.

def generate_clips(gw: Gateway, sb: Storyboard,
                   approved: Sequence[ApprovedKeyframe]) -> list[Clip]: ...
    # image_to_video per approved shot; prompt_image = the approved still's URL,
    # depends_on = [approved.ref]. Fan-out via gw.map with a worker cap.

def generate_audio(gw: Gateway, sb: Storyboard, clips: Sequence[Clip]) -> list[Audio]:
    raise NotImplementedError  # STUB: TTS/SFX params resolved (prompt_text/voice) but
    # per-shot dialogue authoring is out of Phase 2 scope; documented in docstring.

def assemble(clips: Sequence[Clip], audio: Sequence[Audio]) -> LocalVideo: ...
    # Phase 2: plain concat (ffmpeg concat demuxer). Richer editing is a marked stub.

def grade(gw: Gateway, cut: LocalVideo, look: str | None) -> GradedVideo: ...
    # upload the assembled cut, one aleph2 video_to_video pass with `look` as prompt_text.
```

### 4.4 Grade degrades gracefully

The grade is one `aleph2` pass over the stitched cut to unify colour/lighting. It
**must not lose work on failure**:

```python
def grade(gw, cut, look) -> GradedVideo:
    """One aleph2 pass. On TaskFailed/TaskTimeout, log a warning and return the
    ungraded cut wrapped as GradedVideo(graded=False). Never raises past here."""
```

Also note api-surface §Unverified: `aleph2` has no published price, so `Budget` will
`UnknownModelPricing` on it unless the caller registers an override — the grade stage
documents that it needs a price override or a `--no-grade` / `--allow-unpriced` flag.

### 4.5 CLI

```
runway-film cost      <storyboard.json>            # dry-run: estimate credits/$; NO spend
runway-film keyframes <storyboard.json> [--out d]  # bible + keyframes, then STOP at gate
runway-film film      <storyboard.json> [--look s] [--yes]  # full pipeline
```

`cost` sums `estimate_cost` across planned calls and prints a per-stage table — the
honest dollar figure before anything is submitted, and it surfaces any unpriced model.
`keyframes` deliberately halts at the approval gate (writes stills + an approval
manifest). `film` resumes from approvals. Structured logging throughout; `print` only
in the CLI presentation layer.

---

## 5. What Phase 2 stubs (deliberately, with docstrings)

- **Audio stage** — signatures fixed (both TTS variants use `prompt_text`; voice via
  `voice.preset_id` or `voice.audio_uri`), but dialogue authoring is out of scope.
- **Assembly beyond plain concat** — transitions, per-shot trims.
- **Poll-mode `poll()`** — the store-and-return-`Pending` half (§3.5).
- **Postgres cache** — `CacheBackend` Protocol only; `SqliteCache` is real.
- **`Budget.reconcile()`** — pending verification of `retrieve_usage` shape.
- **Object-store durability** — `ArtifactStore` Protocol only; `LocalArtifactStore` real.

A clearly-marked stub is fine; a confidently-wrong implementation is not.

---

## 6. Test plan (all against the injected fake — no live calls)

1. Second run of an identical pipeline → `fake.create_count == 0` (cache proof).
2. Changing one shot's prompt regenerates that shot + descendants, nothing else
   (key derivation + `depends_on`).
3. Moderation failure raises immediately, no retry (`create_count == 1`).
4. 429-then-success retries and returns (`transient_creates=1`).
5. Budget ceiling raises before any `create` (`create_count == 0`).
6. One failure in a fan-out of five leaves four intact, in order.
7. Unapproved keyframe cannot produce a clip (type/construction).
8. Storyboard with an undefined `@tag` fails at `load()`.

`pytest`, `ruff`, `mypy --strict` on the gateway package. Python 3.11+.

---

## Disagreements

Things in the brief I'd change, and why.

1. **`depends_on` as a bare `list[str]` of keys is clumsy — prefer `ArtifactRef`.**
   A raw `depends_on` forces the caller to pass, in parallel, both the URL (for the
   call) and the cache key (for the hash), and to keep them in sync by hand — the
   exact bookkeeping that produces "works once, misses on re-run" bugs. Folding both
   into an `ArtifactRef` that params carry directly means the dependency set is
   *derived*, not remembered. I keep a `depends_on` escape hatch for non-param
   dependencies, but the primary API is the ref. (This is the one real API change I'm
   recommending; §3.1.)

2. **"Implement blocking now, leave a poll seam" understates how cheap poll-mode is.**
   Since every endpoint is submit→id→poll already, making the persisted `TaskRecord`
   the spine (§3.5) means blocking and poll-mode are one object at two times. I'd build
   it that way from the start rather than as a later seam — same effort, no refactor.

3. **SQLite: agree, not a disagreement.** Content-addressed, single-writer, local —
   SQLite (WAL) is correct. The `CacheBackend` Protocol keeps Postgres a drop-in. No
   change; flagging that I considered and rejected "just use Postgres now" as premature.

4. **Storyboard format: JSON over YAML is a close call, decided by the no-deps rule.**
   YAML is friendlier for humans to author (comments, less punctuation) and I'd lean
   YAML in a product. For a stdlib-first scaffold I pick JSON and keep `Storyboard.load`
   format-agnostic so YAML slots in behind it later. If you'd rather take the `pyyaml`
   dependency now, say so — it's a one-line change to the plan.

5. **`recipes.multi_shot_video` is not the pipeline, but it belongs in the gateway.**
   Per your steer, the full wrapper already exposes it (and all recipes/workflows) as
   pass-throughs. It can't host the approval gate, tagged references, or a separate
   grade pass, so `runway-film` does not build on it — but a `product-ad` pipeline
   could, on the same gateway, unchanged. It stays available, not load-bearing.

6. **Budget must fail closed on unknown models — a stronger stance than "estimate".**
   With `aleph2` (the grade model) genuinely unpriced, an estimator that treats unknown
   as `0` would let the grade stage slip a budget ceiling silently. `Budget.check`
   raises on unknown cost unless a price is explicitly registered. This makes the
   ceiling trustworthy at the cost of forcing an explicit override for unpriced models —
   the right trade.
