"""Offline fixtures: fake adapters, fake OCR, a full temp project.

The whole suite runs with no API key — scoring, cost maths and checks are
pure functions, and the runner/judge are exercised through fake adapters
registered via the same registry seam a real provider would use.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from runner.adapters.base import Adapter, GenResult, JudgeResult  # noqa: E402


# --------------------------------------------------------------------------
# image helpers
# --------------------------------------------------------------------------

def gradient_png(size=(1024, 1024), phase=0) -> bytes:
    """Deterministic non-blank image; `phase` varies the pattern."""
    w, h = size
    x = np.linspace(0, 255, w, dtype=np.float64)
    y = np.linspace(0, 255, h, dtype=np.float64)[:, None]
    arr = ((x + y + phase * 40) % 256).astype(np.uint8)
    img = Image.fromarray(np.stack([arr, arr[::-1], arr], axis=-1).astype(np.uint8)
                          if arr.ndim == 2 else arr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def photo_png(size=(1024, 1024), seed=7) -> bytes:
    """Photo-like image with smooth blobs — stable under pHash, unlike the
    sawtooth gradient whose repeating ramps sit on DCT knife edges."""
    import random
    from PIL import ImageDraw, ImageFilter
    rng = random.Random(seed)
    img = Image.new("RGB", size, (110, 125, 118))
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x, y = rng.randint(0, size[0]), rng.randint(0, size[1])
        r = rng.randint(size[0] // 10, size[0] // 3)
        color = tuple(rng.randint(40, 220) for _ in range(3))
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)
    img = img.filter(ImageFilter.GaussianBlur(size[0] // 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def blank_png(size=(1024, 1024), value=200) -> bytes:
    img = Image.new("RGB", size, (value, value, value))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# fake generation adapter
# --------------------------------------------------------------------------

class FakeImageAdapter(Adapter):
    """script: per-call items — an Exception to raise, or bytes to return."""

    def __init__(self, script=None, default_bytes=None, model_tag="fake"):
        self.script = list(script or [])
        self.default_bytes = default_bytes or gradient_png()
        self.model_tag = model_tag
        self.calls = 0
        self.supports = ["text_to_image", "image_edit"]

    def run(self, req):
        self.calls += 1
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        data = item if isinstance(item, (bytes, bytearray)) else self.default_bytes
        return GenResult(data=bytes(data), mime="image/png",
                         provider_version=f"{self.model_tag}-v1",
                         usage={"images": 1},
                         applied_params=dict(req.params),
                         params_unsupported=[],
                         request_id=f"req-{self.model_tag}-{self.calls}")


class FakeJudgeAdapter:
    """handler(prompt, media) -> str | Exception. Default: parse the schema
    from the prompt's last line and return mid-range scores."""

    def __init__(self, handler=None, score=8.0):
        self.handler = handler
        self.score = score
        self.calls = []

    def judge(self, prompt, media):
        self.calls.append({"prompt": prompt, "n_media": len(media)})
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


def install_fake_ocr(monkeypatch, text_for_path):
    """text_for_path: callable(path) -> list[str] of OCR fragments."""
    import runner.image.checks as checks
    monkeypatch.setattr(checks, "_OCR_ENGINE", text_for_path)


# --------------------------------------------------------------------------
# a full temp project (configs + scenarios copied from the repo)
# --------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    shutil.copytree(REPO_ROOT / "configs", tmp_path / "configs")
    shutil.copytree(REPO_ROOT / "scenarios", tmp_path / "scenarios")
    (tmp_path / "runs").mkdir()
    return tmp_path


@pytest.fixture
def fake_models_yaml(project):
    """models.yaml wired to fake adapters (fake env keys set by tests)."""
    text = """
version: 1
image:
  - id: model-a
    enabled: true
    adapter: fake_a
    provider: prov_a
    provider_model: "prov-a-img-1"
    auth_env: FAKE_KEY_A
    supports: [text_to_image, image_edit]
    limits: {max_concurrency: 2}
    params: {size: "1024x1024"}
    price: {unit: per_image, usd: 0.067, tier: "1K", source: test, as_of: 2026-08-31}
  - id: model-b
    enabled: true
    adapter: fake_b
    provider: prov_b
    provider_model: "prov-b-img-1"
    auth_env: FAKE_KEY_B
    supports: [text_to_image]
    limits: {max_concurrency: 2}
    params: {size: "1024x1024"}
    price: {unit: per_token, usd_in_per_1m: 10.0, usd_out_per_1m: 40.0,
            est_usd_per_call: 0.07, source: test, as_of: 2026-08-31}
judge:
  image:
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
