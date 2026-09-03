"""
OpenAI text-to-speech (gpt-4o-mini-tts, tts-1, tts-1-hd).

ONE adapter class serves every OpenAI TTS SKU; which SKU is a config
decision (`provider_model`), and so is how it bills - gpt-4o-mini-tts is
token-priced, tts-1 is character-priced, and neither fact lives here. That
split is the point of the seam: this file knows how to talk to OpenAI, and
nothing else.

USAGE REPORTING. The /v1/audio/speech endpoint returns raw audio and no
usage object. So this adapter reports the one billing quantity it knows
exactly - characters sent - and leaves `reported=False`. cost.py then prices
a character-billed SKU exactly, and prices a token-billed SKU from the
declared estimation assumptions in models.yaml, labelling it. Nothing here
guesses; the assumption lives in config where a reader can see it.
"""

from __future__ import annotations

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

# response_format values the endpoint accepts.
_FORMATS = {"wav", "mp3", "opus", "aac", "flac", "pcm"}


class OpenAITtsAdapter(BaseAdapter):
    ext = "wav"

    def __init__(self, spec) -> None:
        super().__init__(spec)
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - environment
            raise ProviderError(
                "the `openai` package is not installed - `uv pip install -e '.[openai]'`"
            ) from exc
        import os

        key = os.environ.get(spec.auth_env)
        if not key:
            raise AuthError(f"${spec.auth_env} is not set")
        self._client = OpenAI(api_key=key)
        self._fmt = str(spec.params.get("format", "wav")).lower()
        if self._fmt not in _FORMATS:
            raise ProviderError(
                f"model '{spec.id}': params.format={self._fmt!r} is not one of {sorted(_FORMATS)}"
            )
        self.ext = self._fmt

    def run(self, req: GenRequest) -> GenResult:
        # Requested params = model defaults overlaid with the scenario's.
        # Same object shape for every model, so a knob one provider ignores is
        # visible rather than quietly dropped.
        requested: dict[str, Any] = {**self.spec.params, **req.params}
        applied: dict[str, Any] = {}

        kwargs: dict[str, Any] = {
            "model": self.spec.provider_model,
            "input": req.text,
            "response_format": self._fmt,
        }
        applied["format"] = self._fmt

        if req.voice_id:
            kwargs["voice"] = req.voice_id
            applied["voice"] = requested.get("voice", req.voice_logical)

        speed = requested.get("speed")
        if speed is not None:
            kwargs["speed"] = float(speed)
            applied["speed"] = speed

        # `instructions` carries the style directive, and only the SKUs that
        # declare styled_tts support get it. Sending it to tts-1 would be
        # silently ignored by the API - which is exactly the kind of
        # difference params_unsupported exists to surface, so we let it show.
        if req.style and "styled_tts" in self.supports:
            style_line = req.style
            if req.language:
                style_line = f"{style_line}\nLanguage/accent: {req.language}"
            kwargs["instructions"] = style_line
            applied["style"] = req.style
            if req.language:
                applied["language"] = req.language

        try:
            resp = self._client.audio.speech.create(timeout=req.timeout_s, **kwargs)
            data = resp.content
        except Exception as exc:  # noqa: BLE001 - mapped onto our taxonomy below
            raise _map_error(exc) from exc

        if not data:
            raise ProviderError("OpenAI returned an empty audio body")

        request_id = None
        raw_response = getattr(resp, "response", None)
        if raw_response is not None:
            request_id = raw_response.headers.get("x-request-id")

        return GenResult(
            data=data,
            mime=f"audio/{'wav' if self._fmt == 'wav' else self._fmt}",
            provider_version=self.spec.provider_model,
            usage=Usage(
                reported=False,
                characters=len(req.text),
                raw={"note": "OpenAI /v1/audio/speech returns no usage object"},
            ),
            applied_params=applied,
            provider_request_id=request_id,
        )


def _map_error(exc: Exception):
    """Map an OpenAI SDK exception onto the runner's retry taxonomy."""
    name = type(exc).__name__
    msg = str(exc)
    if name in ("RateLimitError",):
        # OpenAI returns 429 for two very different things: a real per-minute
        # rate limit (wait and retry) and an exhausted balance or quota (no
        # amount of waiting helps). Retrying the second wastes three attempts
        # and three backoffs per cell - ten minutes to learn what the first
        # response already said. Separate them by the code the body carries.
        low = msg.lower()
        if "insufficient_quota" in low or "no credits" in low or "billing" in low:
            err = ProviderError(msg)
            err.status = "quota_exhausted"
            err.retryable = False
            return err
        retry_after = None
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                retry_after = float(resp.headers.get("retry-after", "") or 0) or None
            except (TypeError, ValueError):
                retry_after = None
        return RateLimited(msg, retry_after)
    if name in ("APITimeoutError",):
        return Timeout(msg)
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return AuthError(msg)
    if name in ("BadRequestError",):
        # OpenAI reports content refusals as 400s carrying a policy marker.
        low = msg.lower()
        if "safety" in low or "policy" in low or "refus" in low:
            return SafetyRefusal(msg)
        # A genuinely malformed request is not retryable either.
        err = ProviderError(msg)
        err.retryable = False
        return err
    return ProviderError(f"{name}: {msg}")
