"""The real provider adapters, driven against in-process stubs: parameter
translation, LRO/job polling, byte extraction, usage honesty and the error
taxonomy — everything that cannot be integration-tested without spend."""
import pytest

from runner.adapters.base import Asset, GenRequest, SafetyRefusal, ProviderError
from runner.loaders import ModelCfg
from runner.video.openai_video import _pick_size
from tests.conftest import minimal_mp4


def _req(**params):
    return GenRequest(task="text_to_video", text="a slow dolly-in",
                      inputs=[], params=params)


def _cfg(adapter, provider_model, **kw):
    base = dict(id="m", enabled=True, adapter=adapter, provider="p",
                provider_model=provider_model, auth_env="FAKE_KEY",
                supports=["text_to_video"],
                price={"unit": "per_second", "usd": 0.10,
                       "est_usd_per_call": 0.80, "source": "t",
                       "as_of": "2026-09-02"})
    base.update(kw)
    return ModelCfg(**base)


# ---- Sora size mapping ----------------------------------------------------

def test_sora2_downgrades_1080p_and_records_it():
    applied, unsupported = {}, []
    size = _pick_size({"resolution": "1080p", "aspect_ratio": "16:9"},
                      "sora-2-2025-12-08", applied, unsupported)
    assert size == "1280x720"
    assert "downgraded from 1080p" in applied["resolution"]
    assert "resolution:1080p" in unsupported


def test_sora2_pro_serves_1080p():
    applied, unsupported = {}, []
    size = _pick_size({"resolution": "1080p", "aspect_ratio": "16:9"},
                      "sora-2-pro-2025-10-06", applied, unsupported)
    assert size == "1920x1080" and unsupported == []


def test_sora_portrait_orientation():
    applied, unsupported = {}, []
    assert _pick_size({"resolution": "720p", "aspect_ratio": "9:16"},
                      "sora-2", applied, unsupported) == "720x1280"


# ---- OpenAI adapter against a stub client ---------------------------------

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _StubVideos:
    def __init__(self, statuses, data, error=None, seconds="8"):
        self.statuses = list(statuses)
        self.data = data
        self.error = error
        self.seconds = seconds
        self.created_with = None

    def create(self, **kw):
        self.created_with = kw
        return _Obj(id="video_123", status=self.statuses.pop(0),
                    seconds=self.seconds, model="sora-2-2025-12-08",
                    error=self.error)

    def retrieve(self, vid):
        return _Obj(id=vid, status=self.statuses.pop(0), seconds=self.seconds,
                    model="sora-2-2025-12-08", error=self.error)

    def download_content(self, vid):
        return _Obj(content=self.data)


def _openai_adapter(monkeypatch, stub):
    monkeypatch.setenv("FAKE_KEY", "k")
    from runner.video import openai_video
    monkeypatch.setattr(openai_video, "POLL_INTERVAL_S", 0)
    adapter = openai_video.build(_cfg("openai_video", "sora-2-2025-12-08"),
                                 timeout_s=30)
    adapter.client = _Obj(videos=stub)
    return adapter


def test_openai_adapter_happy_path(monkeypatch):
    clip = minimal_mp4()
    stub = _StubVideos(["queued", "in_progress", "completed"], clip)
    adapter = _openai_adapter(monkeypatch, stub)
    res = adapter.run(_req(duration_s=8, resolution="1080p", aspect_ratio="16:9",
                           audio=False))
    assert res.data == clip and res.mime == "video/mp4"
    assert stub.created_with["seconds"] == "8"
    assert stub.created_with["size"] == "1280x720"      # sora-2 ceiling
    assert res.usage == {"seconds": 8.0}                # echoed -> api_reported
    assert "seconds_source" not in res.usage
    assert "audio" in res.params_unsupported            # no audio toggle on Sora
    assert res.request_id == "video_123"


def test_openai_adapter_moderation_failure_is_refusal(monkeypatch):
    stub = _StubVideos(["queued", "failed"], b"",
                       error=_Obj(code="moderation_blocked", message="nope"))
    adapter = _openai_adapter(monkeypatch, stub)
    with pytest.raises(SafetyRefusal):
        adapter.run(_req(duration_s=8, resolution="720p"))


def test_openai_adapter_generic_failure_is_provider_error(monkeypatch):
    stub = _StubVideos(["failed"], b"",
                       error=_Obj(code="internal_error", message="boom"))
    adapter = _openai_adapter(monkeypatch, stub)
    with pytest.raises(ProviderError) as e:
        adapter.run(_req(duration_s=8, resolution="720p"))
    assert e.value.retryable


# ---- Veo adapter against a stub client ------------------------------------

class _StubGoogle:
    """models.generate_videos -> pending op; operations.get -> done op."""

    def __init__(self, video_bytes=None, op_error=None, videos=None,
                 pending_polls=1):
        self.video_bytes = video_bytes
        self.op_error = op_error
        self.videos = videos
        self.pending_polls = pending_polls
        self.generate_kwargs = None
        self.models = _Obj(generate_videos=self._generate)
        self.operations = _Obj(get=self._get)
        self.files = _Obj(download=lambda file: None)

    def _done_op(self):
        if self.op_error is not None:
            return _Obj(done=True, name="op/1", error=self.op_error, response=None)
        vids = self.videos
        if vids is None:
            vids = [_Obj(video=_Obj(video_bytes=self.video_bytes,
                                    mime_type="video/mp4"))]
        return _Obj(done=True, name="op/1", error=None,
                    response=_Obj(generated_videos=vids, model_version="veo-x"))

    def _generate(self, model, prompt, config):
        self.generate_kwargs = {"model": model, "prompt": prompt, "config": config}
        if self.pending_polls <= 0:
            return self._done_op()
        return _Obj(done=False, name="op/1", error=None, response=None)

    def _get(self, op):
        self.pending_polls -= 1
        if self.pending_polls <= 0:
            return self._done_op()
        return _Obj(done=False, name="op/1", error=None, response=None)


def _veo_adapter(monkeypatch, stub):
    from runner.video import veo_video
    monkeypatch.setattr(veo_video, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(veo_video, "_make_client",
                        lambda genai, types, cfg, timeout_s: stub)
    return veo_video.build(
        _cfg("veo_video", "veo-3.1-generate-001", auth_env=None,
             vertex={"project": "ai-studies-console", "location": "global"}),
        timeout_s=30)


def test_veo_adapter_happy_path(monkeypatch):
    clip = minimal_mp4()
    stub = _StubGoogle(video_bytes=clip, pending_polls=2)
    adapter = _veo_adapter(monkeypatch, stub)
    res = adapter.run(_req(duration_s=8, resolution="1080p",
                           aspect_ratio="16:9", audio=False))
    assert res.data == clip and res.mime == "video/mp4"
    cfg = stub.generate_kwargs["config"]
    assert cfg.duration_seconds == 8
    assert cfg.resolution == "1080p"
    assert cfg.aspect_ratio == "16:9"
    assert cfg.generate_audio is False
    # nothing reported back -> requested seconds, labelled as such
    assert res.usage == {"seconds": 8, "seconds_source": "requested"}
    assert res.applied_params["duration_s"] == 8
    assert res.request_id == "op/1"


def test_veo_adapter_op_error_maps_to_taxonomy(monkeypatch):
    stub = _StubGoogle(op_error={"code": 400,
                                 "message": "blocked by safety policy"})
    adapter = _veo_adapter(monkeypatch, stub)
    with pytest.raises(SafetyRefusal):
        adapter.run(_req(duration_s=8))

    stub = _StubGoogle(op_error={"code": 500, "message": "internal"})
    adapter = _veo_adapter(monkeypatch, stub)
    with pytest.raises(ProviderError) as e:
        adapter.run(_req(duration_s=8))
    assert e.value.retryable


def test_veo_adapter_filtered_output_is_refusal(monkeypatch):
    stub = _StubGoogle(videos=[])
    stub.op_error = None
    # completed op, zero videos, RAI filter reason present
    done = stub._done_op()
    done.response.rai_media_filtered_reasons = ["celebrity likeness"]
    stub._done_op = lambda: done
    adapter = _veo_adapter(monkeypatch, stub)
    with pytest.raises(SafetyRefusal, match="filtered"):
        adapter.run(_req(duration_s=8))


def test_veo_adapter_unknown_param_recorded_not_sent(monkeypatch):
    stub = _StubGoogle(video_bytes=minimal_mp4(), pending_polls=0)
    adapter = _veo_adapter(monkeypatch, stub)
    res = adapter.run(_req(duration_s=8, sparkle="max"))
    assert res.params_unsupported == ["sparkle"]
    assert not hasattr(stub.generate_kwargs["config"], "sparkle")


# ---- Omni Flash adapter (Interactions API) --------------------------------

class _StubInteractions:
    def __init__(self, statuses, video_b64=None, errors=None, usage=None):
        self.statuses = list(statuses)
        self.video_b64 = video_b64
        self.errors = errors
        self.usage = usage
        self.create_kwargs = None

    def _obj(self, status):
        video = _Obj(data=self.video_b64, mime_type="video/mp4", uri=None)
        return _Obj(id="int_1", status=status, output_video=video,
                    errors=self.errors, usage=self.usage,
                    model="gemini-omni-flash-preview")

    def create(self, **kw):
        self.create_kwargs = kw
        return self._obj(self.statuses.pop(0))

    def get(self, iid):
        return self._obj(self.statuses.pop(0))


def _omni_adapter(monkeypatch, stub):
    from runner.video import omni_video
    monkeypatch.setattr(omni_video, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(omni_video, "_make_client",
                        lambda genai, types, cfg, timeout_s: _Obj(interactions=stub))
    return omni_video.build(
        _cfg("omni_video", "gemini-omni-flash-preview", auth_env=None,
             vertex={"project": "ai-studies-console", "location": "global"}),
        timeout_s=30)


def test_omni_adapter_happy_path(monkeypatch):
    import base64
    clip = minimal_mp4()
    stub = _StubInteractions(["queued", "in_progress", "completed"],
                             video_b64=base64.b64encode(clip).decode(),
                             usage=_Obj(total_output_tokens=46336,
                                        total_input_tokens=12,
                                        total_tokens=46348))
    adapter = _omni_adapter(monkeypatch, stub)
    res = adapter.run(_req(duration_s=8, resolution="1080p",
                           aspect_ratio="16:9"))
    assert res.data == clip and res.mime == "video/mp4"
    fmt = stub.create_kwargs["response_format"]
    assert fmt == {"type": "video", "delivery": "inline", "duration": "8s",
                   "resolution": "1080p", "aspect_ratio": "16:9"}
    # response_modalities must NOT be sent: Vertex 404s when it accompanies
    # response_format (verified against the live endpoint 2026-09-03)
    assert "response_modalities" not in stub.create_kwargs
    # token counts are the billing basis and are api-reported
    assert res.usage["output_tokens"] == 46336
    assert res.request_id == "int_1"


def test_omni_adapter_safety_failure_is_refusal(monkeypatch):
    stub = _StubInteractions(["failed"],
                             errors=[_Obj(message="blocked by safety policy")])
    adapter = _omni_adapter(monkeypatch, stub)
    with pytest.raises(SafetyRefusal):
        adapter.run(_req(duration_s=8))


def test_omni_adapter_budget_exceeded_is_terminal(monkeypatch):
    stub = _StubInteractions(["budget_exceeded"])
    adapter = _omni_adapter(monkeypatch, stub)
    with pytest.raises(ProviderError) as e:
        adapter.run(_req(duration_s=8))
    assert not e.value.retryable            # retrying a budget stop is waste


def test_omni_adapter_audio_param_recorded_unsupported(monkeypatch):
    import base64
    stub = _StubInteractions(["completed"],
                             video_b64=base64.b64encode(minimal_mp4()).decode())
    adapter = _omni_adapter(monkeypatch, stub)
    res = adapter.run(_req(duration_s=8, audio=False))
    assert "audio" in res.params_unsupported


# ---- Seedance adapter (BytePlus ModelArk REST) ----------------------------

class _StubResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_body or {}
        self.content = content
        self.text = text or str(json_body or "")
        self.headers = {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _StubHttp:
    """Serves create -> poll -> download in order."""

    def __init__(self, statuses, clip, usage=None, error=None, create_status=200,
                 create_text=""):
        self.statuses = list(statuses)
        self.clip = clip
        self.usage = usage
        self.error = error
        self.create_status = create_status
        self.create_text = create_text
        self.posted = None

    def post(self, url, json=None):
        self.posted = {"url": url, "json": json}
        if self.create_status != 200:
            return _StubResponse(self.create_status, text=self.create_text)
        return _StubResponse(200, {"id": "task_9", "status": self.statuses.pop(0)})

    def get(self, url, headers=None):
        if url.startswith("https://cdn"):          # the signed download url
            return _StubResponse(200, content=self.clip)
        status = self.statuses.pop(0)
        body = {"id": "task_9", "status": status,
                "model": "dreamina-seedance-2-5-260628"}
        if status == "succeeded":
            body["content"] = {"video_url": "https://cdn.example/task_9.mp4"}
            body["usage"] = self.usage or {"completion_tokens": 265700,
                                           "total_tokens": 265712}
        if self.error:
            body["error"] = self.error
        return _StubResponse(200, body)


def _seedance_adapter(monkeypatch, stub):
    monkeypatch.setenv("ARK_API_KEY", "k")
    from runner.video import seedance_video
    monkeypatch.setattr(seedance_video, "POLL_INTERVAL_S", 0)
    adapter = seedance_video.build(
        _cfg("seedance_video", "dreamina-seedance-2-5-260628",
             auth_env="ARK_API_KEY"), timeout_s=30)
    adapter._http = stub
    return adapter


def test_seedance_adapter_happy_path(monkeypatch):
    clip = minimal_mp4()
    stub = _StubHttp(["queued", "running", "succeeded"], clip)
    adapter = _seedance_adapter(monkeypatch, stub)
    res = adapter.run(_req(duration_s=8, resolution="1080p", aspect_ratio="16:9",
                           audio=False))
    assert res.data == clip and res.mime == "video/mp4"
    body = stub.posted["json"]
    # the prompt goes verbatim; run settings are top-level API fields
    assert body["content"] == [{"type": "text", "text": "a slow dolly-in"}]
    assert body["duration"] == 8 and body["resolution"] == "1080p"
    assert body["ratio"] == "16:9"
    assert body["generate_audio"] is False        # API default is true — must be sent
    assert body["watermark"] is False
    assert res.applied_params["audio"] is False and res.applied_params["watermark"] is False
    assert res.params_unsupported == []
    assert res.usage["output_tokens"] == 265700      # billing basis
    assert res.request_id == "task_9"


def test_seedance_adapter_forwards_audio_on_for_avatar_rows(monkeypatch):
    stub = _StubHttp(["queued", "succeeded"], minimal_mp4())
    adapter = _seedance_adapter(monkeypatch, stub)
    adapter.run(_req(duration_s=8, resolution="720p", audio=True))
    assert stub.posted["json"]["generate_audio"] is True


def test_seedance_adapter_failed_task_with_moderation_is_refusal(monkeypatch):
    stub = _StubHttp(["queued", "failed"], b"",
                     error={"code": "OutputVideoSensitiveContentDetected",
                            "message": "risk control"})
    adapter = _seedance_adapter(monkeypatch, stub)
    with pytest.raises(SafetyRefusal):
        adapter.run(_req(duration_s=8, resolution="720p"))


def test_seedance_adapter_rate_limit_maps_to_taxonomy(monkeypatch):
    from runner.adapters.base import RateLimited
    stub = _StubHttp(["queued"], b"", create_status=429, create_text="slow down")
    adapter = _seedance_adapter(monkeypatch, stub)
    with pytest.raises(RateLimited):
        adapter.run(_req(duration_s=8))


def test_seedance_adapter_4xx_is_not_retried(monkeypatch):
    stub = _StubHttp(["queued"], b"", create_status=400, create_text="bad model id")
    adapter = _seedance_adapter(monkeypatch, stub)
    with pytest.raises(ProviderError) as e:
        adapter.run(_req(duration_s=8))
    assert not e.value.retryable


# --------------------------------------------------------------------------
# asset-fed payloads: shapes verified against the SDK / API reference
# --------------------------------------------------------------------------

def _asset(tmp_path, name, data, mime):
    from runner.adapters.base import Asset
    p = tmp_path / name
    p.write_bytes(data)
    return Asset(role="source" if mime.startswith("video/") else "reference",
                 path=p, mime=mime, sha256="x" * 64)


def test_omni_input_parts_are_flat_and_the_sdk_keeps_the_data(tmp_path):
    """The Interactions content union is OPEN: a nested part validates
    cleanly and silently yields data=None, so the asset would be dropped and
    the model would generate from the prompt alone while the run looked
    successful. This asserts the shape the SDK actually preserves."""
    pydantic = pytest.importorskip("pydantic")
    content = pytest.importorskip(
        "google.genai._gaos.types.interactions.content")

    from runner.adapters.base import GenRequest
    from runner.video.omni_video import OmniFlashVideoAdapter

    a = OmniFlashVideoAdapter.__new__(OmniFlashVideoAdapter)
    req = GenRequest(task="video_edit", text="make it yellow",
                     inputs=[_asset(tmp_path, "s.mp4", b"CLIP", "video/mp4")],
                     params={})
    parts = a._build_input(req)
    assert parts[0] == {"type": "text", "text": "make it yellow"}
    assert parts[1]["type"] == "video" and parts[1]["mime_type"] == "video/mp4"
    assert parts[1]["data"], "the asset carries no data"

    # round-trip through the SDK's own union: data must survive
    parsed = pydantic.TypeAdapter(list[content.Content]).validate_python(parts)
    assert type(parsed[1]).__name__ == "VideoContent"
    assert parsed[1].data, "the SDK dropped the asset"

    # and the nested shape — the one that looks right — must NOT be used
    nested = [{"type": "video", "video": {"data": "x", "mime_type": "video/mp4"}}]
    dropped = pydantic.TypeAdapter(list[content.Content]).validate_python(nested)
    assert dropped[0].data is None      # exactly the silent failure guarded against


def test_omni_refuses_to_send_a_payload_that_lost_its_asset(tmp_path):
    from runner.adapters.base import ProviderError
    from runner.video.omni_video import _assert_assets_carried
    good = [{"type": "text", "text": "t"},
            {"type": "image", "data": "abc", "mime_type": "image/png"}]
    _assert_assets_carried(good, 1)                       # no raise
    for bad in ([{"type": "text", "text": "t"}],          # asset vanished
                [{"type": "text", "text": "t"},
                 {"type": "image", "image": {"data": "abc"}}]):   # nested
        with pytest.raises(ProviderError, match="did not survive"):
            _assert_assets_carried(bad, 1)


def test_seedance_content_matches_the_documented_examples(tmp_path):
    """Shapes taken from the Seedance 2.5 API reference's own worked
    examples: a data URI for a local file, and a `role` on 2.5 reference
    parts."""
    from runner.adapters.base import GenRequest
    from runner.video.seedance_video import SeedanceVideoAdapter

    a = SeedanceVideoAdapter.__new__(SeedanceVideoAdapter)
    clip = a._build_content(GenRequest(
        task="video_edit", text="remove the extras",
        inputs=[_asset(tmp_path, "s.mp4", b"CLIP", "video/mp4")], params={}))
    assert clip[0] == {"type": "text", "text": "remove the extras"}
    assert clip[1]["type"] == "video_url"
    assert clip[1]["role"] == "reference_video"
    assert clip[1]["video_url"]["url"].startswith("data:video/mp4;base64,")

    still = a._build_content(GenRequest(
        task="image_to_video", text="rotate it",
        inputs=[_asset(tmp_path, "r.png", b"PNG", "image/png")], params={}))
    assert still[1]["type"] == "image_url"
    assert still[1]["role"] == "reference_image"
    assert still[1]["image_url"]["url"].startswith("data:image/png;base64,")

    # text-only is untouched — the path already proven against the live API
    plain = a._build_content(GenRequest(task="text_to_video", text="a cat",
                                        inputs=[], params={}))
    assert plain == [{"type": "text", "text": "a cat"}]
