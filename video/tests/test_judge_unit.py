import io
import json

import pytest
from PIL import Image, PngImagePlugin

from runner.judge import (blind_order, build_judge_prompt, parse_judge_response,
                          strip_image_metadata)
from runner.loaders import Criterion, Scenario


def small_png(size=(64, 64)) -> bytes:
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = ((x * 4) % 256, (y * 4) % 256, ((x + y) * 2) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_blind_order_deterministic_and_scenario_dependent():
    models = ["model-a", "model-b", "model-c"]
    assert blind_order("VID-CIN-01", models) == blind_order("VID-CIN-01", models)
    orders = {tuple(blind_order(f"VID-{i:03d}", models)) for i in range(30)}
    assert len(orders) > 1  # position rotation actually rotates


def test_image_metadata_strip_still_works(tmp_path):
    # the image path of the judge remains intact for mixed deployments
    src = tmp_path / "meta.png"
    img = Image.open(io.BytesIO(small_png()))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("provider", "gemini-super-image")
    img.save(src, pnginfo=meta)
    assert b"gemini-super-image" in src.read_bytes()

    clean = strip_image_metadata(src)
    assert b"gemini-super-image" not in clean
    assert Image.open(io.BytesIO(clean)).size == (64, 64)


def _crits():
    return [Criterion(name="prompt_adherence", weight=0.5, judged_by="judge",
                      description="everything present"),
            Criterion(name="motion_coherence", weight=0.5, judged_by="judge",
                      description="movement plausible and continuous")]


def _scenario():
    return Scenario(id="VID-CIN-01", modality="video", task="text_to_video",
                    prompt="a slow dolly-in", expected="a steady dolly-in shot")


def test_prompt_is_modality_aware_and_reasoning_first():
    p = build_judge_prompt(_scenario(), _crits(),
                           ["resolution: 1920x1080",
                            "clip duration: 8.00s (from the container header)"],
                           ["technical_compliance"])
    assert "one generated video clip" in p
    assert "never be told which system" in p
    assert "reasoning FIRST" in p
    assert "clip duration: 8.00s" in p
    assert "technical_compliance" in p   # told not to score the measured one
    schema = json.loads(p.strip().splitlines()[-1])
    first = schema["criteria"][0]
    assert list(first.keys()) == ["name", "reasoning", "score"]  # reasoning before score


def test_prompt_noun_for_image_scenarios_unchanged():
    s = Scenario(id="img-x", modality="image", task="text_to_image",
                 prompt="a bottle", expected="a bottle photo")
    assert "one generated image" in build_judge_prompt(s, _crits(), [], [])


def test_parse_valid_response():
    text = json.dumps({"criteria": [
        {"name": "prompt_adherence", "reasoning": "dolly-in is present", "score": 8},
        {"name": "motion_coherence", "reasoning": "steady advance", "score": 7.5}],
        "overall_note": "good"})
    out = parse_judge_response(text, ["prompt_adherence", "motion_coherence"])
    assert out["criteria"]["motion_coherence"]["score"] == 7.5


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
        parse_judge_response(bad, ["prompt_adherence", "motion_coherence"])
