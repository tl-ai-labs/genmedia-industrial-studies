"""November list (100 targeted scenarios): the converted bank loads and stays
consistent with its source list, its inputs plan and the rubrics. Offline."""
import json
from collections import Counter

from runner.judge import input_media
from runner.loaders import effective_criteria, load_rubric, load_scenarios
from tests.conftest import REPO_ROOT

SOURCE = REPO_ROOT / "scenarios" / "bank-nov" / "_source"


def _scenarios():
    return load_scenarios(REPO_ROOT / "scenarios" / "tiers" / "nov-100.txt")


def test_november_bank_loads_and_resolves():
    scenarios = _scenarios()
    assert len(scenarios) == 100
    rubrics = {t: load_rubric(REPO_ROOT / "configs" / "rubrics", "image", t)
               for t in ("text_to_image", "image_edit")}
    for s in scenarios:
        crits = {c.name for c in effective_criteria(rubrics[s.task], s)}
        assert s.checklist, s.id
        for c in s.checklist:
            assert c["dim"] is None or c["dim"] in crits, (s.id, c)
        assert set(s.gate_criteria) <= crits, s.id
        if s.task == "image_edit":
            assert "source" in s.inputs and all(
                v.startswith("assets/bank-nov/") for v in s.inputs.values()), s.id
        else:
            assert not s.inputs, s.id
        w, h = (int(v) for v in s.params["size"].split("x"))
        assert w % 16 == 0 and h % 16 == 0 and max(w, h) / min(w, h) <= 3, s.id
        assert 655_360 <= w * h <= 8_294_400, s.id
        assert "seed" not in s.params, s.id
        assert abs(sum(s.weights.values()) - 1.0) < 1e-6, s.id
        assert not s.checks.get("must_read_text"), s.id   # text is judge-read
        assert s.meta["edge"] == "targeted", s.id


def test_november_split_and_image_count():
    scenarios = _scenarios()
    assert Counter(s.meta["industry"] for s in scenarios) == {
        "Advertising & agencies": 50, "Film, OTT & micro-drama": 35,
        "Media, publishing & events": 15}
    assert sum(s.meta["outputs"] for s in scenarios) == 134


def test_november_keeps_every_check_of_the_list():
    rows = {x["id"]: x for x in json.loads((SOURCE / "final-100.json").read_text())}
    for s in _scenarios():
        x = rows[s.id]
        qs = list(x["evaluation"]["checklist"])
        for m in (x.get("set") or {}).get("members", []):
            qs += m.get("checks", [])
        assert [c["q"] for c in s.checklist] == qs, s.id
        assert s.prompt == x["prompt"], s.id


def test_november_inputs_all_have_a_job():
    jobs = json.loads((REPO_ROOT / "assets" / "bank-nov" / "source-jobs.json").read_text())
    keys = {f"assets/bank-nov/inputs/{j['key']}.jpg" for j in jobs}
    used = {v for s in _scenarios() for v in s.inputs.values()}
    assert used == keys


def test_judge_shows_a_jpeg_input_as_it_is(tmp_path):
    from PIL import Image
    jpg, png = tmp_path / "a.jpg", tmp_path / "a.png"
    Image.new("RGB", (32, 32), "red").save(jpg, quality=95)
    Image.new("RGB", (32, 32), "red").save(png)
    assert input_media(jpg) == (jpg.read_bytes(), "image/jpeg")
    assert input_media(png)[1] == "image/png"


def test_november_run_text_names_no_model():
    for s in _scenarios():
        text = " ".join([s.prompt, s.expected or ""] + [c["q"] for c in s.checklist]).lower()
        for name in ("gemini", "nano banana", "gpt", "openai", "dall"):
            assert name not in text, (s.id, name)
