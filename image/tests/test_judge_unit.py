import io
import json

import pytest
from PIL import Image, PngImagePlugin

from runner.judge import (blind_order, build_judge_prompt, parse_judge_response,
                          strip_image_metadata)
from runner.loaders import Criterion, Scenario
from tests.conftest import gradient_png


def test_blind_order_deterministic_and_scenario_dependent():
    models = ["model-a", "model-b", "model-c"]
    assert blind_order("img-001", models) == blind_order("img-001", models)
    orders = {tuple(blind_order(f"img-{i:03d}", models)) for i in range(30)}
    assert len(orders) > 1  # position rotation actually rotates


def test_metadata_stripped(tmp_path):
    src = tmp_path / "meta.png"
    img = Image.open(io.BytesIO(gradient_png((64, 64))))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("provider", "gemini-super-image")
    meta.add_text("prompt", "secret")
    img.save(src, pnginfo=meta)
    assert b"gemini-super-image" in src.read_bytes()

    clean = strip_image_metadata(src)
    assert b"gemini-super-image" not in clean
    assert Image.open(io.BytesIO(clean)).size == (64, 64)


def _crits():
    return [Criterion(name="prompt_adherence", weight=0.5, judged_by="judge",
                      description="everything present"),
            Criterion(name="visual_quality", weight=0.5, judged_by="judge",
                      description="sharp, no artefacts")]


def test_prompt_contains_no_model_names_and_reasoning_first():
    s = Scenario(id="img-001", modality="image", task="text_to_image",
                 prompt="a bottle", expected="a bottle photo")
    p = build_judge_prompt(s, _crits(), ["resolution: 1024x1024"], ["text_accuracy"])
    assert "never be told which system" in p
    assert "reasoning FIRST" in p
    assert "resolution: 1024x1024" in p
    assert "text_accuracy" in p          # told not to score the measured one
    schema = json.loads(p.strip().splitlines()[-1])
    first = schema["criteria"][0]
    assert list(first.keys()) == ["name", "reasoning", "score"]  # reasoning before score


def test_parse_valid_response():
    text = json.dumps({"criteria": [
        {"name": "prompt_adherence", "reasoning": "bottle is present", "score": 8},
        {"name": "visual_quality", "reasoning": "sharp edges", "score": 7.5}],
        "overall_note": "good"})
    out = parse_judge_response(text, ["prompt_adherence", "visual_quality"])
    assert out["criteria"]["visual_quality"]["score"] == 7.5


@pytest.mark.parametrize("bad", [
    "not json at all",
    json.dumps({"criteria": [{"name": "prompt_adherence", "reasoning": "x", "score": 11}]}),
    json.dumps({"criteria": [{"name": "prompt_adherence", "reasoning": "", "score": 5}]}),
    json.dumps({"criteria": [{"name": "prompt_adherence", "reasoning": "x", "score": 5}]}),
    json.dumps({"criteria": [{"name": "mystery", "reasoning": "x", "score": 5}]}),
    json.dumps({"criteria": [{"name": "prompt_adherence", "reasoning": "x",
                              "score": True}]}),
])
def test_parse_rejects_bad_responses(bad):
    from runner.judge import JudgeSchemaError
    with pytest.raises(JudgeSchemaError):
        parse_judge_response(bad, ["prompt_adherence", "visual_quality"])
