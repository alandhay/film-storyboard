# API Surface — Runway Generative Media API

**Phase 0 deliverable.** Every parameter name below was read from primary sources,
not from memory or from the examples in the build prompt:

- Python SDK type stubs on `main`
  (`github.com/runwayml/sdk-python/blob/main/src/runwayml/types/*_params.py`,
  `*_response.py`) — these are the authoritative snake_case field names.
- `docs.dev.runwayml.com` guides: `using-the-api`, `models`, `pricing`,
  `errors/task-failures`, `assets/inputs`, `assets/outputs`, `assets/uploads`.
- `github.com/runwayml/sdk-python/blob/main/api.md` for the resource list.

> **Heads-up: the live API has moved well past the prompt's examples.** The build
> prompt names models like `gen4_image_turbo` and `gen4_turbo`. Those still exist,
> but the current SDK also ships `gen4.5`, `veo3.1`, `seedance2` / `seedance2_5`,
> `aleph2`, `gemini_*`, `seedream5_*`, `gpt_image_2`, `seed_audio`, and the
> ElevenLabs family. The gateway must treat `model` as an opaque string it passes
> through, **not** an enum it validates — new models ship faster than we'd redeploy.

---

## 0. How tasks work (shared across every generation endpoint)

Every `*.create(...)` call is **asynchronous** and returns only a task id:

```python
resp = client.text_to_image.create(model=..., prompt_text=..., ratio=...)
resp.id            # -> str  ("The ID of the task that was created.")
```

`TextToImageCreateResponse` (and every sibling create-response) has exactly one
field: `id: str`. **Output URLs are not on the create response.** You get them by
polling.

### Poll / retrieve — `client.tasks.retrieve(id)`

Returns `TaskRetrieveResponse`, a discriminated union on `status`:

| status (Literal) | extra fields on that variant |
|---|---|
| `PENDING`   | — |
| `THROTTLED` | — |
| `RUNNING`   | `progress: float` |
| `SUCCEEDED` | `output: List[str]`  ← the result URLs live here |
| `FAILED`    | `failure: str`, `failure_code: Optional[str]` |
| `CANCELLED` | — |

Shared on all variants: `id: str`, `created_at: datetime` (alias `createdAt`).

So: **task id** is `resp.id` on create and `task.id` on retrieve; **output URLs**
are `task.output` (a `List[str]`), present only on the `SUCCEEDED` variant;
**failure reason** is `task.failure` / `task.failure_code` on the `FAILED` variant.

### Blocking convenience — `.wait_for_task_output()`

`client.<endpoint>.create(...).wait_for_task_output()` polls to completion and
returns the finished task, raising `TaskFailedError` on a `FAILED` status. This is
the blocking mode Phase 2 implements. Exact poll-interval / timeout kwargs are
**Unverified** (see below) — the gateway will own its own poll loop over
`tasks.retrieve` so we control backoff and timeout regardless.

### Other resources on the client

- `client.tasks.retrieve(id)` / `client.tasks.delete(id)`
- `client.organization.retrieve()` and
  `client.organization.retrieve_usage(**params)` — reconciliation path for budgets.
- `client.uploads.create_ephemeral(file=...)` — see §8.
- `client.recipes.*` and `client.workflows.*` — see §9 (relevant to whether we
  build the assembly stage ourselves).

---

## 1. `text_to_image` — `client.text_to_image.create(...)`

`TextToImageCreateParams` is a **union** over model-specific TypedDicts. Fields
common to the ones we'd use:

| field | required | notes |
|---|---|---|
| `model` | yes | e.g. `"gen4_image"`, `"gen4_image_turbo"`, `"gpt_image_2"`, `"seedream5_pro"`, `"gemini_image3_pro"` |
| `prompt_text` | yes | **not** `text`/`prompt`. Length cap varies by model (1000 UTF-16 for gen4; 32000 for gpt_image_2; 4000 for seedream5). |
| `ratio` | yes | aspect-ratio literal set varies by model (exact per-model list **Unverified**, see §11) |
| `reference_images` | varies | `Iterable[{uri, tag?}]`. **Required (1–3)** for `gen4_image_turbo`; optional for `gen4_image` (≤3), `gpt_image_2` (≤16), `gemini_image3_pro` (≤14). |
| `seed` | optional | `int` |
| `content_moderation` | optional | `{public_figure_threshold: "auto" | "low"}` (gen4 family) |
| `output_count` | optional | gpt_image_2 (1–10), gemini/seedream variants |

**Reference image tags** (the mechanism `runway-film` uses for character consistency):
`reference_images[i].uri` is required (HTTPS URL, `runway://` upload URI, or base64
data URI); `reference_images[i].tag` is optional and, per the gen4 stub, **"must be
3–16 characters, start with a letter."** The pipeline's `@character` references map
onto these tags.

**Response:** `{ id }`. Image URL(s) at `task.output` after `SUCCEEDED`.

---

## 2. `image_to_video` — `client.image_to_video.create(...)`

Union over model TypedDicts (`gen4.5`, `gen4_turbo`, `veo3.1`, `veo3.1_fast`,
`veo3`, `seedance2`/`_fast`/`_mini`, `happyhorse_1_0`, `gemini_omni_flash`).
Representative required/optional fields:

| field | required | notes |
|---|---|---|
| `model` | yes | e.g. `"gen4.5"`, `"gen4_turbo"`, `"veo3.1"`, `"seedance2"` |
| `prompt_image` | yes | `str` **or** `Iterable[{uri, position}]`. `position` is `"first"` (all models) and additionally `"last"` for veo3.1 / some models. This is the keyframe → clip hand-off: pass the approved keyframe URL here. |
| `prompt_text` | varies | motion description (required on gen4.5; optional on others). ≤1000 UTF-16 (gen4/veo), up to 2500–3500 for happyhorse/seedance. |
| `ratio` | yes | per-model literal set (e.g. gen4.5: `1280:720`, `720:1280`, `1104:832`, `960:960`, `832:1104`, `1584:672`) |
| `duration` | varies | gen4.5: int 2–10; veo3.1: Literal `4|6|8`; veo3: Literal `8`; seedance: int |
| `seed` | optional | `int` |
| `audio` | optional | `bool` — veo3.1, seedance2 can generate audio inline |
| `negative_prompt` | optional | veo3 / veo3.1 |
| `content_moderation` | optional | `{public_figure_threshold}` (gen4 family) |
| `reference_audio` | optional | seedance2 — `Iterable[{type:"audio", uri}]`, ≤15s total |

**`prompt_image` accepts a URL** — so chaining keyframe→clip passes the remote
keyframe URL, no re-upload (satisfies the "keep bytes inside Runway's network"
requirement). Data-URI cap is 5MB for images (§7).

**Response:** `{ id }`. Video URL at `task.output` after `SUCCEEDED`.

---

## 3. `video_to_video` — `client.video_to_video.create(...)` (the grade stage)

Union of variants. **The `aleph2` variant is the one the grade stage uses.**

`aleph2`:

| field | required | notes |
|---|---|---|
| `video_uri` | **yes** | **This is the source-video parameter** (answers open question 2). "A HTTPS URL, Runway upload URI, or base64 data URI (`data:video/mp4;base64,...`, up to 16MB)." |
| `model` | yes | `"aleph2"` |
| `prompt_text` | optional | the single look-note the grade stage sends |
| `ratio` | optional | `str` |
| `target_aspect_ratio` | optional | Literal `16:9 | 4:3 | 3:2 | 1:1 | 2:3 | 3:4 | 9:16 | 21:9` |
| `keyframes` | optional | `Iterable` (≤5) |
| `seed` | optional | `int` |
| `content_moderation` | optional | — |

> **Note the parameter name differs across variants of the same endpoint.** The
> `seedance2` and `gemini_omni_flash` video_to_video variants call the source
> `prompt_video` / `video_uri` respectively and add `references` / `reference_videos`.
> For our grade stage we commit to `aleph2` + `video_uri`. The gateway must not
> assume one source-param name across models — another reason `model` stays opaque.

**Source accepts a URL** (16MB data-URI ceiling; larger → uploads endpoint, §8).
So the assembled cut is uploaded once via `uploads.create_ephemeral`, then its
`runway://` URI is passed as `video_uri`.

**Response:** `{ id }`. Graded video at `task.output`.

---

## 4. `text_to_speech` — `client.text_to_speech.create(...)`

Union of two variants. **Answers open question 1.**

`seed_audio` (voice cloning):

| field | required | notes |
|---|---|---|
| `model` | yes | `"seed_audio"` |
| `prompt_text` | **yes** | **the text to speak.** Field is `prompt_text` — the SAME name used by `sound_effect` (§5). There is no bare `text=`. |
| `voice` | optional | `{type:"reference-audio", audio_uri}` — clone from one reference clip, then speak `prompt_text` in that voice |
| `output_format` | optional | `"wav" | "mp3" | "ogg_opus"` |
| `sample_rate` | optional | `8000|16000|24000|32000|44100|48000` |
| `speech_rate`, `pitch_rate`, `loudness_rate` | optional | `int`, 0 = normal |

`eleven_multilingual_v2` (preset voices):

| field | required | notes |
|---|---|---|
| `model` | yes | `"eleven_multilingual_v2"` |
| `prompt_text` | **yes** | ≤1000 chars |
| `voice` | **yes** | `{type:"runway-preset", preset_id}` — `preset_id` is a named preset literal (Maya, Arjun, Serene, …) |

**Voice selection differs by model:** `seed_audio` clones via
`voice.audio_uri`; `eleven_multilingual_v2` picks a named `voice.preset_id`. There
is no single shared voice mechanism — but the **text field is shared: `prompt_text`.**

**Response:** `{ id }`. Audio at `task.output`.

---

## 5. `sound_effect` — `client.sound_effect.create(...)`

Union of two variants. Text field is `prompt_text` in both (shared with TTS).

`seed_audio`: `model`, `prompt_text` (required), plus `reference_audios:
SequenceNotStr[str]` (≤3 clips, referenced in the prompt as `@Audio1`..`@Audio3`),
and the same rate/format knobs as §4.

`eleven_text_to_sound_v2`: `model`, `prompt_text` (required), `duration: float`
(0.5–30s; auto if omitted), `loop: bool`.

**Response:** `{ id }`. Audio at `task.output`.

---

## 6. `video_upscale` — `client.video_upscale.create(...)`

`magnific_video_upscaler_creative`:

| field | required | notes |
|---|---|---|
| `model` | yes | `"magnific_video_upscaler_creative"` |
| `video_uri` | **yes** | URL / `runway://` / data URI ≤16MB |
| `resolution` | optional | `"720p" | "1k" | "2k" | "4k"` (default `2k`) |
| `creativity` | optional | int 0–100 |
| `flavor` | optional | `"vivid" | "natural"` |
| `sharpen` | optional | int 0–100 |
| `smart_grain` | optional | int 0–100 |
| `fps_boost` | optional | bool |

**Response:** `{ id }`. Upscaled video at `task.output`.

---

## 7. Input size limits (`/assets/inputs`)

| type | max via URL | max via base64 data URI | max via ephemeral upload |
|---|---|---|---|
| image | 16MB | **5MB** | 200MB |
| video | 32MB | **16MB** | 200MB |
| audio | 32MB | **16MB** | 200MB |

Accepted: images JPEG/PNG/WebP; video MP4/QuickTime/Matroska/WebM/3GPP/Ogg
(H.264/H.265/AV1); audio MP3/WAV/FLAC/M4A/AAC.

**Design consequence:** the gateway should route anything above the data-URI
ceiling (5MB image / 16MB video/audio) through `uploads.create_ephemeral` rather
than inlining. Between stages we pass the remote URL and never hit these limits.

---

## 8. Uploads endpoint (`/assets/uploads`)

```python
resp = client.uploads.create_ephemeral(file=Path("./cut.mp4"))
resp.uri   # -> "runway://..."  pass this as video_uri / prompt_image / uri
```

- `file`: `pathlib.Path`, a file-like object with a `.name`, or `(filename, bytes)`.
- Max **200MB**, min 512 bytes.
- Returned `runway://` URI **expires after 24h** and needs an active credit balance.

Use this only when local bytes must enter Runway's network (e.g. uploading the
locally-assembled cut before the grade pass). Stage-to-stage chaining uses the
prior stage's remote output URL directly.

---

## 9. Output URL lifetime (`/assets/outputs`)

`task.output` URLs are **ephemeral: they expire within 24–48 hours.** Docs
explicitly say to download and persist to your own storage and not to expose these
URLs to end users. **This is the justification for the gateway's artifact-durability
requirement** — copy to local disk/object storage on completion; keep the cache
entry pointing at the persisted copy, while stage-to-stage chaining may still pass
the (still-fresh) remote URL.

---

## 10. Does Runway already ship the assembly stage? (`recipes` / `workflows`)

**Yes, partially — you should know this before we build our own assembler.**

`client.recipes.multi_shot_video(...)` exists and **assembles multiple shots into
one video.** Two modes:

- **`mode="auto"`**: give one story `prompt` (+ optional `firstFrame`, `ratio`,
  `duration` ∈ {5,10,15}, `audio`) and Runway plans and stitches the shots.
- **`mode="custom"`**: give `shots` (**3–5** objects, each `{prompt, duration}`),
  `version` (`"2026-06"`), optional `audio`/`ratio`/`firstFrame`. Shot durations
  must sum to `duration`.

Other recipes: `ad_localization`, `marketing_stock_image`, `product_ad`,
`product_campaign_image`, `product_swap`, `product_ugc`.

`client.workflows` is a separate hosted-workflow resource: `.retrieve(id)`,
`.list()`, `.run(id, **params)`, plus `client.workflow_invocations.retrieve(id)`.

**Assessment for the design doc:** `multi_shot_video` overlaps the pipeline's
keyframes→clips→assembly span, but it is a **black box** — it does not expose the
per-keyframe **approval gate** that the build prompt calls the main cost control,
nor per-shot tagged-character references or the separate Aleph grade pass. It's a
one-shot text→multi-shot-video convenience, not a controllable pipeline. So it does
**not** replace what we're building; it's an alternative worth documenting (and
possibly worth a thin gateway pass-through later). I'll argue this properly in
`docs/design.md`. `client.workflows` looks like user-defined saved workflows from
the Runway app; **what a custom workflow can contain is Unverified.**

---

## 11. Failure taxonomy (`/errors/task-failures`) — for the classifier

Failures surface as `task.failure_code` (string) on the `FAILED` variant, plus a
human `task.failure`. Observed enumerated codes and the docs' own retry guidance:

| `failure_code` (prefix/value) | our class | docs guidance |
|---|---|---|
| `SAFETY.INPUT.*` (e.g. `SAFETY.INPUT.TEXT`) | **MODERATION** | "You should not retry." Credits not refunded. |
| `SAFETY.OUTPUT.*` | **MODERATION** | Do not retry. |
| `INPUT_PREPROCESSING.SAFETY.TEXT` | **MODERATION** | Input text rejected by moderation. |
| `ASSET.INVALID` | **PERMANENT** | "Do not retry — a problem with your inputs." |
| `INTERNAL.BAD_OUTPUT.*` (e.g. `.01`) | **TRANSIENT** | "May retry; may succeed if corrections made." Often logos/watermarks/explicit-text requests. |
| `INPUT_PREPROCESSING.INTERNAL` | **TRANSIENT** | "May retry, but add a delay." |
| `THIRD_PARTY.UNAVAILABLE` | **TRANSIENT** (delayed) | "Do not retry immediately; wait and retry." |
| `INTERNAL` or `null`/absent | **TRANSIENT** | "May retry, but add a delay." |

Plus transport-level: HTTP **429** and **5xx** from `create`/`retrieve` →
**TRANSIENT** (backoff + jitter). Never-settled within the window → **TIMEOUT**.

**Classifier design:** match on `failure_code` **prefix** (`SAFETY.`,
`INPUT_PREPROCESSING.SAFETY`, `ASSET.INVALID`, `INTERNAL.BAD_OUTPUT`, …), not
loose substrings, with a default of TRANSIENT for unknown codes and a hard
distinction that anything under a `SAFETY.*` / `*.SAFETY.*` namespace is
MODERATION and never retried. The full string set beyond the prefixes above is
open-ended, so prefix-matching (not an exhaustive enum) is the safe encoding.

---

## 12. Pricing (`/guides/pricing`) — for the budget estimator

**1 credit = $0.01.** Costs are per-image / per-second-of-output / per-character.

| model | cost |
|---|---|
| `gen4_image_turbo` | 2 credits / image |
| `gen4_image` | 5 (720p) / 8 (1080p) credits / image |
| `seedream5_pro` | 5 (1K) / 9 (2K) |
| `seedream5_lite` | 4 / image |
| `gemini_image3_pro` | 20 (1K/2K) / 40 (4K) |
| `gemini_2.5_flash` | 5 / image |
| `gpt_image_2` | 1–41 (varies by quality/resolution) |
| `gen4_turbo` (i2v) | 5 credits / second |
| `veo3.1` (i2v, audio) | 40 credits / second |
| `gemini_omni_flash` (i2v) | 10 credits / second |
| `seedance2_5` (i2v, 720p) | 30 / sec output + 15 / sec input |
| `grok_imagine_1_5` (480p) | 10 / sec |
| `magnific_video_upscaler_creative` | ~$0.007–$0.012 / frame by resolution |
| `seed_audio` (TTS/SFX) | 0.25 credits / sec (5-credit minimum) |
| `eleven_v3` (TTS) | 1 credit / 50 characters |
| `eleven_text_to_sound_v2` (SFX) | 1–2 credits (by params) |

**Estimator design:** a per-model cost function keyed on `(model, unit)` where unit
is images, seconds, or characters. Video dominates (an 8s veo3.1 clip = 320 credits
= $3.20; a gen4_image keyframe = 5 credits = $0.05) — which is exactly why the
approval gate gating video behind cheap stills is the headline cost control.
`aleph2` (video_to_video / grade) per-second price was **not found on the pricing
page**, but is now known empirically: **~27 credits/second of output** (measured —
a 29.9s grade at 1080p output billed 812 credits, 812/29.9 ≈ 27.1). Caveat: single
sample; the rate may vary with output resolution (this pass upscaled 720p→1080p).
Note this cost cannot be estimated from params alone — `video_to_video` takes no
`duration`; the billed length is a property of the input `video_uri` asset — so the
default `PricingBook` keeps `aleph2` fail-closed and a caller who knows the cut
length registers an override: `book.register("aleph2", lambda p: (27.0 * secs, ...))`.

---

## Unverified — honest gaps (do not fill these by analogy)

1. **`aleph2` (video_to_video) credit cost.** ~~Not on the pricing page.~~
   RESOLVED empirically: **~27 credits/second of output** (812 cr for a 29.9s 1080p
   grade). Single sample; may vary with output resolution. Still can't be estimated
   pre-call from params (no `duration` param — length comes from the input asset), so
   the budget stays fail-closed on it unless the caller registers an override.
2. **`.wait_for_task_output()` signature** — poll interval, timeout, and whether
   it raises vs returns on failure are not verified from the stub. Mitigated: the
   gateway owns its own poll loop over `tasks.retrieve`, so this only affects the
   optional convenience path.
3. **`organization.retrieve_usage(**params)` exact params and response shape** —
   confirmed the method exists; the param names (date range? model breakdown?) and
   the response field holding credit totals are unverified. Needed for the
   reconciliation path; stub the reconciler until confirmed.
4. **Exact `ratio` literal sets for `text_to_image`** per model — I have the
   image_to_video ratios verbatim but only "aspect-ratio literal" for several
   text_to_image models. Treat `ratio` as pass-through string; don't hard-code an
   enum yet.
5. **`gpt_image_2` pricing (1–41 credits)** — the exact quality/resolution→credit
   mapping wasn't captured; estimator will over- or under-shoot for that model
   until pinned. (We can avoid it by defaulting keyframes to `gen4_image`.)
6. **Whether `recipes.*` / `workflows.run` return a task id resolvable via
   `tasks.retrieve`**, or their own response/polling type. Assumed similar; not
   confirmed. Only matters if we later expose them.
7. **`content_moderation` default** when omitted, and full `public_figure_threshold`
   semantics. Minor.
8. **Full enumerated `failure_code` string set** — docs give prefixes/examples, not
   a closed enum, so the classifier is written against prefixes with a
   TRANSIENT-by-default fallback. This is a deliberate design choice, not a guess.

---

## Answers to the two questions you flagged as open

1. **TTS vs sound_effect text/voice.** Both use **`prompt_text`** for the text —
   the same field name, no bare `text=`. Voice selection is **not** shared: TTS
   `seed_audio` clones a voice via `voice.audio_uri` (`type:"reference-audio"`);
   TTS `eleven_multilingual_v2` picks `voice.preset_id` (`type:"runway-preset"`);
   `sound_effect` has no voice concept (it's `prompt_text` + optional
   `reference_audios`/`duration`/`loop`).

2. **video_to_video source video param.** For the `aleph2` grade variant it is
   **`video_uri`**, accepting an HTTPS URL, a `runway://` upload URI, **or** a
   base64 data URI up to **16MB**. Above 16MB (our assembled cut will exceed this),
   upload first via `client.uploads.create_ephemeral(file=...)` and pass the
   returned `runway://` URI. (Other v2v variants rename it `prompt_video`; we
   standardize on `aleph2`/`video_uri`.)
