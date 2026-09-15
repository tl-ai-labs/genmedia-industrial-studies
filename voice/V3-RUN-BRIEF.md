# v3 run — brief before you start

Companion to `HANDOFF.md` (tasks A/B/C). Read that first. This file is the
pre-run checklist specific to adding **ElevenLabs v3** and re-running the bank.

Branch: `feat/voice-models-sainadh` (cut from `main`, which now carries the
merged voice lane). Nothing here has been spent or committed yet.

---

## DECISIONS LOCKED (2026-09-09) — do not re-litigate

1. **ElevenLabs arm = `eleven_v3`** (the expressive/quality model). Not
   `eleven_v3_conversational`. Best output is the priority.
2. **Transport:** ElevenLabs → **direct API** (`api.elevenlabs.io`, key in
   `voice/.env`). Vertex ruled out — Model Garden on `ai-studies-console` has
   only `elevenlabs-tts-v2-5`, `CAN_PREDICT: No`. No v3 there.
   Gemini → **Vertex** as today (`gemini-3.1-flash-tts-preview`, `us-central1`)
   — it is the *only* Gemini TTS model that streams, and it is already the
   enabled arm. Asymmetric transport is fine and helps independence.
3. **Both arms stream.** `measure_ttfa` → the adapter's `/stream` route (plain
   HTTP chunked, already built for both). TTFA measured the same way on both.
4. **v3 replaces v2.** Every published v2 voice number is withdrawn; v2 and v3
   are not comparable. Re-run the whole bank from scratch, 2 passes per arm
   (same method v2 used — `--label p1` / `--label p2`).
5. **Price:** `eleven_v3` = $0.10 / 1k chars, identical to `multilingual_v2`,
   so the existing `models.yaml` price block value carries over unchanged.
   (Verified: elevenlabs.io/pricing/api + help article, 2026-09-09.)

### Known and accepted (not blockers)

- **v3 first-audio will not be real-time fast** (~1–2 s vs Chom's 0.6–1 s
  target). Streaming narrows the gap vs whole-call time; it does not close it.
  v3's TTFA is currently unmeasured and may be worse than v2's 1292 ms — the
  run measures it, we report it honestly next to Gemini. This is the finding,
  not a problem to fix.
- **v3's emotion advantage is not exercised.** The adapter sends plain text,
  no inline audio tags (`[whispers]`). Testing v3 "plain", same input as v2.
  A "v3 is more expressive" claim needs tag support (code) *and* the blocked
  human panel — out of scope for this run unless explicitly added.
- **Gate failures on v3 are results, not bugs.** Do not loosen gates.
- **v3 voice ≠ v2 voice** at the same voice_id — footnote it, nothing more.
- **Gemini/Vertex auth** is a 1-hour token — mint it in each run command.

### Still needs a human decision before the run is "done" (not before it starts)

- Task C re-grouping (call centre {retail/telco/banking} + micro-drama):
  **separate task**, relabel-vs-edit-scripts question unresolved. Does not
  block the v3 re-run.
- One-arm vs also-enabling `eleven_flash_v2_5` as a latency reference point:
  optional, decide when reading the TTFA results.

---

## Probe results — free metadata check, 2026-09-09

Run via `scratch/probe_elevenlabs_v3.sh` (no characters spent).

- **Plan:** Creator, active. **103,881 characters remaining** of 121,298
  (17,417 used); resets ~1 Oct 2026; cannot extend. One full 23-scenario pass
  ≈ 8k chars, so 2 passes × 2 arms + retries (~35–40k) fits with headroom.
- **Both v3 models are on this key:** `eleven_v3` *and*
  `eleven_v3_conversational` are listed, `can_do_text_to_speech`, 74 languages,
  no `requires_alpha_access`.
- **Style param:** only `eleven_multilingual_v2` reports `can_use_style`.
  Neither v3 model does → v3 expressiveness is inline audio tags only, and the
  adapter's current "style dropped → `params_unsupported`" behaviour will apply
  to v3 as well. No regression, but the emotion angle stays unexercised.
- **All 8 `voice_map` IDs exist** on the account (21 voices total). Not yet
  confirmed they *generate* under v3 — that needs the paid probe.

**Route test — paid probe, ~32 chars spent, 2026-09-09:** all four calls
returned HTTP 200 + audio:

| model | `POST /text-to-speech/{voice}` | `.../stream` |
| --- | --- | --- |
| `eleven_v3` | 200 (46 KB) | 200 (42 KB) |
| `eleven_v3_conversational` | 200 (42 KB) | 200 (38 KB) |

⇒ **Both v3 models work on the adapter's existing REST routes.** No Text-to-
Dialogue endpoint, no WebSocket, no adapter rewrite. Task B is a `models.yaml`
block. The latency arm (task A) can also run through the current adapter with
`measure_ttfa` → `/stream`. **Caveat:** HTTP-chunked `/stream` is not the
WebSocket real-time path, so `eleven_v3_conversational`'s ~280 ms figure will
not reproduce here — but the TTFA methodology is then identical to Gemini's,
which is what makes the comparison fair.

**Pricing** (elevenlabs.io/pricing/api, 2026-09-09 — re-verify before publish):
`eleven_v3` $0.10 / 1k chars (same as multilingual-v2, so the existing price
block value carries over); `eleven_v3_conversational` $0.05 / 1k chars.

---

## 0 · Three things to settle BEFORE any paid run

### 1 · Direct ElevenLabs API vs Google Vertex

Today `runner/adapters/elevenlabs_tts.py` calls `api.elevenlabs.io` directly
with `ELEVENLABS_API_KEY`. HANDOFF §15 notes — unverified — that ElevenLabs
models may also be served through Vertex AI Model Garden. This is an
evidence-integrity decision, not a code detail: you cannot switch route later
without re-running everything.

- **Version certainty.** Model Garden lags upstream. "v3" on Vertex that is
  really a v2.5-era build is a silent, unrecoverable error in the results.
  Direct API gives exactly what ElevenLabs ships today.
- **Billing dimension.** Direct API bills per *character sent* to the
  ElevenLabs plan (`models.yaml` → `unit: per_1k_chars`; adapter reports
  `characters=len(req.text)`). Vertex bills compute to the GCP project. That
  makes every existing $/clip and $/minute figure incomparable — you'd have to
  re-run the Gemini side too just to keep the cost column honest.
- **Vendor independence.** The judge is already Google (`gemini-2.5-flash`)
  and the ASR used to be (fixed in `8ffce31` because it flattered the Google
  arm). Routing ElevenLabs' transport through Google infra as well weakens the
  "independent competitor" framing and must be disclosed on the client report.

**CHECKED 2026-09-09 — decided: DIRECT API.** Vertex Model Garden on project
`ai-studies-console` lists exactly one ElevenLabs model:

```
elevenlabs/elevenlabs-tts-v2-5@default    CAN_DEPLOY: Yes   CAN_PREDICT: No
```

- **No `eleven_v3` on Vertex at all** — only a v2.5-era model. Task B needs v3.
- `CAN_PREDICT: No` — it can't even be called via managed Vertex prediction;
  you'd have to self-deploy it to an endpoint. (The Gemini TTS models are
  `CAN_PREDICT: Yes`, which is why the Gemini arm runs on Vertex fine.)
- Matches the public position: ElevenLabs on GCP is a Marketplace subscription,
  not a managed v3 inference endpoint.

So Vertex is a dead end for this task — not on policy, on availability. The
ElevenLabs arm stays on the **direct API**; the Gemini arm stays on Vertex.
That asymmetry is fine and actually helps independence — the two arms don't
share transport. Record this in HANDOFF §15 and move on.

### 2 · Which v3 model ID

"Add v3" is two different products:

|            | `eleven_v3`                                   | `eleven_v3_conversational`            |
| ---------- | --------------------------------------------- | ------------------------------------- |
| Built for  | Most expressive TTS — audiobook, drama, ads   | Real-time speech for agents / call centre |
| Latency    | ~1–2 s+; ElevenLabs says *not for real-time*  | ~280 ms                               |
| Transport  | HTTP only; no WebSockets (403 if tried)       | Text-to-Dialogue WebSocket only       |
| GA         | 2 Feb 2026                                    | ~20 Aug 2026                          |

The two tasks pull opposite ways:

- **Task B (quality re-run).** None of the 23 scenarios is a multi-speaker
  conversation — all single-voice monologues, including the "agent turn only"
  ones. So `eleven_v3` covers the whole quality bank and is the *better* model
  for drama / audiobook / ads. `eleven_v3_conversational` buys nothing here.
- **Task A (streaming latency, TTFA target 0.6–1 s).** `eleven_v3` physically
  cannot hit that. Sub-second TTFA needs `eleven_v3_conversational` (~280 ms,
  but requires the Text-to-Dialogue WebSocket the adapter does not have) or
  `eleven_flash_v2_5` (~75 ms, already a disabled block in `models.yaml` —
  `vr-game-03`'s header names it as the realistic real-time ElevenLabs tier).

**Do:** settle with Chom. The probe removed the technical constraint — both v3
models run through the current adapter — so this is now purely a study-design
choice:
- **One arm:** `eleven_v3` on the whole bank. Simplest; latency scenarios then
  report `eleven_v3` chunked TTFA with a note that it is not ElevenLabs' real-
  time tier.
- **Two arms:** `eleven_v3` for quality + `eleven_v3_conversational` for the
  responsiveness scenarios (runs through the same adapter now). Costs a second
  set of passes on those scenarios.
- Either way, consider enabling `eleven_flash_v2_5` (already configured,
  disabled) as a latency reference point — `vr-game-03`'s header already cites
  it as the tier real products ship on.

### 3 · Probe the key before spending

Points 1–2 are decisions on paper; this checks they're executable with the key
in `voice/.env` before spending metered quota to find out. Free metadata calls
answer most of it — see `scratch/probe_elevenlabs_v3.sh`.

1. Does this plan have `eleven_v3` access? (`GET /v1/models`)
2. Does it have `eleven_v3_conversational` access? (GA only ~3 weeks old)
3. Does `eleven_v3` work on the adapter's existing route
   `POST /v1/text-to-speech/{voice_id}[/stream]`, or does it 422 (Text-to-
   Dialogue only)? — needs one tiny paid call.
4. Does `eleven_v3_conversational` work on that route? (expect no — WS only) —
   needs one tiny paid call.
5. Quota re-check (`GET /v1/user/subscription`). HANDOFF §13: last known
   ~112k chars; one 23-scenario pass ≈ 6k chars; you need 2 passes × up to
   2 arms + retries.

**Output:** a recorded table — model IDs × routes × (audio | 4xx) — that turns
1 and 2 from "we think" into "we verified", and says whether the next step is
a config line or an adapter rewrite.

---

## 1 · Config / code changes once 0 is settled

- **`models.yaml`:** copy the `elevenlabs-flash-v2-5` block, set
  `provider_model: eleven_v3`, `enabled: true`. Keep the same `voice_map`
  (Matilda / Eric + 6 NPC voices) so voices stay comparable — but verify each
  voice ID resolves under v3 (`GET /v1/voices`), a 404 on first call otherwise.
- **Price block:** check `elevenlabs.io/pricing/api` for the v3 rate (may be
  above multilingual-v2's $0.10/1k). Fresh `as_of` date.
- **Style / audio tags:** the adapter *drops* `style` for ElevenLabs today
  (records `params_unsupported`). v3 expressiveness is inline tags
  (`[whispers]`). Exercising it is adapter work; otherwise the 9 `styled_tts`
  scenarios run with style unsupported, same as v2 — and "emotion" needs the
  human panel anyway (HANDOFF point 5).
- **`eleven_v3_conversational`:** no Text-to-Dialogue / WebSocket path exists
  in the adapter. Real implementation work — scope it explicitly.
- **`max_ttfa_ms` flips the transport.** Adding it to a scenario sets
  `measure_ttfa=True` (`generate.py:275`) → that scenario's ElevenLabs call
  switches to the `/stream` route. Intended, but streamed clips are then
  assembled differently from non-streamed ones — keep it disclosed.

## 2 · What to record / notice on every v3 run

- **Model provenance per clip:** exact `provider_model`, `provider_version`,
  response `request-id`; Vertex Model Garden version if routed that way. Guard
  against a silent v2.5.
- **Two passes per arm** (`--label p1`, `--label p2`, whole bank each). The
  noise floor resets with v3; one pass is not evidence.
- **Voice caveat:** v3 delivery differs from v2 at the same voice ID. Footnote
  cross-version voice comparison as declared-different.
- **Gate pass rates per arm.** Expect more failures on exact-duration
  scenarios (`vr-ads-02`, `vr-drama-04`) and `max_silence_s` / `no_clipping`.
  Do not loosen gates to make v3 pass.
- **Keep local Whisper-medium ASR** (`8ffce31`). Do not switch back to Gemini
  ASR. Attribute any WER change to the model, not the instrument.
- **TTFA and whole-call latency in separate fields** (`ttfa_ms` vs
  `latency_ms`), never overwrite. Report TTFA in its own column. Expected
  honest outcome: neither quality arm hits 0.6–1 s. Streaming narrows, does
  not flip, the latency story — say that.
- **Judge still `gemini-2.5-flash`** judging a Google arm. Disclosed; still
  applies. Neutral-judge cross-check (~$5 OpenAI) is the outstanding
  high-value experiment.
- **0.05 winner band** (Ravi's flat band replacing a measured floor) still
  moves verdicts. A full v3 re-run gives a fresh measured floor — flag to Ravi.
- **Judge the batch runs this time:** `vr-ecom-07`, `vr-game-07`,
  `vr-game-03` (164 clips) carry no quality score.
- **Spend per phase** (tokens + USD). Mint the GCP token in the same command
  (1-hour expiry); `--budget` is a hard cap.
- **Withdraw v2 numbers.** Mark v2 runs superseded; boards must not show v2
  figures as current alongside v3.

## 3 · Re-grouping (task C) cautions

- **Re-tag, don't rename.** id change → hash change → orphaned runs.
- `INDUSTRY` in `runner/dashboard.py` maps id-prefix → label. The new split
  (call centre {retail / telco / banking} + micro-drama) needs tag-driven
  grouping — id-prefix can't separate retail/telco/banking *within* call centre.
- **Decide: relabel vs edit scripts.** Telco/banking have no scenarios.
  "Re-grouping, not new scenarios" conflicts with "telco/banking terminology
  needs adding" — editing a script changes what's spoken and breaks
  comparability. Resolve before touching scenario files.
- Likely mapping to confirm with Chom: `vr-ecom-06` KYC + `vr-ecom-01/03/04`
  → call centre/retail; `voi-tel-*` → call centre/telco; `vr-drama-*` →
  micro-drama; `vr-ads-*` → outside both.

## 4 · Acceptance criteria — done-conditions

- [ ] Vertex decision recorded in HANDOFF §15 before any v3 spend, 3 checks answered
- [ ] v3 configured; key probed; model identity verified per clip; both arms
      re-run from scratch, 2 passes each; v2 numbers withdrawn from the boards
- [ ] `max_ttfa_ms` on every responsiveness scenario; TTFA reported separately
      from whole-call latency; note that streaming does not flip the result
- [ ] Bank re-segmented into call centre (retail / telco / banking separate) +
      micro-drama, via tags not renames
- [ ] Voice drift in the client-facing output (EL 0.976 vs Gemini 0.859 +
      `vr-game-04` six-NPC gate failure), using `runner/voiceprint.py`
- [ ] Cost claim reconciled — our ~2.1× vs Chom's "one-fourth" — one number stated
- [ ] Dashboard aligned with image/video: separate client + internal reports,
      Gemini first column, ratings as %, metric renamed
      "Reliability (worst scenario rating)"

## 5 · Do NOT

- Start v3 spend before 0.1–0.3 are recorded
- Report whole-call latency as TTFA
- Switch ASR back to Gemini, or loosen gates so v3 passes
- Rename scenario ids, or edit scripts under the banner of "re-grouping"
- Leave v2 and v3 numbers side by side as if both current
- Quote a one-pass result
