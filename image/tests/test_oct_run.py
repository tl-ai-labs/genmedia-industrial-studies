"""October run (scenario bank v2/v3): sets, multi-input edits, the checklist
judge, the bank's pass rule, the OCR guard for Indian scripts, and identical
pixel sizes for both arms. Offline: fake adapters through the registry seam."""
import json

import pytest
import yaml

from runner.generate import run_generation
from runner.judge import (JudgeSchemaError, build_checklist_judge_prompt, judge_run,
                          parse_checklist_response)
from runner.loaders import (Scenario, effective_criteria, enabled_models, load_models,
                            load_rubric, load_scenarios)
from runner.scoring import aggregate, checklist_verdict, score_run
from runner.telemetry import RunFiles
from tests.conftest import (REPO_ROOT, FakeImageAdapter, FakeJudgeAdapter, gradient_png,
                            install_adapters, install_fake_ocr)

CHECKLIST = [
    {"q": "Is she the same woman in all three?", "dim": "set_consistency", "scope": "whole set"},
    {"q": "Is the light the same in all three?", "dim": "set_consistency", "scope": "whole set"},
    {"q": "Is there no text?", "dim": "text_accuracy_judged", "scope": "whole set"},
    {"q": "Is the lamp lit?", "dim": "prompt_adherence", "scope": "frame 1 (Dawn)"},
]
WEIGHTS = {"set_consistency": 0.5, "prompt_adherence": 0.3, "text_accuracy_judged": 0.2}


def _set_scenario(**over) -> dict:
    doc = {
        "id": "SET-01", "modality": "image", "task": "text_to_image",
        "title": "Three frames that must agree",
        "params": {"aspect_ratio": "16:9", "size": "1376x768"},
        "prompt": "A village courtyard, one woman, cinematic. 16:9, no text.",
        "expected": "Three frames. Deliverable: Three images, 16:9.",
        "deliverable": "Three images, 16:9",
        "checks": {"min_width": 512, "min_height": 512, "not_blank": True},
        "criteria": list(WEIGHTS), "weights": WEIGHTS,
        "checklist": CHECKLIST, "gate_criteria": ["set_consistency"],
        "pass_min_score": 8.0,
        "output_set": {"mode": "each", "keep": ["Her face"],
                       "members": [{"name": "Dawn", "brief": "She lights a lamp."},
                                   {"name": "Noon", "brief": "She carries water."},
                                   {"name": "Dusk", "brief": "She sits on the step.",
                                    "params": {"aspect_ratio": "4:5", "size": "928x1152"}}]},
        "meta": {"industry": "Film"}, "tags": ["industry:Film"],
    }
    doc.update(over)
    return doc


def _checklist_judge(answers_yes=True, score=9.0):
    def handler(prompt, media):
        schema = json.loads(prompt.strip().splitlines()[-1])
        n = len([ln for ln in prompt.splitlines() if ln.startswith("  ") and ". [" in ln])
        return json.dumps({
            "checks": [{"i": i, "evidence": f"seen in frame {i}",
                        "answer": "yes" if answers_yes else "no"} for i in range(n)],
            "criteria": [{"name": c["name"], "reasoning": "specific", "score": score}
                         for c in schema["criteria"]],
            "overall_note": "fake"})
    return FakeJudgeAdapter(handler=handler)


@pytest.fixture
def set_project(project, fake_models_yaml, fake_env, monkeypatch):
    d = project / "scenarios" / "oct-test"
    d.mkdir()
    (d / "SET-01.yaml").write_text(yaml.safe_dump(_set_scenario(), sort_keys=False))
    fake_a = FakeImageAdapter(model_tag="a", default_bytes=gradient_png(phase=0))
    fake_b = FakeImageAdapter(model_tag="b", default_bytes=gradient_png(phase=3))
    judge = _checklist_judge()
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b, "fake_judge": judge})
    install_fake_ocr(monkeypatch, lambda p: [])
    scenarios = load_scenarios(d, modality="image")
    models = enabled_models(load_models(fake_models_yaml), "image")
    run_dir = run_generation(project, scenarios, models, "image", budget_usd=5.0, workers=2)
    return {"project": project, "run_dir": run_dir, "fake_a": fake_a, "fake_b": fake_b,
            "judge": judge, "scenarios": scenarios, "models": models,
            "models_yaml": fake_models_yaml}


def test_set_generates_one_request_per_member_and_a_contact_sheet(set_project):
    run_dir = set_project["run_dir"]
    out = run_dir / "outputs" / "image" / "SET-01"
    for mid in ("model-a", "model-b"):
        for k in (1, 2, 3):
            assert (out / "frames" / f"{mid}__f{k}.png").exists()   # below the scenario
        assert not list(out.glob(f"{mid}__f*"))   # nothing a folder scanner takes for an arm
        assert (out / f"{mid}.png").exists()          # the contact sheet
    assert set_project["fake_a"].calls == 3 and set_project["fake_b"].calls == 3
    rows = [r for r in RunFiles(run_dir).read("telemetry") if r["status"] == "ok"]
    assert sorted(r["member"] for r in rows if r["model_id"] == "model-a") == [1, 2, 3]
    third = next(r for r in rows if r["model_id"] == "model-a" and r["member"] == 3)
    assert third["params"]["aspect_ratio"] == "4:5"     # member override
    cells = json.loads((run_dir / "manifest.json").read_text())["cells"]
    assert all(c["state"] == "measured" for c in cells.values())


def test_set_resume_never_pays_twice(set_project):
    p = set_project
    run_id = p["run_dir"].name
    run_generation(p["project"], p["scenarios"], p["models"], "image",
                   budget_usd=5.0, workers=2, run_id=run_id)
    assert p["fake_a"].calls == 3 and p["fake_b"].calls == 3


def test_set_member_text_is_brief_plus_the_frame_line():
    s = Scenario(**_set_scenario())
    t = s.member_text(s.set_members[0])
    assert t.startswith(s.text) and "Dawn: She lights a lamp." in t


def test_set_judged_together_and_pass_rule_applied(set_project):
    p = set_project
    judge_run(p["project"], p["run_dir"], p["models_yaml"])
    calls = p["judge"].calls
    assert len(calls) == 2 and all(c["n_media"] == 3 for c in calls)
    prompt = calls[0]["prompt"]
    assert "frame 1: Dawn" in prompt and "OUTPUT SET" in prompt
    assert "resolution:" not in prompt                 # no size fact (can name a vendor)
    assert "model-a" not in prompt and "prov_a" not in prompt
    score_run(p["project"], p["run_dir"])
    scores = [r for r in RunFiles(p["run_dir"]).read("scores") if r["status"] == "scored"]
    assert len(scores) == 2
    for r in scores:
        assert r["checks_yes"] == 4 and r["checks_n"] == 4
        assert r["pass"] is True and r["gate_failed_checks"] == []
    agg = aggregate(p["run_dir"])["tasks"]["text_to_image"]["models"]
    assert agg["model-a"]["pass_rate"] == 1.0


def test_checklist_verdict_gate_and_floor():
    s = Scenario(**_set_scenario())
    yes = [{"i": i, "answer": True, "evidence": "x"} for i in range(4)]
    assert checklist_verdict(s, yes, 9.0)["pass"] is True
    assert checklist_verdict(s, yes, 7.9)["pass"] is False          # below the floor
    gate_no = [dict(c, answer=(c["i"] != 1)) for c in yes]          # a set check fails
    v = checklist_verdict(s, gate_no, 9.5)
    assert v["pass"] is False and v["gate_failed_checks"] == [1]
    other_no = [dict(c, answer=(c["i"] != 3)) for c in yes]         # a non-gate check fails
    assert checklist_verdict(s, other_no, 9.0)["pass"] is True
    plain = Scenario(**_set_scenario(gate_criteria=[], pass_min_score=None))
    assert "pass" not in checklist_verdict(plain, yes, 9.0)


def test_parse_checklist_response_is_strict():
    names = ["set_consistency"]
    ok = {"checks": [{"i": 0, "evidence": "e", "answer": "yes"},
                     {"i": 1, "evidence": "e", "answer": "no"}],
          "criteria": [{"name": "set_consistency", "reasoning": "r", "score": 7}]}
    parsed = parse_checklist_response(json.dumps(ok), names, 2)
    assert [c["answer"] for c in parsed["checks"]] == [True, False]
    with pytest.raises(JudgeSchemaError, match="unanswered"):
        parse_checklist_response(json.dumps(dict(ok, checks=ok["checks"][:1])), names, 2)
    bad = dict(ok, checks=[dict(ok["checks"][0], answer="maybe"), ok["checks"][1]])
    with pytest.raises(JudgeSchemaError, match="yes or no"):
        parse_checklist_response(json.dumps(bad), names, 2)
    dup = dict(ok, checks=[ok["checks"][0], dict(ok["checks"][0], answer="no"), ok["checks"][1]])
    with pytest.raises(JudgeSchemaError, match="more than once"):
        parse_checklist_response(json.dumps(dup), names, 2)


def test_panel_set_sends_its_panels_to_the_generator():
    s = Scenario(**_set_scenario(output_set={"mode": "panels", "keep": ["Same face"],
                                             "members": [{"name": "Joy", "brief": "She laughs."},
                                                         {"name": "Fear", "brief": "She flinches."}]}))
    assert s.set_members == []                       # one request, one image
    assert "1. Joy: She laughs." in s.gen_text and "2. Fear: She flinches." in s.gen_text
    p = build_checklist_judge_prompt(s, [], [], 1, [], [])
    assert "1. Joy: She laughs." in p and "Same face" in p


def test_set_member_files_feed_cost_once_per_cell(set_project):
    from runner.summary import summarize_run
    summ = summarize_run(set_project["run_dir"])
    cells = [c for c in summ["cells"] if c["model_id"] == "model-a"]
    assert len(cells) == 1 and cells[0]["cost_usd"] == pytest.approx(3 * 0.067)
    m = summ["models"]["model-a"]
    assert m["success_rate"] == 1.0


def test_edit_judge_sees_every_input_then_the_output(project, fake_models_yaml,
                                                      fake_env, monkeypatch):
    (project / "assets").mkdir()
    (project / "assets" / "src.png").write_bytes(gradient_png(phase=1))
    (project / "assets" / "ref.png").write_bytes(gradient_png(phase=2))
    d = project / "scenarios" / "oct-edit"
    d.mkdir()
    doc = _set_scenario(id="EDT-01", task="image_edit", output_set=None,
                        inputs={"source": "assets/src.png", "reference": "assets/ref.png"},
                        params={"size": "auto"}, gate_criteria=[], pass_min_score=None,
                        criteria=["reference_fidelity", "prompt_adherence"],
                        weights={"reference_fidelity": 0.6, "prompt_adherence": 0.4},
                        checklist=[{"q": "Same costume as the reference?",
                                    "dim": "reference_fidelity", "scope": "image"}])
    (d / "EDT-01.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    fake_a = FakeImageAdapter(model_tag="a")
    judge = _checklist_judge()
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": FakeImageAdapter(),
                                   "fake_judge": judge})
    install_fake_ocr(monkeypatch, lambda p: [])
    scenarios = load_scenarios(d, modality="image")
    models = enabled_models(load_models(fake_models_yaml), "image")   # model-b: no edits
    run_dir = run_generation(project, scenarios, models, "image", budget_usd=5.0)
    judge_run(project, run_dir, fake_models_yaml)
    assert judge.calls and judge.calls[0]["n_media"] == 3
    assert "image 1 = the image to edit; image 2 = reference" in judge.calls[0]["prompt"]


def test_ocr_never_scores_text_it_cannot_read(project, monkeypatch):
    from runner.image.checks import check_image
    install_fake_ocr(monkeypatch, lambda p: ["COSTUME CONTINUITY"])
    img = project / "x.png"
    img.write_bytes(gradient_png())
    s = Scenario(id="T", modality="image", task="text_to_image", prompt="p", expected="e",
                 checks={"must_read_text": ["కాస్ట్యూమ్ కంటిన్యూటీ"]})
    m = check_image(s, img).measures
    assert "ocr_match" not in m and m["ocr_unreadable_required"]
    s2 = s.model_copy(update={"checks": {"must_read_text": [
        "కాస్ట్యూమ్ కంటిన్యూటీ", "COSTUME CONTINUITY"]}})
    m2 = check_image(s2, img).measures
    assert m2["ocr_match"] == 1.0                     # Latin item read; Telugu not counted
    assert m2["ocr_unreadable_required"] == ["కాస్ట్యూమ్ కంటిన్యూటీ"]


@pytest.mark.parametrize("size,tier", [("1376x768", "1K"), ("1584x672", "1K"),
                                        ("1024x1024", "1K"), ("2048x2048", "2K"),
                                        ("3840x2160", "4K"), ("auto", None)])
def test_gemini_size_tier_for_pixel_requests(size, tier):
    from runner.image.gemini_image import _image_size_for
    assert _image_size_for(size) == tier


def test_october_bank_loads_and_resolves():
    """All 200 converted scenarios load; every criterion exists in the rubric;
    checklists, gates and inputs are consistent."""
    scenarios = load_scenarios(REPO_ROOT / "scenarios" / "tiers" / "oct-200.txt")
    assert len(scenarios) == 200
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
                v.startswith("assets/bank-oct/") for v in s.inputs.values()), s.id
        if s.params.get("size") not in (None, "auto"):
            w, h = (int(v) for v in s.params["size"].split("x"))
            assert w % 16 == 0 and h % 16 == 0 and max(w, h) / min(w, h) <= 3, s.id
            assert 655_360 <= w * h <= 8_294_400, s.id
        assert "seed" not in s.params, s.id
        assert s.params.get("size") != "auto", s.id       # both arms get the same canvas
        assert not s.checks.get("must_read_text"), s.id   # text is judge-read (review)


def test_failed_then_regenerated_cell_gets_scored(project, fake_models_yaml, fake_env,
                                                   monkeypatch):
    """A cell that failed on the first pass and was regenerated on a resume
    must be scored once judged; its old `failed` score row is not final."""
    from runner.adapters.base import ProviderError
    d = project / "scenarios" / "oct-retry"
    d.mkdir()
    doc = _set_scenario(id="RETRY-01", output_set=None, gate_criteria=[], pass_min_score=None,
                        criteria=["prompt_adherence"], weights={"prompt_adherence": 1.0},
                        checklist=[{"q": "Is it there?", "dim": "prompt_adherence", "scope": "image"}])
    (d / "RETRY-01.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    bad = FakeImageAdapter(model_tag="a", script=[ProviderError("boom", retryable=False)])
    judge = _checklist_judge()
    install_adapters(monkeypatch, {"fake_a": bad, "fake_b": FakeImageAdapter(model_tag="b"),
                                   "fake_judge": judge})
    install_fake_ocr(monkeypatch, lambda p: [])
    scenarios = load_scenarios(d, modality="image")
    models = enabled_models(load_models(fake_models_yaml), "image")
    run_dir = run_generation(project, scenarios, models, "image", budget_usd=5.0)
    judge_run(project, run_dir, fake_models_yaml)
    score_run(project, run_dir)
    first = {r["model_id"]: r["status"] for r in RunFiles(run_dir).read("scores")}
    assert first["model-a"] == "failed"
    run_generation(project, scenarios, models, "image", budget_usd=5.0, run_id=run_dir.name)
    judge_run(project, run_dir, fake_models_yaml)
    score_run(project, run_dir)
    latest = {}
    for r in RunFiles(run_dir).read("scores"):
        latest[r["model_id"]] = r["status"]
    assert latest["model-a"] == "scored"
