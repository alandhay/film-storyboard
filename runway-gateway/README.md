# runway-gateway

Domain-agnostic gateway over the [Runway](https://docs.dev.runwayml.com) generative
media API. Knows about tasks, caching, retries, budgets, and artifacts. Knows
nothing about films, shots, characters, or ffmpeg.

## Layers

```
runway_gateway.api    thin, full-surface transport wrapper over the RunwayML SDK.
                      Every endpoint (generation + recipes + workflows), uniform
                      submit / poll / wait, failure classification, cost estimation,
                      and an injectable fake client for tests. No network policy.

runway_gateway.core   (Phase 2) cache, depends_on key derivation, retry/backoff,
                      budget ceiling, bounded fan-out, artifact durability — built
                      ON TOP of api, adding the policy the raw wrapper deliberately
                      omits.
```

The wrapper treats `model` as an opaque pass-through string, never an enum: the
live API ships new models faster than we would redeploy. Param validity is
discovered as a `PERMANENT` failure from the API, not pre-checked here.

## Install (editable, dev)

```bash
pip install -e "runway-gateway[dev]"
```

## The `api` wrapper

```python
from runway_gateway.api import RunwayAPI

api = RunwayAPI.from_env()                      # reads RUNWAYML_API_SECRET
handle = api.submit("text_to_image", model="gen4_image",
                    prompt_text="a lighthouse at dawn", ratio="1920:1080")
result = api.wait(handle)                        # polls tasks.retrieve to completion
print(result.output)                             # ('https://...ephemeral-url...',)
```

Tests inject a fake instead of `from_env()`:

```python
from runway_gateway.api import RunwayAPI
from runway_gateway.api.fakes import FakeRunwayClient, Behavior

api = RunwayAPI(FakeRunwayClient(default_behavior=Behavior.SUCCEED))
```

No test hits the live API.
