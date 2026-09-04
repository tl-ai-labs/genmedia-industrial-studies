"""ByteDance Seedance video generation adapter — official BytePlus ModelArk
REST API (no vendor SDK; httpx, already installed as an openai dependency,
lives ONLY here for this provider).

    POST {base}/contents/generations/tasks   -> {"id": task_id}
      body: {"model": ..., "content": [{"type": "text", "text": "<prompt>"}],
             "resolution": "1080p", "ratio": "16:9", "duration": 8,
             "generate_audio": false, "watermark": false, "seed": ...}
      (run settings are top-level fields per the Seedance 2.5 API reference,
       docs.byteplus.com/en/docs/ModelArk/1520757; the prompt is sent
       verbatim. generate_audio DEFAULTS TO TRUE on the API, so the
       scenario's `audio` param is always forwarded; watermark is always
       off. Everything sent is recorded in applied_params.)
      Rate limits (individual accounts): max concurrency 3, max RPM 180 —
      configured in models.yaml `limits`, enforced by the runner.
    GET  {base}/contents/generations/tasks/{id}
      -> status queued|running|succeeded|failed|cancelled
      -> succeeded: content.video_url (time-limited), downloaded here
      -> usage.completion_tokens (billing basis: $/1M tokens, price unit
         per_token in models.yaml -> api_reported cost)

Auth: Bearer key from the model's auth_env (ARK_API_KEY). Base URL:
ARK_BASE_URL env override, defaulting to the BytePlus ap-southeast endpoint
— only ModelArk-compatible endpoints work; resellers with different body
shapes need their own adapter, not a base-URL swap.
"""
from __future__ import annotations

import os
import time

from ..adapters.base import (Adapter, GenRequest, GenResult, ProviderError,
                             RateLimited, SafetyRefusal, Timeout)

POLL_INTERVAL_S = 10
DEFAULT_DURATION_S = 8
DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
_TERMINAL = ("succeeded", "failed", "cancelled")


class SeedanceVideoAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        import httpx
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        self.timeout_s = timeout_s
        self.base_url = os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        # no transport-level retries: the runner owns retries
        self._http = httpx.Client(
            timeout=min(timeout_s, 120),
            headers={"Authorization": f"Bearer {os.environ[model_cfg.auth_env]}",
                     "Content-Type": "application/json"})

    def run(self, req: GenRequest) -> GenResult:
        applied: dict = {}
        unsupported: list[str] = []

        duration_s = DEFAULT_DURATION_S
        fields: dict = {"watermark": False}
        for key, value in req.params.items():
            if key == "duration_s":
                duration_s = int(value)
                fields["duration"] = duration_s
                applied[key] = duration_s
            elif key == "resolution":         # "480p" | "720p" | "1080p"
                fields["resolution"] = str(value)
                applied[key] = str(value)
            elif key == "aspect_ratio":       # "16:9" | "9:16" | ...
                fields["ratio"] = str(value)
                applied[key] = str(value)
            elif key == "audio":              # API default is TRUE — always forward
                fields["generate_audio"] = bool(value)
                applied[key] = bool(value)
            elif key == "seed":
                fields["seed"] = int(value)
                applied[key] = int(value)
            else:
                unsupported.append(key)
        applied["watermark"] = False

        try:
            resp = self._http.post(
                f"{self.base_url}/contents/generations/tasks",
                json={"model": self.cfg.provider_model,
                      "content": [{"type": "text", "text": req.text}],
                      **fields})
            task = self._checked(resp)
            task_id = task.get("id")
            if not task_id:
                raise ProviderError(f"create returned no task id: {task}",
                                    retryable=True)
            deadline = time.monotonic() + self.timeout_s
            while task.get("status") not in _TERMINAL:
                if time.monotonic() > deadline:
                    raise Timeout(f"task {task_id} still {task.get('status')} "
                                  f"after {self.timeout_s:.0f}s")
                time.sleep(POLL_INTERVAL_S)
                task = self._checked(self._http.get(
                    f"{self.base_url}/contents/generations/tasks/{task_id}"))
            if task.get("status") != "succeeded":
                raise _translate_failure(task)

            video_url = (task.get("content") or {}).get("video_url")
            if not video_url:
                raise ProviderError(f"succeeded task carried no video_url: "
                                    f"{task}", retryable=True)
            dl = self._http.get(video_url, headers={"Authorization": ""})
            dl.raise_for_status()
            data = dl.content
        except Exception as e:
            raise _translate(e) from e
        if not data:
            raise ProviderError("download returned no bytes", retryable=True)

        usage: dict = {}
        u = task.get("usage") or {}
        if u.get("completion_tokens") is not None:
            usage["output_tokens"] = u["completion_tokens"]   # billing basis
            usage["total_tokens"] = u.get("total_tokens")
        usage["seconds"] = duration_s
        usage["seconds_source"] = "requested"

        return GenResult(
            data=bytes(data),
            mime="video/mp4",
            provider_version=task.get("model"),
            usage=usage,
            applied_params=applied,
            params_unsupported=unsupported,
            request_id=str(task_id))

    def _checked(self, resp) -> dict:
        """Raise the taxonomy for HTTP-level errors; return the JSON body."""
        if resp.status_code == 429:
            retry_after = None
            if resp.headers.get("retry-after"):
                try:
                    retry_after = float(resp.headers["retry-after"])
                except ValueError:
                    pass
            raise RateLimited(resp.text[:300], retry_after=retry_after)
        if resp.status_code >= 500:
            raise ProviderError(f"{resp.status_code}: {resp.text[:300]}",
                                retryable=True)
        if resp.status_code >= 400:
            low = resp.text.lower()
            if "sensitive" in low or "moderation" in low or "risk" in low:
                raise SafetyRefusal(resp.text[:300])
            raise ProviderError(f"{resp.status_code}: {resp.text[:300]}",
                                retryable=False)
        return resp.json()


def _translate_failure(task: dict) -> Exception:
    err = task.get("error") or {}
    code = str(err.get("code", ""))
    msg = str(err.get("message", "")) or f"task {task.get('status')}"
    low = (code + " " + msg).lower()
    if any(w in low for w in ("sensitive", "moderation", "risk", "safety",
                              "blocked")):
        return SafetyRefusal(f"{code}: {msg}")
    return ProviderError(f"{code}: {msg}",
                         retryable="internal" in low or "timeout" in low)


def _translate(e: Exception) -> Exception:
    if isinstance(e, (RateLimited, Timeout, SafetyRefusal, ProviderError)):
        return e
    import httpx
    if isinstance(e, httpx.TimeoutException):
        return Timeout(str(e))
    if isinstance(e, httpx.HTTPError):
        return ProviderError(str(e), retryable=True)
    return ProviderError(str(e), retryable=True)


def build(model_cfg, timeout_s: float) -> SeedanceVideoAdapter:
    return SeedanceVideoAdapter(model_cfg, timeout_s)
