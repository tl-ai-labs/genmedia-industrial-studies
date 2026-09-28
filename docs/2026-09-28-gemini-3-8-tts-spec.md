# Gemini 3.8 Flash TTS — integration spec

Verified against the live API on 2026-09-28 (project `temp-genmedia-study`),
then end to end through the runner (`voi-tel-01`: WER 0.0, all 12 digits
exact, $0.0024). Where this spec differs from the brief it replaces, the
difference is marked **Corrected** and comes from an observed response, not
from docs.

Reference implementation: `voice/runner/adapters/gemini_api_tts.py`, with
its tests in `voice/tests/test_gemini_api_tts.py`.

## The three things that cost the most time

1. **It is not on Vertex AI.** Use `generativelanguage.googleapis.com`, not
   `aiplatform.googleapis.com`, and never `vertexai=True`.
2. **Service accounts are rejected.** An OAuth token from a service account
   gets `403 "Access to Gemini API is restricted with service accounts. Use
   authorization keys instead."` Use the authorization key.
3. **The audio format depends on how you call it.** A unary call with no
   format requested returns a finished WAV. A streamed call returns raw PCM.
   Code that always adds a WAV header produces a double-header file on unary
   calls, and code that never adds one produces an unplayable file on
   streamed calls. Both look like a broken model. Ask for the format
   explicitly and branch on `mimeType` (see [Response](#response)).

## Authentication

| Case | Result | What to do |
|---|---|---|
| `x-goog-api-key: <authorization key>` | 200 | The only working route |
| Service-account bearer token, `cloud-platform` scope only | 403 `ACCESS_TOKEN_SCOPE_INSUFFICIENT` | Don't use |
| Service-account bearer token plus the `generative-language.retriever` scope | 403 "restricted with service accounts" | Don't use |
| Key in `.env`, in code, or in a log line | — | Never |

The key is an authorization key bound to `studies-runner`. It is stored in
Secret Manager and read once at process start:

```
Secret:   projects/temp-genmedia-study/secrets/gemini-tts-key/versions/latest
Identity: studies-runner@temp-genmedia-study.iam.gserviceaccount.com
          (has roles/secretmanager.secretAccessor on that secret)
```

- **Cloud Run / GCE / GKE:** attach `studies-runner` as the service account.
  The Secret Manager call authenticates with no credential in the image.
- **Local:** `gcloud auth application-default login` (or ADC impersonating
  `studies-runner`), then read the same secret.
- **In this repo:** `voice/.env` holds the secret's *resource name* in
  `GEMINI_API_KEY_SECRET`, never the key. The adapter refuses a value that
  isn't a `projects/*/secrets/*/versions/*` name, so a key pasted there by
  mistake fails loudly and isn't echoed.

The service account is used to *read the secret*. It never calls the Gemini
API itself.

## Request

```
POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent
POST .../models/gemini-3.8-flash-tts:streamGenerateContent?alt=sse   (streamed)
x-goog-api-key: <key>
```

```json
{
  "contents": [{
    "role": "user",
    "parts": [{
      "text": "<verbatim transcript>",
      "speech_metadata": {"style": "calm, reassuring"}
    }]
  }],
  "generationConfig": {
    "responseModalities": ["AUDIO"],
    "responseFormat": {"audio": {"mimeType": "AUDIO_L16", "sampleRate": 24000}},
    "speechConfig": {
      "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Aoede"}}
    }
  }
}
```

| Field | Rule |
|---|---|
| `text` | A **verbatim transcript**. Unlike 3.1, a "Say the following in this style…" preamble is spoken aloud. |
| `speech_metadata.style` | Optional. Where the style goes. Probe: the style text was not spoken. |
| `responseFormat` | **Always send it.** It makes unary and streamed calls return the same format. |
| `voiceConfig` | `prebuiltVoiceConfig.voiceName` works, and is the same shape Vertex uses. (`voiceConfig.voice` was also accepted, but we don't rely on it.) |

## Response

The audio is base64 in `candidates[].content.parts[].inlineData.data`. What
the bytes are depends on the call:

| Case | Observed `mimeType` | Bytes | Handling |
|---|---|---|---|
| Unary, with `responseFormat` `AUDIO_L16` | `audio/l16; rate=24000; channels=1` | Raw PCM, s16le mono | Add the WAV header once, at the rate from `mimeType` |
| Unary, **no** `responseFormat` | `audio/wav` | A complete WAV (starts `RIFF`) | Keep as-is. **Corrected:** the brief said raw PCM here. |
| Streamed, with or without `responseFormat` | `audio/l16; rate=24000; channels=1` per event (~50 events per sentence) | Raw PCM fragments | Concatenate all fragments, then add the header once |
| `audio/l16` or `audio/pcm` with a different `rate=` | — | Raw PCM | Use that rate in the header. **Never hard-code 24000.** |
| Fragments disagree on rate | — | — | Error: don't stitch mixed rates |
| `channels=` other than 1 | — | — | Error: the header we write is mono |
| Any other type (`audio/mpeg`, …) | — | — | Error: don't save it under a `.wav` name |
| No `inlineData` part at all | — | — | Error naming `finishReason` and the event count |

Decision rule, in order:

```
if one part and bytes start with b"RIFF"   -> it is already a WAV; keep it
elif mimeType is audio/l16 or audio/pcm    -> PCM; rate = mimeType's rate=,
                                              default 24000 only if absent
else                                       -> refuse
```

Duration in seconds is `len(pcm) / 2 / rate` for mono 16-bit audio.

## Metadata you get back

| Field | Example (e2e run) | Use |
|---|---|---|
| `modelVersion` | `gemini-3.8-flash-tts` | Provenance |
| `responseId` | `4WS6aunwGuCcjuMP36a58AU` | Tracing a call with Google |
| `usageMetadata.promptTokenCount` | 21 | Input cost |
| `usageMetadata.candidatesTokenCount` | 268 (6.56 s of audio) | Audio output cost (~41 tokens/s) |
| `usageMetadata.candidatesTokensDetails` | `[{modality: AUDIO, …}]` | Confirms the output modality |
| `usageMetadata.serviceTier` | `standard` | Which price tier billed the call |
| `candidates[].finishReason` | `STOP` | See [Finish reasons](#finish-reasons) |

Usage is reported by the API, so cost is exact rather than estimated. On a
stream, `usageMetadata` arrives on the last event.

## Finish reasons

| `finishReason` | Meaning | Retry? |
|---|---|---|
| `STOP` | Complete | — |
| `MAX_TOKENS` | Cut at the output limit (~3 min of speech). The audio is present but truncated. | **No.** The same text hits the same limit. Reject the clip, don't score it. |
| `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`, `RECITATION` or `promptFeedback.blockReason` | Refused | **No.** Record it as a refusal. |

## Errors and retries

| HTTP | Class | Retry? |
|---|---|---|
| 429 | Rate limited | Yes. Honour `Retry-After`, otherwise exponential backoff. |
| 500, 502, 503 | Provider error | Yes, with exponential backoff |
| 408, 504, deadline | Timeout | Yes |
| 401, 403 | Auth | No |
| Other 4xx | Provider error | Treated as retryable today; fix the request instead |

In this repo, **the runner owns retries**: 3 attempts, exponential backoff
capped at 45 s, and `Retry-After` honoured (`voice/runner/generate.py`). The
adapter only classifies errors. Don't add a second retry loop inside an
adapter.

**No secret in any message.** Every exception leaving the adapter is scrubbed
of the key. Network errors are reported by type only, because a `requests`
error can echo request headers. Secret Manager errors name the secret and the
status, never the payload. The e2e run folder was scanned for the key, and
none was found.

## Limits

- Input: 8,192 tokens (~6,000 words)
- Output: 16,384 tokens (~3 minutes of speech)

**Long text.** The brief suggested splitting on paragraph boundaries and
concatenating the PCM. In the study runner we deliberately **don't**: joining
chunks changes what is being measured. An over-long script fails with
`MAX_TOKENS` instead. A production caller that does chunk should request
`AUDIO_L16` for every chunk, check that every chunk has the same rate, join
the PCM, and write one header at the end.

## Latency

Streamed first audio for a 36-character script: **1,837 ms** (whole call
4,232 ms). Unary calls took about 3 s per short sentence. This model is
request/response. Live conversational audio is a different model
(`gemini-3.8-live`) with a bidirectional protocol.

## Migrating to Vertex when 3.8 TTS lands there

1. In `voice/configs/models.yaml`, change the 3.8 blocks to `adapter:
   gemini_tts` and `auth_env: GOOGLE_APPLICATION_CREDENTIALS`.
2. Delete the `gemini-tts-key` secret and `GEMINI_API_KEY_SECRET`.
3. **Re-probe before trusting defaults.** The default format changed between
   3.1 (raw PCM) and 3.8 (WAV) without notice. Check the unary and streamed
   `mimeType`, and whether Vertex's 3.8 accepts `speech_metadata.style` (the
   Vertex adapter currently sends a style preamble, which 3.8 would speak
   aloud).
