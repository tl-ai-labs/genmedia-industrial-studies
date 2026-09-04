"""Offline fixtures: fake adapters, synthetic MP4s, a full temp project.

The whole suite runs with no API key — scoring, cost maths and checks are
pure functions, and the runner/judge are exercised through fake adapters
registered via the same registry seam a real provider would use. The fake
"clips" are minimal but well-formed ISO BMFF files built in code, so the
pure-python container parser in runner/video/checks.py is exercised for
real, not mocked.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from runner.adapters.base import Adapter, GenResult, JudgeResult  # noqa: E402


# --------------------------------------------------------------------------
# synthetic MP4 helpers — ftyp + moov(mvhd + trak/tkhd) + mdat
# --------------------------------------------------------------------------

def _box(btype: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + btype + payload


def _mvhd(duration_s: float, timescale: int = 1000) -> bytes:
    body = bytearray(100)                      # version 0 layout, zero-filled
    body[12:16] = timescale.to_bytes(4, "big")
    body[16:20] = round(duration_s * timescale).to_bytes(4, "big")
    body[20:24] = (0x00010000).to_bytes(4, "big")     # rate 1.0
    return _box(b"mvhd", bytes(body))


def _trak(width: int, height: int, duration_s: float,
          timescale: int = 1000, track_id: int = 1) -> bytes:
    body = bytearray(84)                       # tkhd version 0 layout
    body[12:16] = track_id.to_bytes(4, "big")
    body[20:24] = round(duration_s * timescale).to_bytes(4, "big")
    body[76:80] = (width << 16).to_bytes(4, "big")     # 16.16 fixed point
    body[80:84] = (height << 16).to_bytes(4, "big")
    return _box(b"trak", _box(b"tkhd", bytes(body)))


def minimal_mp4(duration_s: float = 4.0, width: int = 1280, height: int = 720,
                payload_bytes: int = 2048, audio_track: bool = False) -> bytes:
    """A well-formed-enough movie for the container parser: real box sizes,
    real mvhd/tkhd fields, junk mdat. `audio_track` adds a 0x0 trak the
    parser must skip when picking the video dimensions."""
    ftyp = _box(b"ftyp", b"isom" + (512).to_bytes(4, "big") + b"isomiso2mp41")
    traks = _trak(width, height, duration_s)
    if audio_track:
        traks += _trak(0, 0, duration_s, track_id=2)
    moov = _box(b"moov", _mvhd(duration_s) + traks)
    mdat = _box(b"mdat", bytes(payload_bytes))
    return ftyp + moov + mdat


def broken_mp4() -> bytes:
    """Not a movie at all — the decode gate must fail, never crash."""
    return b"this is not an mp4 container, just bytes with an .mp4 name" * 4


def _full_box(btype: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(btype, bytes([version]) + flags.to_bytes(3, "big") + payload)


def fragmented_mp4(duration_s: float = 8.0, width: int = 1280, height: int = 720,
                   timescale: int = 12800, fps: int = 25, fragments: int = 2,
                   per_sample_durations: bool = False) -> bytes:
    """A fragmented movie the way stock-footage encoders write them: every
    header duration is 0 (mvhd, tkhd, mdhd), fragment defaults live in
    mvex/trex, and the real duration is the sum of the moof/traf/trun sample
    durations. `per_sample_durations` puts the durations in the trun itself
    (flag 0x100) instead of relying on the trex default."""
    frame = timescale // fps
    total_frames = round(duration_s * fps)
    tkhd = bytearray(84)
    tkhd[12:16] = (1).to_bytes(4, "big")                  # track id 1, duration 0
    tkhd[76:80] = (width << 16).to_bytes(4, "big")
    tkhd[80:84] = (height << 16).to_bytes(4, "big")
    mdhd = bytearray(24)
    mdhd[12:16] = timescale.to_bytes(4, "big")            # duration stays 0
    trak = _box(b"trak", _box(b"tkhd", bytes(tkhd)) + _box(b"mdia", _box(b"mdhd", bytes(mdhd))))
    trex = _full_box(b"trex", 0, 0, (1).to_bytes(4, "big") + (1).to_bytes(4, "big")
                     + frame.to_bytes(4, "big") + (0).to_bytes(4, "big") + (0).to_bytes(4, "big"))
    moov = _box(b"moov", _mvhd(0.0, 1000) + trak + _box(b"mvex", trex))
    ftyp = _box(b"ftyp", b"iso5" + (512).to_bytes(4, "big") + b"iso5iso6mp41")
    out = ftyp + moov
    per = total_frames // fragments
    for i in range(fragments):
        n = per if i < fragments - 1 else total_frames - per * (fragments - 1)
        mfhd = _full_box(b"mfhd", 0, 0, (i + 1).to_bytes(4, "big"))
        tfhd = _full_box(b"tfhd", 0, 0x20000, (1).to_bytes(4, "big"))   # default-base-is-moof
        if per_sample_durations:
            trun = _full_box(b"trun", 0, 0x101, n.to_bytes(4, "big") + (0).to_bytes(4, "big")
                             + b"".join(frame.to_bytes(4, "big") for _ in range(n)))
        else:
            trun = _full_box(b"trun", 0, 0x1, n.to_bytes(4, "big") + (0).to_bytes(4, "big"))
        out += _box(b"moof", mfhd + _box(b"traf", tfhd + trun)) + _box(b"mdat", bytes(512))
    return out


# --------------------------------------------------------------------------
# fake generation adapter
# --------------------------------------------------------------------------

class FakeVideoAdapter(Adapter):
    """script: per-call items — an Exception to raise, or bytes to return.
    `usage` is echoed verbatim into GenResult so tests can model both the
    Veo posture ({"seconds": n, "seconds_source": "requested"} -> cost
    labelled estimated) and the Sora posture ({"seconds": n} -> api_reported).
    """

    def __init__(self, script=None, default_bytes=None, model_tag="fake",
                 usage=None):
        self.script = list(script or [])
        self.default_bytes = default_bytes or minimal_mp4()
        self.model_tag = model_tag
        self.usage = usage or {"seconds": 4}
        self.calls = 0
        self.supports = ["text_to_video"]

    def run(self, req):
        self.calls += 1
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        data = item if isinstance(item, (bytes, bytearray)) else self.default_bytes
        return GenResult(data=bytes(data), mime="video/mp4",
                         provider_version=f"{self.model_tag}-v1",
                         usage=dict(self.usage),
                         applied_params=dict(req.params),
                         params_unsupported=[],
                         request_id=f"req-{self.model_tag}-{self.calls}")


class FakeJudgeAdapter:
    """handler(prompt, media) -> str | Exception. Default: parse the schema
    from the prompt's last line and return mid-range scores. Records the
    media mimes and byte lengths so tests can assert the mp4 reached the
    judge unmodified."""

    def __init__(self, handler=None, score=8.0):
        self.handler = handler
        self.score = score
        self.calls = []

    def judge(self, prompt, media):
        self.calls.append({"prompt": prompt, "n_media": len(media),
                           "mimes": [m for _, m in media],
                           "media_bytes": [bytes(d) for d, _ in media]})
        if self.handler:
            out = self.handler(prompt, media)
            if isinstance(out, Exception):
                raise out
            return JudgeResult(text=out, provider_version="fake-judge-v1",
                               usage={"prompt_token_count": 1200,
                                      "candidates_token_count": 400})
        schema = json.loads(prompt.strip().splitlines()[-1])
        names = [c["name"] for c in schema["criteria"]]
        body = {"criteria": [{"name": n,
                              "reasoning": f"observed specifics for {n}",
                              "score": self.score} for n in names],
                "overall_note": "fake judge"}
        return JudgeResult(text=json.dumps(body), provider_version="fake-judge-v1",
                           usage={"prompt_token_count": 1200,
                                  "candidates_token_count": 400})


def install_adapters(monkeypatch, mapping: dict):
    """Route the registry seam to fakes — the same seam a real provider uses."""
    import runner.adapters as reg

    def fake_get(name, cfg, timeout_s):
        if name in mapping:
            return mapping[name]
        raise ValueError(f"test registry has no adapter {name!r}")

    monkeypatch.setattr(reg, "get", fake_get)


# --------------------------------------------------------------------------
# a full temp project (configs + scenarios copied from the repo)
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_real_providers(monkeypatch):
    """The suite is offline by contract. Every test runs with Application
    Default Credentials reported absent and every provider key unset, so a
    test that accidentally reaches a real adapter is REJECTED at pre-flight
    instead of billing someone (it happened once: a CLI test inherited the
    working copy's enabled arms and generated three real Omni clips, $1.22).
    A test that genuinely needs a live provider must opt out explicitly."""
    import runner.generate as gen
    monkeypatch.setattr(gen, "adc_available", lambda: False)
    for k in ("OPENAI_API_KEY", "ARK_API_KEY", "GEMINI_API_KEY",
              "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def project(tmp_path):
    shutil.copytree(REPO_ROOT / "configs", tmp_path / "configs")
    shutil.copytree(REPO_ROOT / "scenarios", tmp_path / "scenarios")
    (tmp_path / "runs").mkdir()
    return tmp_path


@pytest.fixture
def fake_models_yaml(project):
    """models.yaml wired to fake adapters (fake env keys set by tests).
    model-a bills like Veo (requested seconds -> estimated cost); model-b
    bills like Sora (echoed seconds -> api_reported cost)."""
    text = """
version: 1
video:
  - id: model-a
    enabled: true
    adapter: fake_a
    provider: prov_a
    provider_model: "prov-a-vid-1"
    auth_env: FAKE_KEY_A
    supports: [text_to_video]
    limits: {max_concurrency: 2}
    params: {}
    price: {unit: per_second, usd: 0.40, est_usd_per_call: 1.60,
            source: test, as_of: 2026-09-02}
  - id: model-b
    enabled: true
    adapter: fake_b
    provider: prov_b
    provider_model: "prov-b-vid-1"
    auth_env: FAKE_KEY_B
    supports: [text_to_video]
    limits: {max_concurrency: 2}
    params: {}
    price: {unit: per_second, usd: 0.10, est_usd_per_call: 0.40,
            source: test, as_of: 2026-09-02}
judge:
  video:
    adapter: fake_judge
    provider: prov_j
    provider_model: "prov-judge-1"
    auth_env: FAKE_KEY_J
    temperature: 0
    price: {unit: per_token, usd_in_per_1m: 0.5, usd_out_per_1m: 3.0,
            est_usd_per_call: 0.003, source: test, as_of: 2026-08-31}
"""
    path = project / "configs" / "models-fake.yaml"
    path.write_text(text)
    return path


@pytest.fixture
def fake_env(monkeypatch):
    for k in ("FAKE_KEY_A", "FAKE_KEY_B", "FAKE_KEY_J"):
        monkeypatch.setenv(k, "test-key")
