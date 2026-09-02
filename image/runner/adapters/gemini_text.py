"""Gemini multimodal judge adapter (google-genai SDK lives ONLY here).

The judge seam: judge.py calls `judge(prompt, media)` blindly. Swapping the
judge model is a YAML edit; swapping the judge provider is one file like this.
"""
from __future__ import annotations

import os

from .base import JudgeResult, ProviderError, RateLimited, SafetyRefusal, Timeout


class GeminiTextAdapter:
    def __init__(self, judge_cfg, timeout_s: float):
        from google import genai
        from google.genai import types
        from .google_client import _make_client
        self._types = types
        self.cfg = judge_cfg
        self.client = _make_client(genai, types, judge_cfg, timeout_s)

    def judge(self, prompt: str, media: list[tuple[bytes, str]]) -> JudgeResult:
        """media: list of (bytes, mime) shown to the judge, already stripped."""
        types = self._types
        contents: list = [types.Part.from_bytes(data=d, mime_type=m) for d, m in media]
        contents.append(prompt)
        try:
            resp = self.client.models.generate_content(
                model=self.cfg.provider_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=self.cfg.temperature,
                    response_mime_type="application/json"))
        except Exception as e:
            raise _translate(e) from e

        block = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
        if block:
            raise SafetyRefusal(f"judge prompt blocked: {block}")
        text = resp.text
        if not text:
            raise ProviderError("judge returned empty response", retryable=True)

        usage_md = getattr(resp, "usage_metadata", None)
        usage = {}
        if usage_md is not None:
            usage = {
                "prompt_token_count": getattr(usage_md, "prompt_token_count", None),
                "candidates_token_count": getattr(usage_md, "candidates_token_count", None),
                "total_token_count": getattr(usage_md, "total_token_count", None),
            }
        return JudgeResult(
            text=text,
            provider_version=getattr(resp, "model_version", None),
            usage=usage,
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
    if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
        return Timeout(str(e))
    return ProviderError(str(e), retryable=True)


def build(judge_cfg, timeout_s: float) -> GeminiTextAdapter:
    return GeminiTextAdapter(judge_cfg, timeout_s)
