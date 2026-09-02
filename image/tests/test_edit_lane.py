"""image_edit lane, offline: frozen+hashed inputs, adapters receive assets,
global pHash preservation gate, judge sees source+result, preservation is a
measured criterion. A whole-image repaint must earn 0 objectively."""
import io
import json

import pytest
from PIL import Image

from runner.adapters.base import Adapter, GenResult
from runner.generate import run_generation
from runner.judge import judge_run
from runner.loaders import enabled_models, load_models, load_scenarios
from runner.scoring import preservation_score, score_run
from runner.telemetry import RunFiles
from tests.conftest import (FakeJudgeAdapter, blank_png, photo_png,
                            install_adapters, install_fake_ocr)


class FakeEditAdapter(Adapter):
    """Returns the source with a small local change (faithful editor) or a
    completely different image (repainter)."""

    def __init__(self, repaint=False):
        self.repaint = repaint
        self.requests = []
        self.supports = ["text_to_image", "image_edit"]

    def run(self, req):
        self.requests.append(req)
        if self.repaint:
            data = blank_png((1024, 1024), value=30)
        else:
            src = Image.open(req.inputs[0].path).convert("RGB")
            px = src.load()
            for x in range(80):        # small local edit
                for y in range(80):
                    px[x, y] = (250, 40, 40)
            buf = io.BytesIO()
            src.save(buf, format="PNG")
            data = buf.getvalue()
        return GenResult(data=data, mime="image/png", provider_version="fake-edit",
                         usage={"images": 1}, applied_params=dict(req.params))


@pytest.fixture
def edit_scenario(project):
    assets = project / "assets" / "bank"
    assets.mkdir(parents=True)
    (assets / "e-1-source.png").write_bytes(photo_png((1024, 1024)))
    scn = project / "scenarios" / "edit"
    scn.mkdir()
    (scn / "e-1.yaml").write_text("""
id: e-1
modality: image
task: image_edit
title: recolor corner
inputs: {source: assets/bank/e-1-source.png}
prompt: "Turn the top-left corner red. Change nothing else."
expected: "Only the corner changes."
checks:
  min_width: 700
  min_height: 700
  not_blank: true
  preservation: {method: phash, max_distance: 12}
criteria: [edit_fidelity, preservation, visual_quality, realism_style]
weights: {edit_fidelity: 0.35, preservation: 0.30, visual_quality: 0.20,
          realism_style: 0.15}
""")
    return scn


def test_edit_requires_source_input(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nmodality: image\ntask: image_edit\nprompt: p\nexpected: e\n")
    with pytest.raises(Exception, match="requires an input asset"):
        load_scenarios(bad)


def test_edit_lane_end_to_end(project, fake_models_yaml, fake_env,
                              monkeypatch, edit_scenario):
    editor = FakeEditAdapter(repaint=False)
    repainter = FakeEditAdapter(repaint=True)
    judge = FakeJudgeAdapter(score=8.0)
    install_adapters(monkeypatch, {"fake_a": editor, "fake_b": repainter,
                                   "fake_judge": judge})
    install_fake_ocr(monkeypatch, lambda p: [])

    scenarios = load_scenarios(edit_scenario, modality="image")
    models = enabled_models(load_models(fake_models_yaml), "image")
    # only model-a supports image_edit in the fixture; give b support too
    models[1].supports = ["text_to_image", "image_edit"]
    run_dir = run_generation(project, scenarios, models, "image", budget_usd=5.0)

    # inputs frozen + hashed; adapters received the frozen asset
    manifest = json.loads((run_dir / "manifest.json").read_text())
    frozen = manifest["inputs"]["e-1"]
    assert frozen[0]["role"] == "source" and len(frozen[0]["sha256"]) == 64
    for ad in (editor, repainter):
        req = ad.requests[0]
        assert req.task == "image_edit"
        assert req.inputs[0].role == "source"
        assert str(run_dir) in str(req.inputs[0].path)   # frozen copy, not assets/

    files = RunFiles(run_dir)
    checks = {r["model_id"]: r for r in files.read("checks")}
    ed, rp = checks["model-a"], checks["model-b"]
    assert ed["passed"] and ed["measures"]["preservation_phash_distance"] <= 12
    assert not rp["passed"]     # repaint fails the preservation gate
    assert manifest["cells"]["e-1::model-b"]["state"] == "invalid"

    judge_run(project, run_dir, fake_models_yaml)
    jrows = files.read("judge")
    assert [r["model_id"] for r in jrows] == ["model-a"]   # invalid never judged
    assert judge.calls[0]["n_media"] == 2                  # source + result, blind

    score_run(project, run_dir)
    scores = {r["model_id"]: r for r in files.read("scores")}
    a, b = scores["model-a"], scores["model-b"]
    assert b["status"] == "invalid" and b["score"] == 0.0  # the earned zero
    assert a["criteria"]["preservation"]["source"] == "measured"
    assert a["criteria"]["preservation"]["score"] > 5      # inside the bound
    assert a["score"] > 5


def test_preservation_score_mapping():
    assert preservation_score({"preservation_phash_distance": 0,
                               "preservation_phash_max": 12}) == 10.0
    assert preservation_score({"preservation_phash_distance": 12,
                               "preservation_phash_max": 12}) == 5.0
    assert preservation_score({"preservation_phash_distance": 24,
                               "preservation_phash_max": 12}) == 0.0
    assert preservation_score({"preservation_ssim_outside": 1.0,
                               "preservation_ssim_min": 0.92}) == 10.0
    assert preservation_score({"preservation_ssim_outside": 0.92,
                               "preservation_ssim_min": 0.92}) == 5.0
    assert preservation_score({}) is None                  # unmeasured
