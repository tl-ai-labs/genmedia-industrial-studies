# Deliberate probe — does the `@Video1` handle matter? (2026-09-09)

**Authorised by the study lead. $1.72 spent, both Omni cells. Seedance cost
nothing — it was refused before generating.**

## Question

The Seedance 2.5 API reference references assets in the prompt by handle
("remove everyone in @Video1 except the protagonist"). Our scenario prompts,
verbatim from the bank, carry no handle. If Seedance needs one and Omni does
not, the two arms would receive **different briefs** — which breaks the rule
that every model gets the identical prompt and makes the comparison unfair.

## Method

One scenario (VID-EDIT-01, the shortest edit source at 5.65s) in two
variants against both arms, through the runner with `--budget 6.0` and an
isolated `models-probe.yaml`, so the real config stayed paused throughout:

- `PROBE-NOHANDLE` — the bank prompt exactly as written
- `PROBE-HANDLE`   — identical, with "this clip" replaced by "@Video1"

## Result

| Arm | No handle | With handle |
|---|---|---|
| **Omni Flash** | ✅ edited correctly, $0.8616, 136s | ✅ edited correctly, $0.8616, 103s |
| **Seedance 2.5** | ❌ `AccountOverdueError` (403) | ❌ `AccountOverdueError` (403) |

**Omni binds the source clip without any handle.** The output is a genuine
edit: the umbrella is recoloured red→yellow while the woman, her pose, the
fence and the treeline are unchanged. Output duration 5.674s against a
source of 5.653s — it followed the source's length rather than emitting a
default 8s clip, which is itself evidence the asset was bound.

**The handle makes no observable difference to Omni.** Both variants produced
an equivalent correct edit.

**Seedance is untested.** The BytePlus account has an overdue balance and
every request is refused with 403 before any generation, so it cost nothing.

## What this settles

1. The verbatim bank prompts work as-is for Omni. No prompt rewriting is
   needed for the Gemini arm.
2. Because the handle is a **no-op for Omni**, adding it to *both* arms'
   prompts would be a fair fallback if Seedance turns out to require it —
   it cannot advantage or disadvantage Omni. That removes the fairness
   objection from option (b) without needing Seedance tested first.

Caveat: one scenario, one seed. Two equivalent outputs are evidence, not
proof, that the handle is universally inert for Omni.

## What is still open

Whether Seedance needs the handle. It cannot be answered until the BytePlus
balance is cleared. The probe scenarios are kept at
`/private/tmp/claude-501/handle-probe/` and can be re-run for ~$2.65 the
moment the account is funded.
