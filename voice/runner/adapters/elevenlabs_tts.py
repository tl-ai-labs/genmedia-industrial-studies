"""
ElevenLabs text-to-speech (REST, no SDK dependency).

BILLING NOTE THAT MATTERS. ElevenLabs bills on CHARACTERS SENT, not on audio
produced (plan section 15). The price block in models.yaml says
`unit: per_1k_chars` for exactly that reason, and this adapter reports the
character count as its billing quantity. Pricing this per minute of output -
the intuitive mistake, since the artefact is audio - overstates a 600
character clip by roughly 2x.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
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
    split_dialogue,
)

API_ROOT = "https://api.elevenlabs.io/v1"

# ElevenLabs names formats like "pcm_24000" / "mp3_44100_128"; we ask for the
# one the model config declares and derive the file extension from it.
_FORMAT_MAP = {
    ("wav", 24000): ("pcm_24000", "wav"),
    ("wav", 16000): ("pcm_16000", "wav"),
    ("mp3", 44100): ("mp3_44100_128", "mp3"),
}


class ElevenLabsTtsAdapter(BaseAdapter):
    ext = "wav"

    def __init__(self, spec) -> None:
        super().__init__(spec)
        self._key = os.environ.get(spec.auth_env)
        if not self._key:
            raise AuthError(f"${spec.auth_env} is not set")
        fmt = str(spec.params.get("format", "wav")).lower()
        rate = int(spec.params.get("sample_rate", 24000))
        mapped = _FORMAT_MAP.get((fmt, rate))
        if not mapped:
            raise ProviderError(
                f"model '{spec.id}': no ElevenLabs output_format for "
                f"format={fmt} sample_rate={rate}. Supported: {sorted(_FORMAT_MAP)}"
            )
        self._output_format, self.ext = mapped

    def _tagged(self, text: str, style: str | None) -> str:
        """The text with its delivery direction as a leading audio tag.

        eleven_v3 and eleven_v4 read bracketed tags ("[whispers]") as
        direction, not as words, so a style directive can reach them the way
        `speech_metadata.style` reaches Gemini. Only when the model block
        opts in: older ElevenLabs models would speak the brackets aloud.
        """
        if style and self.spec.params.get("style_as_audio_tags"):
            return f"[{style.strip().rstrip('.')}] {text}"
        return text

    def build_request(self, req: GenRequest) -> tuple[str, dict[str, Any], dict[str, Any], int]:
        """(path, body, applied, characters billed). Split out so tests can see it."""
        requested: dict[str, Any] = {**self.spec.params, **req.params}
        applied: dict[str, Any] = {
            "format": self.spec.params.get("format"),
            "sample_rate": self.spec.params.get("sample_rate"),
            "voice": requested.get("voice", req.voice_logical),
        }
        tags = bool(self.spec.params.get("style_as_audio_tags"))
        if tags:
            applied["style_as_audio_tags"] = True
        # The streaming route returns the same audio, chunked, so the first
        # chunk can be timed. Same model, same voice, same body - only the
        # transport differs, which is what keeps the TTFA number comparable
        # with the non-streamed clip's own gates.
        stream = "/stream" if req.measure_ttfa else ""

        if req.speakers:
            # A two-voice scene goes to Text to Dialogue: one input per line,
            # each with its speaker's voice, the "<label>: " prefix removed
            # (it is a script convention, not words to speak).
            voices = {label: (vid, s) for label, vid, _, s in req.speakers}
            inputs: list[dict[str, str]] = []
            for label, line in split_dialogue(req.text, list(voices)):
                vid, s = voices[label]
                inputs.append({"text": self._tagged(line, s or req.style), "voice_id": vid})
            body: dict[str, Any] = {"inputs": inputs, "model_id": self.spec.provider_model}
            applied["speakers"] = list(voices)
            if tags and (req.style or any(s for _, s in voices.values())):
                applied["style"] = req.style or "; ".join(
                    f"{lb}: {s}" for lb, (_, s) in voices.items() if s)
            path = f"/text-to-dialogue{stream}"
            billed = sum(len(i["text"]) for i in inputs)
        else:
            if not req.voice_id:
                raise ProviderError(
                    f"model '{self.id}': ElevenLabs needs a voice id - the scenario's "
                    f"logical voice is not in this model's voice_map"
                )
            body = {"text": self._tagged(req.text, req.style), "model_id": self.spec.provider_model}
            # Without opting in to tags, style reaches no ElevenLabs field and is
            # NOT recorded as applied - it shows up in params_unsupported, which
            # is the honest report.
            if tags and req.style:
                applied["style"] = req.style
            path = f"/text-to-speech/{req.voice_id}{stream}"
            billed = len(body["text"])
        if req.language:
            body["language_code"] = req.language.split("-")[0]
            applied["language"] = req.language
        return path, body, applied, billed

    def run(self, req: GenRequest) -> GenResult:
        path, body, applied, billed = self.build_request(req)
        url = f"{API_ROOT}{path}?output_format={self._output_format}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "xi-api-key": self._key,
                "Content-Type": "application/json",
                "Accept": "audio/*",
            },
            method="POST",
        )

        ttfa_ms: int | None = None
        # STARTED BEFORE urlopen, deliberately. urlopen blocks until the
        # response headers arrive, so a timer started after it would exclude
        # connection AND the server's time to first byte - the bulk of what
        # TTFA measures - and make this arm look artificially fast.
        import time as _t

        started = _t.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=req.timeout_s) as resp:
                request_id = resp.headers.get("request-id")
                if req.measure_ttfa:
                    parts: list[bytes] = []
                    while True:
                        block = resp.read(4096)
                        if not block:
                            break
                        if ttfa_ms is None:
                            ttfa_ms = int((_t.perf_counter() - started) * 1000)
                        parts.append(block)
                    data = b"".join(parts)
                else:
                    data = resp.read()
        except urllib.error.HTTPError as exc:
            raise _map_http_error(exc) from exc
        except TimeoutError as exc:
            raise Timeout(f"ElevenLabs timed out after {req.timeout_s}s") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"ElevenLabs connection failed: {exc.reason}") from exc

        if not data:
            raise ProviderError("ElevenLabs returned an empty audio body")

        # PCM comes back headerless; wrap it so the artefact on disk is a
        # real, playable WAV rather than something only our checks can read.
        if self._output_format.startswith("pcm_"):
            data = _wrap_pcm_as_wav(data, int(self._output_format.split("_")[1]))

        return GenResult(
            data=data,
            mime="audio/wav" if self.ext == "wav" else f"audio/{self.ext}",
            provider_version=self.spec.provider_model,
            usage=Usage(
                reported=False,
                characters=billed,
                raw={"note": "ElevenLabs bills on characters sent; no usage object is returned"},
            ),
            applied_params=applied,
            ttfa_ms=ttfa_ms,
            provider_request_id=request_id,
        )


def _wrap_pcm_as_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    import struct

    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def _map_http_error(exc: urllib.error.HTTPError):
    try:
        detail = exc.read().decode("utf-8", "replace")[:500]
    except Exception:  # noqa: BLE001
        detail = ""
    msg = f"ElevenLabs HTTP {exc.code}: {detail}"
    if exc.code == 429:
        retry_after = exc.headers.get("retry-after")
        return RateLimited(msg, float(retry_after) if retry_after else None)
    if exc.code in (401, 403):
        return AuthError(msg)
    if exc.code == 422 and "policy" in detail.lower():
        return SafetyRefusal(msg)
    if 500 <= exc.code < 600:
        return ProviderError(msg)
    err = ProviderError(msg)
    err.retryable = False
    return err
