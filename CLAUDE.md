# CLAUDE.md

Guidance for Claude Code working in this repo. Read this before making changes.

## What this is

A monorepo with two installable Python packages:

- `runway-gateway/` — domain-agnostic gateway over the Runway generative media API.
  - `src/runway_gateway/api/` — thin, full-surface transport wrapper over the
    `runwayml` SDK (all generation endpoints + recipes + workflows + uploads + org).
    Uniform `submit`/`retrieve`/`wait`, failure taxonomy, pricing, an injectable
    `FakeRunwayClient`. **No policy** (no cache/retry/budget).
  - `src/runway_gateway/core/` — policy on top of `api`: content-addressed cache
    with `depends_on`/`ArtifactRef` key derivation, retry+backoff, budget ceiling,
    bounded fan-out, artifact durability. `Gateway.generate(...)` is the entry point.
- `runway-film/` — storyboard-to-film pipeline on the gateway. Stages: character
  bible → keyframes → approval gate → clips → audio → assembly → grade.

`docs/api-surface.md` (verified API facts) and `docs/design.md` (architecture +
disagreements with the original brief) are the source of truth for intent.

## Non-negotiable invariants

1. **Package boundary.** The gateway must never learn film concepts. This must stay
   empty except for Runway's own `multi_shot_video` endpoint name:
   ```bash
   grep -ri "shot\|film\|character\|ffmpeg" runway-gateway/src
   ```
   `runway-film` may depend on `runway-gateway`; never the reverse.
2. **`model` is an opaque pass-through string, never an enum.** Runway ships new
   models faster than we redeploy. Do not validate model names or per-model param
   shapes in the gateway; invalid params surface as a `PERMANENT` failure from the
   API. Model-specific knowledge lives in exactly one place: the pricing book.
3. **Verify API facts against primary docs, don't guess.** Parameter names,
   response shapes, and failure codes come from the SDK type stubs and
   docs.dev.runwayml.com — recorded in `docs/api-surface.md` with an explicit
   "Unverified" section. If you can't confirm something, stub it and say so; never
   fill a gap by analogy with another endpoint.
4. **No new runtime dependencies beyond `runwayml` without asking.** Stdlib first
   (sqlite3, urllib, concurrent.futures, json, argparse are all in use). Dev tools
   (`pytest`, `ruff`, `mypy`) are fine.
5. **Never commit a key.** `.env` is gitignored; `.env.example` only. The gateway
   reads `RUNWAY_API_KEY` or `RUNWAYML_API_SECRET` via `runway_gateway.config`.
6. **No test may hit the live API.** Inject `FakeRunwayClient` at construction. Tests
   assert on `fake.create_count` / `retrieve_count` — that's how caching and retry
   are proven.

## Toolchain (run per package, from its directory)

```bash
python -m pytest -q          # 49 gateway tests, 15 film tests
python -m ruff check .       # line-length 100; select E,F,I,UP,B,SIM
python -m mypy               # --strict; files = ["src"] (tests not strict-checked)
```

Python 3.11+. Type hints throughout. Structured logging (`logging`), no bare
`print` outside `runway_film/cli.py` (the presentation layer).

Type-checking note: the SDK client / fake are matched to `RunwayClient` structurally
and need a `cast` at the injection boundary (`RunwayAPI.from_env`) — this is
deliberate, not a bug. Keep the fake fully typed so tests still type-check.

## Key mechanisms to preserve when editing

- **`ArtifactRef`** (`core/cache.py`) carries a stable `cache_key` (used for hashing)
  and a rotating `url` (used for the live call). This is what makes a re-run of a
  chained pipeline fully cached despite signed-URL rotation. Don't hash URLs.
- **The approval gate is a type** (`runway_film/gate.py`). `animate()` accepts only
  an `ApprovedKeyframe`, which only `approve()` can construct. Do not add a code path
  that animates a raw `Keyframe`.
- **Budget fails closed** (`core/budget.py`): an unpriced model raises rather than
  costing zero. `aleph2` (the grade model) is genuinely unpriced.
- **Grade degrades gracefully** (`runway_film/grade.py`): on failure it returns the
  ungraded cut with a warning — never lose the assembled work.

## Deliberate stubs (raise `NotImplementedError` with a docstring — keep them honest)

Audio stage, ffmpeg assembly beyond plain concat, poll-mode `Gateway.poll()`,
Postgres `CacheBackend`, `Budget.reconcile()` (pending verification of
`retrieve_usage` shape). A clearly-marked stub is fine; a confidently-wrong
implementation is not.

## Conventions

- Small commits, one concern each, conventional-commit messages.
- Work in phases; the docs track them (Phase 0 API surface → 1 design → 2 scaffold).
- The live account may have 0 credits — generation calls fail until it's topped up;
  the fake-based tests and the `cost` command work regardless.
