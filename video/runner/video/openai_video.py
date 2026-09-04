"""OpenAI Sora video generation adapter (openai SDK lives ONLY here).

videos.create -> poll videos.retrieve until completed/failed ->
download_content. Polling happens inside run() so the runner's synchronous
lifecycle, retries and telemetry apply unchanged.

Resolution honesty: sora-2 (non-pro) tops out at 720p. A brief asking for
more is delivered at the model's ceiling and RECORDED — the downgrade shows
in applied_params/params_unsupported, and the measured technical_compliance
criterion grades the shortfall. A capability gap is a visible, graded fact,
never a silent substitution and never a fabricated success.
"""
from __future__ import annotations

import os
import time

from ..adapters.base import (Adapter, GenRequest, GenResult, ProviderError,
                             RateLimited, SafetyRefusal, Timeout)

POLL_INTERVAL_S = 10
DEFAULT_DURATION_S = 8

# size strings by (resolution, orientation); sora-2-pro only for the 1080p row
_SIZES = {
    ("720p", "landscape"): "1280x720",
    ("720p", "portrait"): "720x1280",
    ("1080p", "landscape"): "1920x1080",
    ("1080p", "portrait"): "1080x1920",
}


def _pick_size(params: dict, provider_model: str,
               applied: dict, unsupported: list) -> str | None:
    res = str(params.get("resolution", "")).lower()
    ar = str(params.get("aspect_ratio", "16:9"))
    try:
        num, den = (int(x) for x in ar.split(":"))
        orient = "landscape" if num >= den else "portrait"
    except ValueError:
        orient = "landscape"
    if not res:
        return None
    is_pro = "sora-2-pro" in provider_model
    if res == "1080p" and not is_pro:
        size = _SIZES[("720p", orient)]
        applied["resolution"] = f"720p (downgraded from 1080p: {provider_model} ceiling)"
        unsupported.append("resolution:1080p")
    else:
        size = _SIZES.get((res, orient))
        if size is None:
            unsupported.append(f"resolution:{res}")
            return None
        applied["resolution"] = res
    applied["aspect_ratio"] = ar
    return size


class OpenAIVideoAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        from openai import OpenAI
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        self.timeout_s = timeout_s
        # max_retries=0: the runner owns retries; hidden SDK retries would
        # corrupt the one-attempt-one-telemetry-row rule.
        self.client = OpenAI(api_key=os.environ[model_cfg.auth_env],
                             timeout=min(timeout_s, 120), max_retries=0)

    def run(self, req: GenRequest) -> GenResult:
        applied: dict = {}
        unsupported: list[str] = []

        duration_s = DEFAULT_DURATION_S
        if "duration_s" in req.params:
            duration_s = int(req.params["duration_s"])
            applied["duration_s"] = duration_s
        size = _pick_size(req.params, self.cfg.provider_model, applied, unsupported)
        for key in req.params:
            if key == "audio":
                # Sora has no audio toggle — clips always carry generated audio
                unsupported.append(key)
            elif key not in ("duration_s", "resolution", "aspect_ratio", "audio"):
                unsupported.append(key)

        kwargs: dict = {"model": self.cfg.provider_model, "prompt": req.text,
                        "seconds": str(duration_s)}
        if size:
            kwargs["size"] = size

        try:
            video = self.client.videos.create(**kwargs)
            deadline = time.monotonic() + self.timeout_s
            while video.status not in ("completed", "failed"):
                if time.monotonic() > deadline:
                    raise Timeout(f"video {video.id} still {video.status} after "
                                  f"{self.timeout_s:.0f}s")
                time.sleep(POLL_INTERVAL_S)
                video = self.client.videos.retrieve(video.id)
            if video.status == "failed":
                raise _translate_failure(video)
            content = self.client.videos.download_content(video.id)
        except Exception as e:
            raise _translate(e) from e

        data = content.read() if hasattr(content, "read") else content.content
        if not data:
            raise ProviderError("download returned no bytes", retryable=True)

        usage: dict = {}
        reported = getattr(video, "seconds", None)
        if reported is not None:  # the job object echoes the billed seconds
            usage["seconds"] = float(reported)
        else:
            usage["seconds"] = duration_s
            usage["seconds_source"] = "requested"

        return GenResult(
            data=bytes(data),
            mime="video/mp4",
            provider_version=getattr(video, "model", None),
            usage=usage,
            applied_params=applied,
            params_unsupported=unsupported,
            request_id=getattr(video, "id", None))


def _translate_failure(video) -> Exception:
    """A terminal `failed` job carries an error object, not an exception."""
    err = getattr(video, "error", None)
    code = str(getattr(err, "code", "") or "")
    msg = str(getattr(err, "message", "") or err or "video job failed")
    low = (code + " " + msg).lower()
    if "moderation" in low or "content_policy" in low or "safety" in low:
        return SafetyRefusal(f"{code}: {msg}")
    return ProviderError(f"{code}: {msg}", retryable="internal" in low)


def _translate(e: Exception) -> Exception:
    if isinstance(e, (RateLimited, Timeout, SafetyRefusal, ProviderError)):
        return e
    import openai
    if isinstance(e, openai.RateLimitError):
        retry_after = None
        headers = getattr(getattr(e, "response", None), "headers", None)
        if headers and headers.get("retry-after"):
            try:
                retry_after = float(headers["retry-after"])
            except ValueError:
                pass
        return RateLimited(str(e), retry_after=retry_after)
    if isinstance(e, openai.APITimeoutError):
        return Timeout(str(e))
    if isinstance(e, openai.APIStatusError):
        body = str(getattr(e, "message", "")) + str(getattr(e, "body", ""))
        if e.status_code == 400 and ("moderation" in body.lower()
                                     or "safety" in body.lower()
                                     or "content_policy" in body.lower()):
            return SafetyRefusal(str(e))
        return ProviderError(str(e), retryable=e.status_code >= 500)
    if isinstance(e, openai.APIConnectionError):
        return ProviderError(str(e), retryable=True)
    return ProviderError(str(e), retryable=True)


def build(model_cfg, timeout_s: float) -> OpenAIVideoAdapter:
    return OpenAIVideoAdapter(model_cfg, timeout_s)
