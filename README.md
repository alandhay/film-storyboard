# Runway: `runway-gateway` + `runway-film`

Two Python packages in one repo:

- **[`runway-gateway`](runway-gateway/)** — a domain-agnostic gateway over the
  [Runway](https://docs.dev.runwayml.com) generative media API. Tasks, caching,
  retries, budgets, artifacts. Knows nothing about films.
- **[`runway-film`](runway-film/)** — a storyboard-to-film pipeline built on the
  gateway. Storyboards, character bibles, an approval gate, assembly. Contains zero
  HTTP, retry, or cache logic.

```
runway-gateway/
  src/runway_gateway/
    api/     full-surface transport wrapper over the SDK (submit/poll/wait,
             failure taxonomy, pricing, an injectable fake). No policy.
    core/    policy on top of api: cache + depends_on key derivation, retry,
             budget, bounded fan-out, artifact durability.
runway-film/
  src/runway_film/   storyboard, bible, keyframes, gate, clips, audio (stub),
                     assembly, grade, cli.
  examples/storyboard.json
docs/        api-surface.md (verified API facts) · design.md (architecture)
```

The boundary is load-bearing and mechanically checkable:

```bash
grep -ri "shot\|film\|character\|ffmpeg" runway-gateway/src   # only 'multi_shot_video' (Runway's own endpoint)
```

## Quickstart

```bash
python -m pip install -e "runway-gateway[dev]"
python -m pip install -e "runway-film[dev]"

cp .env.example .env      # then put your key in it (RUNWAY_API_KEY=...)
```

Dry-run the cost of the example film — **no key, no spend**:

```bash
cd runway-film
python -m runway_film.cli cost examples/storyboard.json
```

## Running the pipeline live

`keyframes` and `film` read `RUNWAY_API_KEY` (or `RUNWAYML_API_SECRET`) and spend
credits. Both preview cost and require `--yes`.

```bash
cd runway-film
python -m runway_film.cli keyframes examples/storyboard.json --yes   # stills, then STOP at the gate
# edit artifacts/approvals.json — set the shots you approve to true
python -m runway_film.cli film examples/storyboard.json --approvals artifacts/approvals.json --yes
```

The approval gate is the cost control: stills are ~5 credits ($0.05) each; a video
clip is dollars. Approve cheap stills before animating. Re-running is free — the
content-addressed cache serves anything already generated.

## Development

```bash
# per package (run from runway-gateway/ or runway-film/)
python -m pytest -q
python -m ruff check .
python -m mypy            # --strict, configured in pyproject
```

Python 3.11+. The only runtime dependency is `runwayml`; everything else is stdlib.
Secrets live in `.env` (gitignored) — never commit a key.

See [docs/api-surface.md](docs/api-surface.md) for the verified API facts this is
built on, and [docs/design.md](docs/design.md) for the architecture and the
design decisions (including where the original brief was pushed back on).
