# Video lane — full report
**Gemini Omni Flash vs Seedance 2.5 · edits + ads scope · 10-11 September 2026**

Scope set by the 2026-09-04 Google review: video edits and ads. 20 scenarios
in the bank, 18 runnable (VID-EDIT-02 retired, VID-EDIT-10 has no source).
Everything below is one seed. The sheet asks for three.

---

## 1. Headline

**Ads — 8 usable pairs of 10.**

| | Omni Flash | Seedance 2.5 |
|---|---|---|
| Mean rating | 9.16 | **9.39** |
| Win-tie-loss | 2 | **4** (2 ties) |
| Delivered | **10 of 10** | 8 of 10 |
| Cost per clip | **$1.22** | $4.18 |
| Latency, median | **71s** | 303s |

Seedance is better where it delivers and worse at delivering: **3.4x the
cost, 4.3x the latency, and two scenarios refused outright.** Which model
"wins" depends entirely on whether refusals and cost matter to the buyer.

**Edits — 1 usable pair of 3 attempted.** Not a quality result. The finding is
that both arms have hard capability limits, and they are different limits.

---

## 2. Ads, scenario by scenario

| scenario | Omni | Seedance | result |
|---|---|---|---|
| VID-AD-01 | 10.00 | 9.40 | Omni |
| VID-AD-02 | 9.15 | 7.95 | Omni |
| VID-AD-03 | 8.45 | **10.00** | Seedance |
| VID-AD-04 | 8.75 | 9.15 | tie |
| VID-AD-06 | 9.07 | **10.00** | Seedance |
| VID-AD-07 | 9.40 | 9.95 | Seedance |
| VID-AD-09 | 10.00 | 9.70 | tie |
| VID-AD-10 | 8.43 | 9.00 | Seedance |
| VID-AD-05 | 9.00 | refused | audio/copyright |
| VID-AD-08 | 9.53 | refused | audio/copyright |

Ties use the 0.5-point band (5 percentage points).

**The two refusals are a product fact, not missing data.**
`OutputAudioSensitiveContentDetected.PolicyViolation` — "the output audio may
be related to copyright restrictions" — after 4-5 minutes of generation. Both
would have passed with audio off, which is how the scenarios were configured
before the study lead enabled audio for this run.

---

## 3. Edits — three walls, none of them about quality

| scenario | Omni | Seedance | |
|---|---|---|---|
| VID-EDIT-07 | 10.00 | 10.00 | the only pair — a tie |
| VID-EDIT-01 | 10.00 | refused | input contains a real person |
| VID-EDIT-03 | rejected | 9.00 | source exceeds Omni's 10s limit |

1. **Seedance refuses some input video containing an identifiable person**
   (`InputVideoSensitiveContentDetected.PrivacyInformation`). Not
   consistently: it edited VID-EDIT-03, whose brief is explicitly *"keep the
   person, their motion and the lighting on them exactly as they are"*, and
   refused VID-EDIT-01. What separates them is unknown.
2. **Omni rejects any edit longer than 10 seconds** — *"Editing duration
   11.3333 exceeds maximum duration 10"*. That excludes four of the eight
   runnable edits outright (11.24s, 11.61s, 16.80s, 19.20s).
3. **Omni refuses some clips on `recitation`** — VID-EDIT-04 and VID-EDIT-06,
   all three attempts each.

For edit work on real footage this is more decision-useful than a score: one
arm declines at the door on people, the other cannot touch anything over ten
seconds.

---

## 4. Money

| | |
|---|---|
| Recorded, BytePlus | **$60.76** |
| Recorded, Google Vertex | **$18.89** |
| **Actually billed by BytePlus** | **$94.21** |
| **Unrecorded** | **$33.45** — 8 orphaned clips |

The gap is closed and cannot recur, but it was real: creates timed out at TCP
level with the request already delivered, the runner retried, and each retry
bought another clip nobody could attribute to a scenario.

**Measured unit costs** (both now exact, from 16+ tasks each):

| | per clip | per second |
|---|---|---|
| Seedance generation, 1080p+audio | $4.1818 | $0.5227 |
| Seedance **edit**, 1080p | varies | **$1.01-1.04** |
| Omni generation, 1080p | $1.2180 | $0.1518 |
| Omni edit, 1080p | varies | $0.161-0.166 |

An edit bills roughly **twice** a generation of the same length: the source
clip's tokens land inside `completion_tokens`. The documented "$6.40/1M
video-input tier" does not exist — the rate stays $10.70 and the count doubles.

The **28% 1080p promo is not visible in token billing.** Whether it applies at
invoice time is unresolved and worth ~$12 across this work. Only the CSV bill
export settles it.

---

## 5. What was wrong with our own instrument

Five defects found and fixed. Two of them invalidate earlier results.

**Blind judging was not blind.** Both providers stamp C2PA provenance into the
mp4 container — Seedance embeds the literal model id `dreamina-seedance-2-5`,
Omni embeds `Google LLC` and `encoder=Google`. The judge is a Gemini model
reading mp4 natively. Fixed by a lossless ffmpeg remux before judging.
**The 2026-09-03 pilot's 9.26-vs-9.27 tie was produced without this and its
blinding cannot be claimed.**

**A gate failed both models for being right.** VID-AD-06 asked for 9:16
vertical; its check demanded `min_width: 1280`, which no portrait clip can
satisfy. Both arms delivered exactly 1080x1920 and both scored 0.00. The
re-run scored them 9.07 and 10.00 — the zeros were hiding a Seedance win.

**A transient 429 permanently discarded a paid clip.** VID-AD-07's Seedance
clip was fine; Vertex rate-limited the judge, the cell went to `unjudged`
(terminal by design) and a placeholder score row then blocked re-scoring
forever. Recovered; `--retry-unjudged` added.

**Omni's cost was understated.** Its price block declared no input rate, so
`cost.py` priced input tokens at zero on every asset-fed run.

**The budget cap did not hold.** A $14 cap let $18.94 through: the guard
checked spend and recorded it as separate steps, so two concurrent calls both
passed at `spent == 0`, and the estimate was 2.7x under. Now reserves before
the call, and estimates are per-task and worst-case.

---

## 6. Open

1. **Confirm the BytePlus balance from the CSV export.** The console read ~$5
   high all day. Remaining is somewhere between ~$3 and -$2.40.
2. **Whether the 1080p promo applied.** Same export.
3. **The remaining 5 edits** need ~$174 across both arms at honest estimates —
   and four of them exceed Omni's 10s limit, so they can never produce a pair.
   Re-cutting those sources under 10s is the only route to a real edits
   comparison.
4. **Two seeds still missing.** The sheet asks for three.
5. **VID-AD-05 / VID-AD-08** would pair if run with audio off — but that
   would hide the refusal, which is itself the finding.
