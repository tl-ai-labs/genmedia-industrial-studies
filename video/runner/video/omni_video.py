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


def _assert_assets_carried(parts: list, expected: int) -> None:
    """Fail loudly if an asset did not survive into the payload.

    This is not paranoia. The Interactions content union is OPEN and lenient:
    a malformed image part — say {"type": "image", "image": {...}} with the
    fields nested instead of flat — VALIDATES CLEANLY and yields an
    ImageContent whose `data` is None. The asset is silently dropped, the
    model generates from the prompt alone, and the run looks perfectly
    successful while comparing the wrong thing. Verified against
    google-genai 2.20.0 while wiring these tasks up.

    So the payload is checked before it is sent, not trusted.
    """
    carried = sum(1 for part in parts
                  if part.get("type") in ("image", "video") and part.get("data"))
    if carried != expected:
        raise ProviderError(
            f"input assets did not survive into the interaction payload "
            f"({carried} of {expected} carry data) — refusing to generate "
            f"from the prompt alone, which would look like a success",
            retryable=False)


class OmniFlashVideoAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        from google import genai
        from google.genai import types
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        self.timeout_s = timeout_s
        self.client = _make_client(genai, types, model_cfg, timeout_s)

    def _build_input(self, req: GenRequest):
        """The `input` payload for one interaction.

        Verified against google-genai 2.20.0: `interactions.create(input=...)`
        is typed `InteractionsInputParam = Union[ContentParam,
        List[StepParam], List[ContentParam], str]`, and ContentParam is the
        open union of Text/Document/Image/Audio/VideoContentParam. Each
        content is FLAT — `type` alongside its own fields, not nested under a
        key named after the type:

            {"type": "text",  "text": "..."}
            {"type": "image", "data": <base64>, "mime_type": "image/png"}
            {"type": "video", "data": <base64>, "mime_type": "video/mp4"}

        (google/genai/_gaos/types/interactions/{interactionsinput,content,
        imagecontent,videocontent}.py — the mime_type literals there are the
        accepted set, and video/mp4 and image/png are both in it.)

        Text-only (text_to_video) keeps sending the bare string: that is a
        legal InteractionsInputParam and it is the shape already proven
        against the live endpoint, so the working path is untouched.
        """
        if not req.inputs:
            return req.text
        parts: list = [{"type": "text", "text": req.text}]
        for asset in req.inputs:
            parts.append({
                "type": "video" if asset.mime.startswith("video/") else "image",
                "data": base64.b64encode(asset.path.read_bytes()).decode(),
                "mime_type": asset.mime,
            })
        _assert_assets_carried(parts, len(req.inputs))
        return parts

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
                input=self._build_input(req),
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
