"""Gemini image generation adapter (google-genai SDK lives ONLY here)."""
from __future__ import annotations

from ..adapters.base import (Adapter, GenRequest, GenResult, ProviderError,
                             RateLimited, SafetyRefusal, Timeout)
from ..adapters.google_client import _make_client  # noqa: F401 — re-exported;
# shared with the judge and video adapters

_SIZE_MAP = {"1024x1024": "1K", "2048x2048": "2K", "4096x4096": "4K"}


class GeminiImageAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        from google import genai
        from google.genai import types  # noqa: F401 — imported for use in run()
        self._genai = genai
        self._types = types
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        self.client = _make_client(genai, types, model_cfg, timeout_s)

    def run(self, req: GenRequest) -> GenResult:
        types = self._types
        applied: dict = {}
        unsupported: list[str] = []

        image_cfg_kwargs: dict = {}
        for key, value in req.params.items():
            if key == "aspect_ratio":
                image_cfg_kwargs["aspect_ratio"] = value
                applied[key] = value
            elif key == "size":
                if value in _SIZE_MAP:
                    image_cfg_kwargs["image_size"] = _SIZE_MAP[value]
                    applied[key] = f"{value} (as image_size={_SIZE_MAP[value]})"
                else:
                    unsupported.append(key)
            elif key == "seed":
                applied[key] = value
            else:
                unsupported.append(key)

        config_kwargs: dict = {"response_modalities": ["IMAGE"]}
        if "seed" in req.params:
            config_kwargs["seed"] = int(req.params["seed"])
        if image_cfg_kwargs:
            config_kwargs["image_config"] = types.ImageConfig(**image_cfg_kwargs)

        contents: list = []
        for asset in req.inputs:  # image_edit: source/mask/reference go in first
            contents.append(types.Part.from_bytes(
                data=asset.path.read_bytes(), mime_type=asset.mime))
        contents.append(req.text)

        try:
            resp = self.client.models.generate_content(
                model=self.cfg.provider_model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs))
        except Exception as e:  # translate — the SDK's taxonomy stays in this file
            raise _translate(e) from e

        block = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
        if block:
            raise SafetyRefusal(f"prompt blocked: {block}")

        image_parts = []
        for cand in (resp.candidates or []):
            finish = str(getattr(cand, "finish_reason", "") or "")
            if "SAFETY" in finish or "PROHIBITED" in finish:
                raise SafetyRefusal(f"generation blocked: finish_reason={finish}")
            for part in (getattr(cand.content, "parts", None) or []):
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    image_parts.append(inline)
        if not image_parts:
            raise ProviderError("response contained no image data", retryable=True)

        usage_md = getattr(resp, "usage_metadata", None)
        usage = {"images": len(image_parts)}
        if usage_md is not None:
            usage.update({
                "prompt_token_count": getattr(usage_md, "prompt_token_count", None),
                "candidates_token_count": getattr(usage_md, "candidates_token_count", None),
                "total_token_count": getattr(usage_md, "total_token_count", None),
            })

        return GenResult(
            data=image_parts[0].data,
            mime=image_parts[0].mime_type or "image/png",
            provider_version=getattr(resp, "model_version", None),
            usage=usage,
            applied_params=applied,
            params_unsupported=unsupported,
            request_id=getattr(resp, "response_id", None))


def _translate(e: Exception) -> Exception:
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


def build(model_cfg, timeout_s: float) -> GeminiImageAdapter:
    return GeminiImageAdapter(model_cfg, timeout_s)
