# October 2026 text-to-speech run — 200 scenarios

Gemini 3.8 Flash TTS (`gemini-3-8-flash-tts`) against ElevenLabs Eleven v4
(`elevenlabs-v4`), one clip per scenario per model.

| Folder | Scenarios | Source |
|---|---|---|
| `english/` | 60 | New, written for this run. Each copies a v2 Hindi or Hinglish scenario's industry, use case, speaker, tests and difficulty, in Indian English. |
| `hinglish/` | 75 | v2 text-to-speech bank, Hindi in Roman or mixed script |
| `hindi/` | 50 | v2 text-to-speech bank, Hindi in Devanagari |
| `telugu/` | 9 | v2 text-to-speech bank, Telugu script (every one the bank has) |
| `tenglish/` | 6 | v2 text-to-speech bank, Telugu in Roman letters |

Each file's header says where it came from, its target customer, what it
tests and why it was picked. The v2 originals are on the scenario bank site
(`.../scenario-bank/v2/voice/<id>`).

This folder is outside `scenarios/`, so a plain `run` never picks it up. Name it.

## What is different from the September runs

- **Delivery instructions reach both models.** Gemini gets the style in
  `speech_metadata`; Eleven v4 gets it as one leading audio tag
  (`style_as_audio_tags` in its model block). In September, ElevenLabs got none.
- **Both models are told the language.** `hi-IN`, `te-IN` or `en-IN`, as
  `languageCode` for Gemini and `language_code` for ElevenLabs.
- **Nine scenes have two speakers** (`params.speakers`). Gemini uses its
  two-voice mode; ElevenLabs uses Text to Dialogue.
- **The transcript is not scored for Hindi, Hinglish, Telugu and Tenglish**
  (`asr_scored: false`). Whisper writes those languages in their own scripts,
  so a word error rate against a Roman-letter script would measure spelling,
  not speech. The transcript is kept; accuracy comes from the checklist,
  judged by listeners and the judge.
- **Time to first audio is measured on every clip, not gated** (`measure_ttfa`).
  The bank's "within 1 s" failed both models in September from this network,
  so as a gate it would fail every clip on both sides.
- **The voices are the September ones.** Scenarios name eight speaker types
  (`female_young` … `male_older`); every woman is ElevenLabs Matilda / Gemini
  Aoede and every man Eric / Charon, as in September. The older man is Bill /
  Fenrir (two more September voices) so two-voice scenes have two distinct
  voices. All are ElevenLabs stock voices: no voice slots needed. Age is not
  voiced, so a checklist line about a speaker's age is a limit of this cast.

## Before the run

1. **ElevenLabs characters.** One pass needs about 30,800 and two about 61,600.
   The two keys in `.env` (first and second) have 10,000 a month each.
2. **Gemini key.** Read from Secret Manager through `studies-runner`; needs a
   current `gcloud` login as chinnamnaidu@tilicho.in.
3. **Listen to one clip per voice** on both arms before the full run. A voice
   that sounds wrong for its age band is a casting fix in `models.yaml`.

## Run

```bash
cd voice
set -a && . ./.env && set +a
.venv/bin/python -m runner.cli --modality voice --scenarios scenarios-oct-2026 run --bundle --label oct-p1 --budget 10
.venv/bin/python -m runner.cli --modality voice judge
.venv/bin/python -m runner.cli --modality voice report --open
```

Repeat `run` with `--label oct-p2` for the second pass. The estimate at list
prices is $2.73 per pass for all 400 clips.
