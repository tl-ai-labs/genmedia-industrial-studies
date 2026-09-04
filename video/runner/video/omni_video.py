"""Gemini Omni Flash video generation adapter (google-genai SDK lives ONLY
here). Omni is Google's multimodal Gemini-family model whose video output
runs through the Interactions API (google-genai >= 2.10):

    client.interactions.create(model=..., input=prompt,
                               response_format={type: "video", ...})
    -> poll client.interactions.get(id) while queued/in_progress
    -> interaction.output_video.data (base64) with delivery="inline"

response_format alone declares the video modality. Passing
response_modalities=["video"] as well makes Vertex answer 404 "Requested
entity was not found" (verified against the live endpoint 2026-09-03) —
so this adapter deliberately does not send it.

Billing is per video output token ($/1M, price unit per_token in
models.yaml); the interaction's usage block reports the token counts, so
cost is api_reported — no estimation needed.
"""
from __future__ import annotations

import base64
import time

from ..adapters.base import (Adapter, GenRequest, GenResult, ProviderError,
                             RateLimited, SafetyRefusal, Timeout)
from ..adapters.google_client import _make_client  # shared with the judge

POLL_INTERVAL_S = 10
DEFAULT_DURATION_S = 8
_PENDING = ("queued", "in_progress")


class OmniFlashVideoAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        from google import genai
        from google.genai import types
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        self.timeout_s = timeout_s
        self.client = _make_client(genai, types, model_cfg, timeout_s)

    def run(self, req: GenRequest) -> GenResult:
        applied: dict = {}
        unsupported: list[str] = []

        duration_s = DEFAULT_DURATION_S
        response_format: dict = {"type": "video", "delivery": "inline"}
        create_kwargs: dict = {}
        for key, value in req.params.items():
            if key == "duration_s":
                duration_s = int(value)
                # protobuf-style duration string; the applied value is
                # recorded, so a provider-side reinterpretation stays visible
                response_format["duration"] = f"{duration_s}s"
                applied[key] = duration_s
            elif key == "resolution":       # "360p" | "720p" | "1080p" | "4k"
                response_format["resolution"] = str(value)
                applied[key] = str(value)
            elif key == "aspect_ratio":     # "16:9" | "9:16"
                response_format["aspect_ratio"] = str(value)
                applied[key] = str(value)
            elif key == "seed":
                create_kwargs["generation_config"] = {"seed": int(value)}
                applied[key] = int(value)
            else:
                # no audio toggle on the Interactions video surface
                unsupported.append(key)

        try:
            interaction = self.client.interactions.create(
                model=self.cfg.provider_model,
                input=req.text,
                response_format=response_format,   # no response_modalities: see module docstring
                **create_kwargs)
            deadline = time.monotonic() + self.timeout_s
            while str(getattr(interaction, "status", "")) in _PENDING:
                if time.monotonic() > deadline:
                    raise Timeout(
                        f"interaction {getattr(interaction, 'id', '?')} still "
                        f"{interaction.status} after {self.timeout_s:.0f}s")
                time.sleep(POLL_INTERVAL_S)
                interaction = self.client.interactions.get(interaction.id)
        except Exception as e:  # translate — the SDK's taxonomy stays here
            raise _translate(e) from e

        status = str(getattr(interaction, "status", ""))
        if status != "completed":
            raise _translate_terminal(interaction, status)

        video = getattr(interaction, "output_video", None)
        data = getattr(video, "data", None) if video is not None else None
        if not data:
            raise ProviderError("completed interaction carried no video data "
                                f"(uri={getattr(video, 'uri', None)})",
                                retryable=True)
        raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)

        usage: dict = {}
        u = getattr(interaction, "usage", None)
        if u is not None:
            usage = {"output_tokens": getattr(u, "total_output_tokens", None),
                     "input_tokens": getattr(u, "total_input_tokens", None),
                     "total_tokens": getattr(u, "total_tokens", None)}
        # the billing basis is tokens; requested seconds recorded for context
        usage["seconds"] = duration_s
        usage["seconds_source"] = "requested"

        return GenResult(
            data=raw,
            mime=getattr(video, "mime_type", None) or "video/mp4",
            provider_version=getattr(interaction, "model", None),
            usage=usage,
            applied_params=applied,
            params_unsupported=unsupported,
            request_id=getattr(interaction, "id", None))


def _translate_terminal(interaction, status: str) -> Exception:
    errs = getattr(interaction, "errors", None) or []
    msg = "; ".join(str(getattr(e, "message", e)) for e in errs) or f"status={status}"
    low = (status + " " + msg).lower()
    if any(w in low for w in ("safety", "blocked", "prohibited", "violat")):
        return SafetyRefusal(msg)
    if status in ("cancelled", "budget_exceeded"):
        return ProviderError(f"{status}: {msg}", retryable=False)
    return ProviderError(f"{status}: {msg}", retryable=True)


def _translate(e: Exception) -> Exception:
    if isinstance(e, (RateLimited, Timeout, SafetyRefusal, ProviderError)):
        return e
    from google.genai import errors as gerrors
    if isinstance(e, gerrors.APIError):
        code = getattr(e, "code", None) or 0
        if code == 429:
            return RateLimited(str(e))
        if code >= 500:
            return ProviderError(str(e), retryable=True)
        return ProviderError(str(e), retryable=False)
    name = type(e).__name__.lower()
    if "timeout" in name or "timeout" in str(e).lower():
        return Timeout(str(e))
    return ProviderError(str(e), retryable=True)


def build(model_cfg, timeout_s: float) -> OmniFlashVideoAdapter:
    return OmniFlashVideoAdapter(model_cfg, timeout_s)
