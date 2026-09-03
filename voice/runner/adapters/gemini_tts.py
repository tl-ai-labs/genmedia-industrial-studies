"""
Google Gemini text-to-speech (google-genai SDK, Vertex or AI Studio).

Gemini TTS is TOKEN billed on both sides - text in and audio out - and it is
the one TTS provider here that actually returns a usage object. So this
adapter sets `reported=True` and the cost path prices it from the provider's
own numbers rather than from an assumption, which is what the plan asks for
and what the other two providers cannot offer.

The response carries raw PCM (24 kHz, 16-bit, mono) rather than a container,
so we wrap it into a WAV before handing it back - every artefact on disk is
a real playable file.
"""

from __future__ import annotations

import os
import struct
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

GEMINI_PCM_RATE = 24000


class GeminiTtsAdapter(BaseAdapter):
    ext = "wav"

    def __init__(self, spec) -> None:
        super().__init__(spec)
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - environment
            raise ProviderError(
                "the `google-genai` package is not installed - `uv pip install -e '.[google]'`"
            ) from exc

        if not os.environ.get(spec.auth_env):
            raise AuthError(f"${spec.auth_env} is not set")

        # Vertex when a project is configured (the ADC path this repo uses
        # elsewhere), AI Studio when only an API key is present. The doorway
        # is recorded on every row so two runs of "the same model" can never
        # be silently compared across two different APIs.
        project = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            self.gateway = "vertex-adc"
            self._client = genai.Client(
                vertexai=True, project=project, location=spec.region or "us-central1"
            )
        else:
            self.gateway = "ai-studio-key"
            self._client = genai.Client(api_key=os.environ[spec.auth_env])

    def run(self, req: GenRequest) -> GenResult:
        from google.genai import types

        if not req.voice_id:
            raise ProviderError(
                f"model '{self.id}': Gemini TTS needs a prebuilt voice name - the "
                f"scenario's logical voice is not in this model's voice_map"
            )
        applied: dict[str, Any] = {
            "voice": (req.params.get("voice") or req.voice_logical),
            "format": "wav",
            "sample_rate": GEMINI_PCM_RATE,
        }

        # Gemini takes the style directive as a natural-language preamble on
        # the prompt itself. The script is still sent verbatim after it - the
        # words spoken are byte-identical to every other model's.
        text = req.text
        if req.style and "styled_tts" in self.supports:
            text = f"Say the following in this style - {req.style}:\n\n{req.text}"
            applied["style"] = req.style

        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=req.voice_id)
                )
            ),
        )

        try:
            resp = self._client.models.generate_content(
                model=self.spec.provider_model, contents=text, config=config
            )
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise _map_error(exc) from exc

        pcm = _extract_audio(resp)
        if not pcm:
            raise ProviderError("Gemini returned no audio part")

        um = getattr(resp, "usage_metadata", None)
        usage = Usage(
            reported=um is not None,
            input_tokens=getattr(um, "prompt_token_count", None) if um else None,
            audio_out_tokens=getattr(um, "candidates_token_count", None) if um else None,
            characters=len(req.text),
            raw=_usage_raw(um),
        )

        return GenResult(
            data=_wrap_pcm_as_wav(pcm, GEMINI_PCM_RATE),
            mime="audio/wav",
            provider_version=getattr(resp, "model_version", None) or self.spec.provider_model,
            usage=usage,
            applied_params=applied,
            provider_request_id=getattr(resp, "response_id", None),
        )


def _extract_audio(resp) -> bytes:
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data
    return b""


def _usage_raw(um) -> dict[str, Any]:
    if um is None:
        return {}
    return {
        k: getattr(um, k)
        for k in ("prompt_token_count", "candidates_token_count", "total_token_count")
        if getattr(um, k, None) is not None
    }


def _wrap_pcm_as_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits
    )
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def _map_error(exc: Exception):
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    if "429" in msg or "resource_exhausted" in low or "quota" in low:
        return RateLimited(msg)
    if "deadline" in low or name == "TimeoutError":
        return Timeout(msg)
    if "401" in msg or "403" in msg or "permission" in low or "credential" in low:
        return AuthError(msg)
    if "safety" in low or "blocked" in low or "prohibited" in low:
        return SafetyRefusal(msg)
    return ProviderError(f"{name}: {msg}")
