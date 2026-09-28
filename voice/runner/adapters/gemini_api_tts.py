"""
Gemini 3.8 TTS through the Gemini API (generativelanguage.googleapis.com).

WHY A SEPARATE ADAPTER. Gemini 3.8 Flash TTS is served by the Gemini API and,
as of 2026-09-28, not by Vertex AI - so it cannot go through gemini_tts.py's
Vertex doorway. When 3.8 lands on Vertex, the migration is `adapter:
gemini_tts` in models.yaml and deleting this module; nothing else calls it.

WHAT A LIVE PROBE SHOWED (2026-09-28, temp-genmedia-study, one sentence per
call - these are observations, not doc quotes):

  - AUTH. A service-account OAuth token is refused: 403 "Access to Gemini API
    is restricted with service accounts. Use authorization keys instead."
    (With only the cloud-platform scope it fails earlier, on scope.) So the
    credential is an authorization key bound to studies-runner, held in
    Secret Manager and fetched at start-up. It is never in .env, never logged.
  - FORMAT. A unary call with no responseFormat returns a WAV (audio/wav,
    RIFF header); a streamed call returns raw PCM chunks
    ("audio/l16; rate=24000; channels=1"). We ask for AUDIO_L16 on both, so
    one path wraps PCM into WAV exactly once, at the rate the mimeType states.
  - STYLE. The text is a VERBATIM TRANSCRIPT; a "Say the following..."
    preamble would be spoken aloud. The style travels in
    `parts[].speech_metadata.style` and was not spoken in the probe clip.
  - VOICE. `voiceConfig.prebuiltVoiceConfig.voiceName` - the same shape the
    Vertex adapter sends, so the voice_map carries over unchanged.

The runner owns retries (base.py): this module only maps 429 -> RateLimited
and 5xx -> ProviderError so the runner's backoff sees them as retryable.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from typing import Any

from ..cost import Usage
from .base import (
    AuthError,
    BaseAdapter,
    GenRequest,
    GenResult,
    ProviderError,
    RateLimited,
    SafetyRefusal,
    Timeout,
)
from .gemini_tts import GEMINI_PCM_RATE, _wrap_pcm_as_wav

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
SECRET_MANAGER_BASE = "https://secretmanager.googleapis.com/v1"
_REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
_SECRET_NAME = re.compile(r"^projects/[^/]+/secrets/[^/]+/versions/[^/]+$")

# One fetch per secret per process, however many adapters are built.
_key_cache: dict[str, str] = {}
_key_lock = threading.Lock()


class GeminiApiTtsAdapter(BaseAdapter):
    ext = "wav"

    def __init__(self, spec) -> None:
        super().__init__(spec)
        try:
            import requests  # noqa: F401 - a google-auth[requests] dependency
        except ImportError as exc:  # pragma: no cover - environment
            raise ProviderError(
                "the `requests` package is not installed - `uv pip install -e '.[google]'`"
            ) from exc

        # auth_env holds the secret's RESOURCE NAME, not the key - so the
        # preflight check "is auth_env set" still means "is this arm wired".
        secret = (os.environ.get(spec.auth_env) or "").strip()
        if not secret:
            raise AuthError(f"${spec.auth_env} is not set")
        if not _SECRET_NAME.match(secret):
            raise AuthError(
                f"${spec.auth_env} must be a Secret Manager version name "
                f"(projects/<p>/secrets/<s>/versions/<v>), not a key"
            )
        self._api_key = _fetch_api_key(secret)
        self.gateway = "gemini-api-key"

    # ------------------------------------------------------------------- run

    def build_body(self, req: GenRequest) -> tuple[dict[str, Any], dict[str, Any]]:
        """The request body, and what it honours. Split out so tests can see it."""
        part: dict[str, Any] = {"text": req.text}
        applied: dict[str, Any] = {
            "voice": (req.params.get("voice") or req.voice_logical),
            "format": "wav",
            "sample_rate": GEMINI_PCM_RATE,
        }
        if req.style and "styled_tts" in self.supports:
            part["speech_metadata"] = {"style": req.style}
            applied["style"] = req.style
        body = {
            "contents": [{"role": "user", "parts": [part]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "responseFormat": {
                    "audio": {"mimeType": "AUDIO_L16", "sampleRate": GEMINI_PCM_RATE}
                },
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": req.voice_id}}
                },
            },
        }
        return body, applied

    def run(self, req: GenRequest) -> GenResult:
        try:
            return self._run(req)
        except Exception as exc:
            # Last line of defence: no message leaving this adapter carries
            # the key, whatever layer produced it.
            _scrub_exception(exc, self._api_key)
            raise

    def _run(self, req: GenRequest) -> GenResult:
        import requests

        if not req.voice_id:
            raise ProviderError(
                f"model '{self.id}': Gemini TTS needs a prebuilt voice name - the "
                f"scenario's logical voice is not in this model's voice_map"
            )
        body, applied = self.build_body(req)
        model = self.spec.provider_model
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}
        timeout = (10.0, req.timeout_s)

        chunks: list[dict[str, Any]] = []
        ttfa_ms: int | None = None
        audio: list[tuple[str, bytes]] = []
        try:
            if req.measure_ttfa:
                # Time to the first event that actually carries audio - not
                # the first event of any kind, which would flatter the number.
                started = time.perf_counter()
                with requests.post(
                    f"{API_BASE}/models/{model}:streamGenerateContent?alt=sse",
                    headers=headers, json=body, timeout=timeout, stream=True,
                ) as resp:
                    _raise_for_status(resp)
                    for chunk in _iter_sse(resp):
                        chunks.append(chunk)
                        got = _extract_audio(chunk)
                        if got:
                            if ttfa_ms is None:
                                ttfa_ms = int((time.perf_counter() - started) * 1000)
                            audio.extend(got)
            else:
                resp = requests.post(
                    f"{API_BASE}/models/{model}:generateContent",
                    headers=headers, json=body, timeout=timeout,
                )
                _raise_for_status(resp)
                chunk = resp.json()
                chunks.append(chunk)
                audio.extend(_extract_audio(chunk))
        except requests.Timeout as exc:
            raise Timeout(f"{type(exc).__name__} calling the Gemini API") from exc
        except requests.RequestException as exc:
            # Not str(exc): a requests error can echo the request it failed on.
            raise ProviderError(f"{type(exc).__name__} calling the Gemini API") from exc

        for chunk in chunks:
            _raise_if_refused(chunk)
        _raise_if_truncated(chunks)
        if not audio:
            raise ProviderError(
                f"Gemini API returned no inlineData audio part "
                f"(finishReason={_finish_reason(chunks)}, events={len(chunks)})"
            )
        wav, rate = _assemble_wav(audio)
        applied["sample_rate"] = rate

        last = chunks[-1] if chunks else {}
        um = next((c["usageMetadata"] for c in reversed(chunks) if c.get("usageMetadata")), None)
        usage = Usage(
            reported=um is not None,
            input_tokens=um.get("promptTokenCount") if um else None,
            audio_out_tokens=um.get("candidatesTokenCount") if um else None,
            characters=len(req.text),
            raw={k: um[k] for k in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")
                 if um and um.get(k) is not None},
        )
        return GenResult(
            data=wav,
            mime="audio/wav",
            provider_version=last.get("modelVersion") or model,
            usage=usage,
            applied_params=applied,
            provider_request_id=last.get("responseId"),
            ttfa_ms=ttfa_ms,
        )


# ------------------------------------------------------------------ secret


def _fetch_api_key(secret: str) -> str:
    """
    Read the key from Secret Manager with the ambient Google identity: the
    attached service account on Cloud Run/GCE/GKE, ADC on a laptop. Nothing
    about the key reaches an exception or a log line.
    """
    with _key_lock:
        if secret in _key_cache:
            return _key_cache[secret]

        import google.auth
        from google.auth.exceptions import DefaultCredentialsError, RefreshError
        from google.auth.transport.requests import AuthorizedSession

        from ..gcp_auth import vertex_credentials

        creds = vertex_credentials()
        if creds is None:
            try:
                creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            except DefaultCredentialsError as exc:
                raise AuthError(
                    "no Google credential to read the Gemini API key from Secret Manager - "
                    "attach studies-runner, or run `gcloud auth application-default login`"
                ) from exc
        try:
            resp = AuthorizedSession(creds).get(f"{SECRET_MANAGER_BASE}/{secret}:access", timeout=30)
        except RefreshError as exc:
            raise AuthError(f"could not refresh the Google credential: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            # The error body names the secret and the principal, never the payload.
            status = _error_status(resp)
            raise AuthError(f"Secret Manager refused {secret}: {resp.status_code} {status}")
        try:
            key = base64.b64decode(resp.json()["payload"]["data"]).decode().strip()
        except (KeyError, ValueError) as exc:
            raise AuthError(f"Secret Manager returned no readable payload for {secret}") from exc
        if not key:
            raise AuthError(f"secret {secret} is empty")
        _key_cache[secret] = key
        return key


def _scrub_exception(exc: BaseException, key: str) -> None:
    if not key:
        return
    for e in (exc, exc.__cause__, exc.__context__):
        if e is not None and any(key in str(a) for a in e.args):
            e.args = tuple(str(a).replace(key, "<redacted>") for a in e.args)


# ------------------------------------------------------------------ parsing


def _iter_sse(resp):
    """Server-sent events: one JSON object per `data:` line."""
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                yield json.loads(payload)


def _extract_audio(chunk: dict[str, Any]) -> list[tuple[str, bytes]]:
    out = []
    for cand in chunk.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                out.append((mime, base64.b64decode(inline["data"])))
    return out


def _pcm_rate(mime: str) -> int:
    """'audio/l16; rate=24000; channels=1' -> 24000. Refuses what it cannot wrap."""
    low = mime.lower()
    if not (low.startswith("audio/l16") or low.startswith("audio/pcm")):
        raise ProviderError(f"Gemini API returned audio as {mime!r}, not 16-bit PCM")
    if "channels=" in low and "channels=1" not in low:
        raise ProviderError(f"Gemini API returned multi-channel audio ({mime!r})")
    m = re.search(r"rate=(\d+)", low)
    return int(m.group(1)) if m else GEMINI_PCM_RATE


def _assemble_wav(audio: list[tuple[str, bytes]]) -> tuple[bytes, int]:
    # AUDIO_L16 was asked for; if the service sent a WAV anyway, keep it
    # rather than nesting a second header inside it.
    if len(audio) == 1 and audio[0][1][:4] == b"RIFF":
        data = audio[0][1]
        return data, int.from_bytes(data[24:28], "little")
    rates = {_pcm_rate(mime) for mime, _ in audio}
    if len(rates) != 1:
        raise ProviderError(f"Gemini API mixed sample rates in one response: {sorted(rates)}")
    rate = rates.pop()
    return _wrap_pcm_as_wav(b"".join(pcm for _, pcm in audio), rate), rate


def _finish_reason(chunks: list[dict[str, Any]]) -> str | None:
    for chunk in reversed(chunks):
        for cand in chunk.get("candidates") or []:
            if cand.get("finishReason"):
                return cand["finishReason"]
    return None


def _raise_if_refused(chunk: dict[str, Any]) -> None:
    block = (chunk.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise SafetyRefusal(f"prompt blocked: {block}")
    for cand in chunk.get("candidates") or []:
        if cand.get("finishReason") in _REFUSAL_REASONS:
            raise SafetyRefusal(f"generation stopped: {cand['finishReason']}")


def _raise_if_truncated(chunks: list[dict[str, Any]]) -> None:
    # 16,384 output tokens is ~3 minutes of speech. A clip cut at the limit
    # is still audio, and would be scored as if the model dropped the ending.
    if _finish_reason(chunks) == "MAX_TOKENS":
        err = ProviderError(
            "Gemini API stopped at its output-token limit (~3 min of speech) - "
            "the script is too long for one call"
        )
        err.retryable = False  # the same script hits the same limit
        raise err


def _error_status(resp) -> str:
    try:
        err = resp.json().get("error") or {}
        return f"{err.get('status', '')}: {err.get('message', '')}".strip(": ")
    except ValueError:
        return resp.text[:300]


def _raise_for_status(resp) -> None:
    if resp.status_code < 400:
        return
    msg = f"{resp.status_code} {_error_status(resp)}"
    low = msg.lower()
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After") or ""
        raise RateLimited(msg, retry_after=float(retry_after) if retry_after.isdigit() else None)
    if resp.status_code in (401, 403):
        raise AuthError(msg)
    if resp.status_code in (408, 504) or "deadline" in low:
        raise Timeout(msg)
    if "safety" in low or "blocked" in low or "prohibited" in low:
        raise SafetyRefusal(msg)
    raise ProviderError(msg)
