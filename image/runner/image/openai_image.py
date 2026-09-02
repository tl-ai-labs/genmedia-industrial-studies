"""OpenAI image generation adapter (openai SDK lives ONLY here)."""
from __future__ import annotations

import base64
import os

from ..adapters.base import (Adapter, GenRequest, GenResult, ProviderError,
                             RateLimited, SafetyRefusal, Timeout)

_SUPPORTED_PARAMS = {"size", "quality", "background", "output_format"}


class OpenAIImageAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        from openai import OpenAI
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        # max_retries=0: the runner owns retries; hidden SDK retries would
        # corrupt the one-attempt-one-telemetry-row rule.
        self.client = OpenAI(api_key=os.environ[model_cfg.auth_env],
                             timeout=timeout_s, max_retries=0)

    def run(self, req: GenRequest) -> GenResult:
        applied = {k: v for k, v in req.params.items() if k in _SUPPORTED_PARAMS}
        unsupported = [k for k in req.params if k not in _SUPPORTED_PARAMS]

        try:
            if req.task in ("image_edit", "inpaint_mask") and req.inputs:
                kwargs: dict = {}
                images = [open(a.path, "rb") for a in req.inputs if a.role != "mask"]
                kwargs["image"] = images[0] if len(images) == 1 else images
                mask = next((a for a in req.inputs if a.role == "mask"), None)
                if mask is not None:
                    kwargs["mask"] = open(mask.path, "rb")
                resp = self.client.images.edit(
                    model=self.cfg.provider_model, prompt=req.text,
                    **applied, **kwargs)
            else:
                resp = self.client.images.generate(
                    model=self.cfg.provider_model, prompt=req.text, **applied)
        except Exception as e:
            raise _translate(e) from e

        if not resp.data or not resp.data[0].b64_json:
            raise ProviderError("response contained no image data", retryable=True)
        data = base64.b64decode(resp.data[0].b64_json)

        usage: dict = {"images": len(resp.data)}
        raw_usage = getattr(resp, "usage", None)
        if raw_usage is not None:
            usage.update({
                "input_tokens": getattr(raw_usage, "input_tokens", None),
                "output_tokens": getattr(raw_usage, "output_tokens", None),
                "total_tokens": getattr(raw_usage, "total_tokens", None),
            })

        fmt = getattr(resp, "output_format", None) or "png"
        return GenResult(
            data=data,
            mime=f"image/{fmt}",
            provider_version=getattr(resp, "model", None),
            usage=usage,
            applied_params=applied,
            params_unsupported=unsupported)


def _translate(e: Exception) -> Exception:
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


def build(model_cfg, timeout_s: float) -> OpenAIImageAdapter:
    return OpenAIImageAdapter(model_cfg, timeout_s)
