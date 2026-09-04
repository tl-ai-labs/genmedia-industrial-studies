"""Veo video generation adapter (google-genai SDK lives ONLY here).

generate_videos returns a long-running operation; this adapter polls it to
completion inside run() so the runner's synchronous lifecycle, retries and
telemetry apply unchanged. The poll budget is the runner's video timeout.

Usage honesty: the video API reports no usage counters back, so the adapter
records the REQUESTED clip seconds with seconds_source="requested" —
cost.py then labels the cost `estimated` and the label follows the number
into the report.
"""
from __future__ import annotations

import time

from ..adapters.base import (Adapter, GenRequest, GenResult, ProviderError,
                             RateLimited, SafetyRefusal, Timeout)
from ..adapters.google_client import _make_client  # shared with the judge

POLL_INTERVAL_S = 10
DEFAULT_DURATION_S = 8


class VeoVideoAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        from google import genai
        from google.genai import types
        self._genai = genai
        self._types = types
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        self.timeout_s = timeout_s
        self.client = _make_client(genai, types, model_cfg, timeout_s)

    def run(self, req: GenRequest) -> GenResult:
        types = self._types
        applied: dict = {}
        unsupported: list[str] = []

        cfg_kwargs: dict = {"number_of_videos": 1}
        duration_s = DEFAULT_DURATION_S
        for key, value in req.params.items():
            if key == "duration_s":
                duration_s = int(value)
                cfg_kwargs["duration_seconds"] = duration_s
                applied[key] = duration_s
            elif key == "resolution":         # "720p" | "1080p" | "4k"
                cfg_kwargs["resolution"] = str(value)
                applied[key] = str(value)
            elif key == "aspect_ratio":       # "16:9" | "9:16"
                cfg_kwargs["aspect_ratio"] = str(value)
                applied[key] = str(value)
            elif key == "audio":
                cfg_kwargs["generate_audio"] = bool(value)
                applied[key] = bool(value)
            elif key == "seed":
                cfg_kwargs["seed"] = int(value)
                applied[key] = int(value)
            else:
                unsupported.append(key)

        try:
            op = self.client.models.generate_videos(
                model=self.cfg.provider_model,
                prompt=req.text,
                config=types.GenerateVideosConfig(**cfg_kwargs))
            deadline = time.monotonic() + self.timeout_s
            while not op.done:
                if time.monotonic() > deadline:
                    raise Timeout(
                        f"video operation still running after {self.timeout_s:.0f}s "
                        f"(operation {getattr(op, 'name', '?')})")
                time.sleep(POLL_INTERVAL_S)
                op = self.client.operations.get(op)
        except Exception as e:  # translate — the SDK's taxonomy stays in this file
            raise _translate(e) from e

        err = getattr(op, "error", None)
        if err:
            raise _translate_op_error(err)

        resp = getattr(op, "response", None) or getattr(op, "result", None)
        videos = list(getattr(resp, "generated_videos", None) or [])
        if not videos:
            filtered = (getattr(resp, "rai_media_filtered_reasons", None)
                        or getattr(resp, "rai_media_filtered_count", None))
            if filtered:
                raise SafetyRefusal(f"all outputs filtered: {filtered}")
            raise ProviderError("operation completed with no video", retryable=True)

        video = videos[0].video
        data = getattr(video, "video_bytes", None)
        if data is None:
            # API-key (Gemini Developer API) route: bytes arrive only after a
            # files.download call; on Vertex video_bytes is populated directly
            try:
                self.client.files.download(file=video)
            except Exception as e:
                raise _translate(e) from e
            data = getattr(video, "video_bytes", None)
        if not data:
            raise ProviderError("generated video carried no bytes", retryable=True)

        return GenResult(
            data=bytes(data),
            mime=getattr(video, "mime_type", None) or "video/mp4",
            provider_version=getattr(resp, "model_version", None),
            usage={"seconds": duration_s, "seconds_source": "requested"},
            applied_params=applied,
            params_unsupported=unsupported,
            request_id=getattr(op, "name", None))


def _translate_op_error(err) -> Exception:
    """The LRO's terminal error is a {code, message} mapping, not a raised
    SDK exception — same taxonomy mapping as _translate."""
    code = 0
    msg = str(err)
    if isinstance(err, dict):
        code = int(err.get("code") or 0)
        msg = str(err.get("message") or err)
    low = msg.lower()
    if any(w in low for w in ("safety", "blocked", "prohibited", "violat")):
        return SafetyRefusal(msg)
    if code == 429:
        return RateLimited(msg)
    if code >= 500:
        return ProviderError(msg, retryable=True)
    return ProviderError(msg, retryable=False)


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


def build(model_cfg, timeout_s: float) -> VeoVideoAdapter:
    return VeoVideoAdapter(model_cfg, timeout_s)
