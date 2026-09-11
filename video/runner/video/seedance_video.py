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

import base64
import os
import time

import httpx

from ..adapters.base import (Adapter, GenRequest, GenResult, ProviderError,
                             RateLimited, SafetyRefusal, Timeout)

POLL_INTERVAL_S = 10
# consecutive polling failures tolerated before a task is given up on; the
# task keeps generating and billing, so patience is cheaper than a re-create
POLL_MAX_TRANSIENT = 6
DOWNLOAD_ATTEMPTS = 3
# ModelArk stamps created_at server-side; allow for clock skew when deciding
# which tasks appeared after a create we lost the response to
CREATE_CLOCK_SKEW_S = 10
DEFAULT_DURATION_S = 8
DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
_TERMINAL = ("succeeded", "failed", "cancelled")


def _assert_url_fetchable(url: str) -> None:
    """Confirm the provider will actually be able to GET this asset.

    ARK_ASSET_BASE_URL pins a commit sha so a run's inputs cannot drift after
    the fact. The cost of pinning is that the pin goes stale the moment new
    assets are committed: on 2026-09-11 five sources were re-cut in a later
    commit than the one the URL named, and four cells came back
    "content[1].video_url.url ... resource not found" after the run had
    already started. The provider was right and we were pointing at the past.

    A HEAD before the create turns that into a local error naming the URL,
    costs one cheap request against a call that takes minutes, and cannot
    itself be billed.
    """
    import httpx
    try:
        r = httpx.head(url, timeout=20, follow_redirects=True)
    except Exception as e:
        raise ProviderError(
            f"could not reach the asset url {url} ({e}). ModelArk must fetch "
            f"it anonymously, so this would have failed provider-side.",
            retryable=False) from e
    if r.status_code != 200:
        raise ProviderError(
            f"asset url {url} returned HTTP {r.status_code}. ModelArk fetches "
            f"this itself, so the run would fail. If the file exists locally, "
            f"ARK_ASSET_BASE_URL is pinned to a commit that predates it — "
            f"repin it to a pushed commit containing the asset.",
            retryable=False)


def _assert_assets_carried(content: list, expected: int) -> None:
    """Refuse to generate from the prompt alone when an asset went missing.

    The Omni adapter carries the same guard for a proven reason: a malformed
    part can validate and still arrive empty, the model then generates from
    the prompt, and the run looks perfectly successful while comparing the
    wrong thing. Nothing says ModelArk is immune — an empty or truncated data
    URI is exactly the shape of failure that would slip through — and an
    asset-fed run is 18 of 18 scenarios here. Cheap check, silent-failure
    class of bug.
    """
    carried = 0
    for part in content:
        kind = part.get("type")
        if kind in ("image_url", "video_url") and (part.get(kind) or {}).get("url"):
            carried += 1
    if carried != expected:
        raise ProviderError(
            f"input assets did not survive into the request content "
            f"({carried} of {expected} carry a data URI) — refusing to "
            f"generate from the prompt alone, which would look like a success",
            retryable=False)


class SeedanceVideoAdapter(Adapter):
    def __init__(self, model_cfg, timeout_s: float):
        import httpx
        self.cfg = model_cfg
        self.supports = list(model_cfg.supports)
        self.timeout_s = timeout_s
        self.base_url = os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        # no transport-level retries: the runner owns retries
        #
        # This bounds ONE http call, not the whole task. 120s was too short:
        # an asset-fed create ships a multi-MB base64 data URI in the body,
        # and on 2026-09-10 five creates were lost at 87-122s — every one of
        # which had actually REACHED ModelArk, generated a clip and billed
        # $4.18, while the runner recorded a timeout and $0.00. Connect stays
        # short (the endpoint answers in 0.2s or it is down); write and read
        # get room for the body. The 1800s cell deadline still bounds polling.
        self._http = httpx.Client(
            timeout=httpx.Timeout(min(timeout_s, 600), connect=15.0),
            headers={"Authorization": f"Bearer {os.environ[model_cfg.auth_env]}",
                     "Content-Type": "application/json"})

    def _build_content(self, req: GenRequest) -> list:
        """The `content` array for one task.

        Verified 2026-09-09 against the Seedance 2.5 API reference
        (docs.byteplus.com/en/docs/ModelArk/1520757), whose own worked
        examples are:

          edit:  {"type": "video_url", "video_url": {"url": ...},
                  "role": "reference_video"}
          i2v:   {"type": "image_url", "image_url": {"url":
                  "data:image/png;base64,..."}}

        An IMAGE travels as a data URI and needs no upload step — that is how
        all ten ads ran on 2026-09-10. A VIDEO does not: the same shape is
        refused with `InvalidParameter: reference_video must be provided as a
        web url`, in 1-2s, before any generation (all four edits, 2026-09-10).
        The reference's edit example shows a bare url and evidently means a
        fetchable one.

        So video inputs are sent as a URL built from ARK_ASSET_BASE_URL, which
        must be a base the PROVIDER can fetch anonymously. Pin it to an
        immutable ref — a commit sha, never a branch — or a run's inputs can
        drift after the fact and its record stops being true.

        Text-only keeps its single text part, byte-identical to the proven path.
        """
        content: list = [{"type": "text", "text": req.text}]
        for asset in req.inputs or []:
            if asset.mime.startswith("video/"):
                base = os.environ.get("ARK_ASSET_BASE_URL", "").strip().rstrip("/")
                if not base:
                    raise ProviderError(
                        "this task sends a video input, which ModelArk accepts "
                        "only as a web url, but ARK_ASSET_BASE_URL is unset. "
                        "Nothing was called.", retryable=False)
                url = f"{base}/assets/bank/{asset.path.name}"
                _assert_url_fetchable(url)
                content.append({"type": "video_url", "video_url": {"url": url},
                                "role": "reference_video"})
                continue
            b64 = base64.b64encode(asset.path.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{asset.mime};base64,{b64}"},
                            "role": "reference_image"})
        _assert_assets_carried(content, len(req.inputs or []))
        return content

    # ---- transport ------------------------------------------------------
    #
    # Every failure below happens AFTER money is committed, so the rule is:
    # never create a second task while a first may exist, and never abandon a
    # task whose id we already hold. On 2026-09-10 the opposite behaviour cost
    # $33.45 — eight generated, billed clips nobody could attribute.

    def _tasks_created_since(self, since: float) -> set:
        """Ids of tasks ModelArk created at or after `since` (epoch seconds).

        Only ever called on the timeout path, so the happy path pays nothing
        for it. Read-only and never billable.
        """
        try:
            resp = self._http.get(f"{self.base_url}/contents/generations/tasks",
                                  params={"page_size": 20})
            return {t.get("id") for t in (self._checked(resp).get("items") or [])
                    if t.get("id") and (t.get("created_at") or 0) >= since}
        except Exception:
            return set()

    def _create_task(self, req: GenRequest, fields: dict) -> str:
        """Create one task, and be certain we never create two.

        A create can time out at the TCP level (errno 60 was seen repeatedly
        against ap-southeast) with the request already delivered. Blind retry
        then buys a second clip. So the task list is snapshotted first: after
        a timeout, whatever is new tells us what actually happened.
          nothing new  -> the request never landed, retry is free and safe
          exactly one  -> it landed, adopt it instead of paying twice
          more than one -> ambiguous under concurrency; refuse to guess,
                           because mis-attributing a clip to the wrong
                           scenario corrupts the study far worse than the
                           money lost by stopping.
        """
        body = {"model": self.cfg.provider_model,
                "content": self._build_content(req), **fields}
        url = f"{self.base_url}/contents/generations/tasks"
        t0 = time.time() - CREATE_CLOCK_SKEW_S
        try:
            task = self._checked(self._http.post(url, json=body))
        except httpx.TimeoutException as first:
            landed = self._tasks_created_since(t0)
            if not landed:
                try:                       # provably nothing to pay for twice
                    task = self._checked(self._http.post(url, json=body))
                except httpx.TimeoutException as second:
                    again = self._tasks_created_since(t0)
                    if len(again) == 1:
                        return again.pop()
                    raise ProviderError(
                        f"create timed out twice; {len(again)} task(s) may "
                        f"exist: {sorted(again)}. NOT retrying — a retry would "
                        f"pay for another clip. ({second})",
                        retryable=False) from second
            elif len(landed) == 1:
                return landed.pop()
            else:
                raise ProviderError(
                    f"create timed out and {len(landed)} tasks appeared "
                    f"({sorted(landed)}); cannot tell which is ours, so this "
                    f"cell is abandoned rather than mis-attributed. Reconcile "
                    f"against the ModelArk task list. ({first})",
                    retryable=False) from first
        task_id = task.get("id")
        if not task_id:
            raise ProviderError(f"create returned no task id: {task}",
                                retryable=True)
        return task_id

    def _await_task(self, task_id: str) -> dict:
        """Poll one task to a terminal state, holding on through blips.

        The task is already generating and already billing. A network error
        while polling is not a reason to give it up — the previous code let
        the runner restart the whole cell, which created a SECOND task and
        paid twice. Transient errors are absorbed here; only the deadline
        ends it, and then non-retryably.
        """
        deadline = time.monotonic() + self.timeout_s
        transient = 0
        task: dict = {}
        while True:
            if time.monotonic() > deadline:
                raise ProviderError(
                    f"task {task_id} still {task.get('status', 'unknown')} "
                    f"after {self.timeout_s:.0f}s. It is generating and billing "
                    f"regardless, so this cell is NOT retried — that would pay "
                    f"for a second clip. Recover it from the task list.",
                    retryable=False)
            try:
                task = self._checked(self._http.get(
                    f"{self.base_url}/contents/generations/tasks/{task_id}"))
                transient = 0
            except (RateLimited, ProviderError):
                raise
            except Exception:
                transient += 1
                if transient > POLL_MAX_TRANSIENT:
                    raise ProviderError(
                        f"lost contact with task {task_id} after "
                        f"{transient} consecutive polling failures; it is "
                        f"billing regardless, so this cell is NOT retried",
                        retryable=False)
                time.sleep(POLL_INTERVAL_S)
                continue
            if task.get("status") in _TERMINAL:
                break
            time.sleep(POLL_INTERVAL_S)
        if task.get("status") != "succeeded":
            raise _translate_failure(task)
        return task

    def _download(self, task: dict, task_id: str) -> bytes:
        """Fetch the finished clip. Paid for already, so try harder than once."""
        video_url = (task.get("content") or {}).get("video_url")
        if not video_url:
            raise ProviderError(f"succeeded task carried no video_url: {task}",
                                retryable=False)
        last: Exception | None = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                dl = self._http.get(video_url, headers={"Authorization": ""})
                dl.raise_for_status()
                if dl.content:
                    return bytes(dl.content)
                last = ProviderError("download returned no bytes")
            except Exception as e:
                last = e
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(2.0 * attempt)
        raise ProviderError(
            f"task {task_id} succeeded but its clip could not be downloaded "
            f"after {DOWNLOAD_ATTEMPTS} attempts ({last}). The clip is paid "
            f"for — fetch it from the task list rather than regenerating.",
            retryable=False)

    def run(self, req: GenRequest) -> GenResult:
        applied: dict = {}
        unsupported: list[str] = []

        duration_s = DEFAULT_DURATION_S
        fields: dict = {"watermark": False}
        if req.task == "video_edit":
            # The API reference's own edit example sends ratio "adaptive" and
            # duration -1, i.e. "keep the source's framing and length". That
            # matters here beyond convention: forcing a ratio or a duration on
            # an edit makes the model reframe or re-time the shot, and the
            # edit rubric then penalises it for doing exactly what we asked.
            # A scenario may still override both, and the override is recorded.
            fields["ratio"] = "adaptive"
            fields["duration"] = -1
            duration_s = None                 # the source's length, not ours
            applied["ratio"] = "adaptive"
            applied["duration_s"] = "source"
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
            task_id = self._create_task(req, fields)
            task = self._await_task(task_id)
            data = self._download(task, task_id)
        except Exception as e:
            raise _translate(e) from e
        if not data:
            raise ProviderError("download returned no bytes", retryable=True)

        usage: dict = {}
        u = task.get("usage") or {}
        if u.get("completion_tokens") is not None:
            usage["output_tokens"] = u["completion_tokens"]   # billing basis
            usage["total_tokens"] = u.get("total_tokens")
        if duration_s is None:
            # an edit keeps the source's length, so there is no requested
            # duration to record — the measured one is on the checks row
            usage["seconds_source"] = "adaptive (source length)"
        else:
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
