# runway-film

A storyboard-to-film pipeline on top of [`runway-gateway`](../runway-gateway).
Knows about storyboards, character bibles, the approval gate, and assembly. Contains
zero HTTP, zero retry logic, zero cache logic — all of that is the gateway's job.

## Stages

```
character bible → keyframes → [approval gate] → shot clips → audio → assembly → grade
```

- **Character bible** — one reference still per character (`text_to_image`), cached.
- **Keyframes** — a still per shot; character refs passed as tagged `reference_images`
  so `@tags` in prompts stay visually consistent.
- **Approval gate** — a *type*, not a boolean. `animate()` only accepts an
  `ApprovedKeyframe`, which only `approve()` can construct. Unapproved → clip is a
  type error. Stills are cheap; video is dollars — approving before animating is the
  cost control.
- **Clips** — `image_to_video` per approved keyframe, fanned out; one failure leaves
  the rest intact.
- **Audio / assembly / grade** — audio is a marked stub; assembly is plain ffmpeg
  concat; grade is one `aleph2` pass that degrades gracefully (returns the ungraded
  cut on failure).

## CLI

```bash
runway-film cost      examples/storyboard.json              # dry-run, no spend, no key
runway-film keyframes examples/storyboard.json --yes        # stills, then STOP at the gate
runway-film film      examples/storyboard.json --approvals artifacts/approvals.json --yes
```

`cost` needs no API key (pricing only). `keyframes`/`film` read `RUNWAY_API_KEY`
(or `RUNWAYML_API_SECRET`) and spend credits — both preview cost and require `--yes`.

## Storyboards

Declarative JSON, validated on load — an unknown `@tag` fails at parse time, not after
forty generations. See [`examples/storyboard.json`](examples/storyboard.json).
