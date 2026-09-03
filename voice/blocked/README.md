# Blocked scenarios — written, validated, not runnable yet

All 28 scenarios from the workbook's **"Voice — real use cases"** tab exist.
**11 run**; these **17 do not**, because the measurement each one specifies
needs a capability this harness does not have.

They live outside `scenarios/` on purpose. Everything under `scenarios/` is
picked up by `runner.cli run`, and a scenario that generates a clip while
silently failing to measure its own claim is the exact failure mode this
project exists to avoid — it produces a number that looks like evidence.

They are still validated by `tests/test_scenario_bank.py`, so each is correct
on the day its blocker is lifted: drop it into `scenarios/real-use-cases/`
and it runs.

## What blocks what

| Capability needed | Scenarios | Feasibility |
|---|---|---|
| **Speaker-embedding cosine** | `DRAMA-01`, `DRAMA-03`, `GAME-04`, `GAME-06`, and half of `ADS-01` | **Verified feasible 2026-09-03** — `resemblyzer` loads and discriminates on our own clips (one arm scored 0.969 against itself across scenarios, the other 0.776). A build, not a research problem. **Highest leverage: unlocks 5.** |
| **Batch throughput reporting** | `ECOM-07`, `GAME-07`, `ADS-01` | No new instrument — cost, latency and failures are already recorded per call. Needs a batch runner and a rollup that divides one by the other. |
| **Blind human listener panel** | `DRAMA-07`, `ADS-04`, `ADS-07` | Not a code problem. The LLM judge could label these, but "a model recognises the emotion" is not "an audience does" — and for regulated copy the approving client's ear is the one that counts. Related: this project's own calibration gate (2 humans × 5 clips) has never run either. |
| **Multilingual ASR + native review** | `ECOM-05`, `ADS-03`, and the language half of `DRAMA-03`/`GAME-06` | The shipped ASR is Whisper `small.en` — English only. A multilingual model gives whole-clip WER, but these specify **per-language segment** WER plus a native reviewer at each switch point. |
| **Streaming / time-to-first-audio** | `GAME-03`, and `GAME-05`'s latency half | The harness makes a blocking call and receives a finished file, so the only latency available is whole-call — a different quantity by an order of magnitude. Reporting it as TTFA would be a lie. |
| **Character-exact alphanumeric extraction** | `ECOM-06` | Smallest of the blocked builds: the digit path again, over letters, plus a per-character-class confusion matrix. |
| **Far-field acoustic simulation** | `DRAMA-05` | Band-limit + room impulse + distance attenuation. Tractable with numpy/scipy. |
| **Multi-speaker synthesis + diarisation** | `DRAMA-06` | Two blockers at once — the adapters send one script in one voice, so a four-character scene cannot even be requested. Largest build here. |
| **Locally-hosted arms** | `GAME-05` | Not a harness feature. Both arms are hosted APIs with no compute knob; answering this honestly means a different comparison with different arms. |

## The one worth building first

**`GAME-03`** is the strongest Google story in the entire workbook: Fortnite's
AI Darth Vader ran on **Gemini 2.0 Flash** for responses, with the James Earl
Jones estate's permission — the largest shipped example of real-time
conversational NPCs already uses a Google model. It is blocked on streaming
support, and it is the one scenario where a passing number would let a seller
name a reference the customer has already heard of.

**Speaker cosine unlocks the most (5)**, is verified feasible, and would also
complete the "same voice across a batch" half of two claims `vr-game-01` and
`vr-game-02` already make but cannot currently support.

## Reading these files

Each carries the same header as a runnable scenario — real application, named
source, and the sales claim it would support — plus a **`BLOCKED BY`** block
stating exactly what is missing and what it would take. Nothing here is a
sketch; the scripts are final and the checks are the ones that will run.
