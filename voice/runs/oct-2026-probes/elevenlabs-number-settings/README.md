# ElevenLabs number-reading settings test - 2026-10-09

Why: in bank-en-01..03 eleven_v4 read every Indian-grouped rupee amount the
international way ("one million rupees" for Rs 10,00,000), while Gemini, which
is sent `languageCode: en-IN`, read them as lakh. This test asks whether any
documented ElevenLabs setting changes that. Outside the study; not scored.

Text: "Your fixed deposit of Rs 10,00,000 matures next month." (55 characters)
Model eleven_v4, voice Bill (pqHfZKP75CvOlQylNhV4), first key, one call each, no retries.

| Variant | Result |
|---|---|
| 1 `language_code: en-IN` | Refused, HTTP 400: "Model 'eleven_v4' does not support language_code 'en-IN'." No audio. |
| 2 `apply_text_normalization: off` | Transcribed "Your fixed deposit of R.S. Dunlac Resort matures next month." (same with digits banned, and as Hindi). "Rs" read as letters; the amount is not intelligible to the transcriber. Needs a listen. |
| 3 `apply_text_normalization: on` | "Your fixed deposit of one million rupees matures next month." Same as the default (auto). |

Files: calls.json (bodies sent, status, request ids), transcripts.txt, the two .wav clips.
Characters: 110 sent (55 x 2); the refused call produced no audio.
