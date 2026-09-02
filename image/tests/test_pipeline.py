"""End-to-end offline: run -> checks -> judge -> score -> report, through the
same registry seam a real provider uses. No network, no keys, no spend."""
import json

import pytest

from runner.generate import RunRejected, run_generation
from runner.judge import judge_run
from runner.loaders import enabled_models, load_models, load_scenarios
from runner.report import build_report
from runner.scoring import aggregate, score_run
from runner.telemetry import RunFiles
from tests.conftest import (FakeImageAdapter, FakeJudgeAdapter, blank_png,
                            gradient_png, install_adapters, install_fake_ocr)
from runner.adapters.base import ProviderError, RateLimited, SafetyRefusal


def _load(project, models_yaml):
    scenarios = load_scenarios(project / "scenarios", modality="image")
    models = enabled_models(load_models(models_yaml), "image")
    return scenarios, models


def _ocr_by_model(path):
    p = str(path)
    if "model-a" in p:
        return ["TRAILHEAD 750", "FRESH FILTER COFFEE"]   # reads perfectly
    return ["TRAILHFAD 750", "FRESH FILTER COFFE"]        # typo'd


@pytest.fixture
def happy_run(project, fake_models_yaml, fake_env, monkeypatch):
    fake_a = FakeImageAdapter(model_tag="a", default_bytes=gradient_png(phase=0))
    fake_b = FakeImageAdapter(model_tag="b", default_bytes=gradient_png(phase=3))
    judge = FakeJudgeAdapter(score=8.0)
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    install_fake_ocr(monkeypatch, _ocr_by_model)
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios, models, "image",
                             budget_usd=5.0, workers=2)
    return {"project": project, "run_dir": run_dir, "models_yaml": fake_models_yaml,
            "fake_a": fake_a, "fake_b": fake_b, "judge": judge,
            "scenarios": scenarios, "models": models}


def test_phase0_walking_skeleton(happy_run):
    run_dir = happy_run["run_dir"]
    files = RunFiles(run_dir)

    # six outputs on disk, browsable per scenario
    for sid in ("img-001", "img-002", "img-003"):
        d = run_dir / "outputs" / "image" / sid
        assert (d / "model-a.png").exists() and (d / "model-b.png").exists()

    # six telemetry rows carrying task, cost, latency, sha256
    rows = files.read("telemetry")
    assert len(rows) == 6
    for r in rows:
        assert r["task"] == "text_to_image"
        assert r["status"] == "ok"
        assert r["cost"]["micro_usd"] > 0 and isinstance(r["cost"]["micro_usd"], int)
        assert r["output"]["sha256"] and r["output"]["width"] == 1024
        assert r["latency_ms"] >= 0
        assert r["provider_version"]

    # per_image cost is api_reported; token model without usage is estimated + labelled
    by_model = {}
    for r in rows:
        by_model.setdefault(r["model_id"], []).append(r)
    assert all(r["cost"]["usage_source"] == "api_reported" for r in by_model["model-a"])
    assert all(r["cost"]["usage_source"] == "estimated" for r in by_model["model-b"])
    total = sum(r["cost"]["micro_usd"] for r in rows)
    assert total == 3 * 67000 + 3 * 70000

    # frozen scenarios + manifest with cell states and effective weights
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert len(list((run_dir / "scenarios").glob("*.yaml"))) == 3
    assert manifest["state"] == "generated" or manifest["state"] == "planned"
    assert all(c["state"] == "measured" for c in manifest["cells"].values())
    w2 = manifest["effective_weights"]["img-002"]
    assert "text_accuracy" not in w2 and abs(sum(w2.values()) - 1.0) < 1e-6

    # deterministic checks ran and recorded OCR as a measured fact
    checks = files.read("checks")
    assert len(checks) == 6
    a1 = next(c for c in checks if c["model_id"] == "model-a"
              and c["scenario_id"] == "img-001")
    assert a1["passed"] and a1["measures"]["ocr_match"] == 1.0


def test_resume_never_pays_twice(happy_run):
    run_dir = happy_run["run_dir"]
    files = RunFiles(run_dir)
    calls_before = happy_run["fake_a"].calls + happy_run["fake_b"].calls
    rows_before = len(files.read("telemetry"))

    run_generation(happy_run["project"], happy_run["scenarios"],
                   happy_run["models"], "image", budget_usd=5.0,
                   run_id=run_dir.name)
    assert happy_run["fake_a"].calls + happy_run["fake_b"].calls == calls_before
    assert len(files.read("telemetry")) == rows_before


def test_phase1_blind_judging_and_scoring(happy_run):
    project, run_dir = happy_run["project"], happy_run["run_dir"]
    counts = judge_run(project, run_dir, happy_run["models_yaml"])
    assert counts == {"judged": 6, "unjudged": 0, "skipped_existing": 0}

    files = RunFiles(run_dir)
    jrows = files.read("judge")
    assert len(jrows) == 6
    for r in jrows:
        assert r["status"] == "judged"
        assert r["blind_label"] in ("A", "B")
        assert r["rubric_hash"] and r["prompt_sha256"]
        assert r["judge"]["temperature"] == 0
        assert r["cost"]["micro_usd"] > 0
        # reasoning present for every judge-scored criterion
        for name, c in r["criteria"].items():
            assert c["reasoning"]

    # the judge prompt never contained a model or provider name
    for call in happy_run["judge"].calls:
        low = call["prompt"].lower()
        for forbidden in ("model-a", "model-b", "prov_a", "prov_b",
                          "prov-a-img-1", "prov-b-img-1"):
            assert forbidden not in low
    # measured facts were injected
    assert any("fuzzy match" in c["prompt"] for c in happy_run["judge"].calls)

    sc = score_run(project, run_dir)
    assert sc["scored"] == 6 and sc["invalid"] == 0 and sc["unjudged"] == 0
    srows = files.read("scores")
    assert len(srows) == 6
    a1 = next(r for r in srows if r["model_id"] == "model-a"
              and r["scenario_id"] == "img-001")
    b1 = next(r for r in srows if r["model_id"] == "model-b"
              and r["scenario_id"] == "img-001")
    # judge criteria all 8.0; text_accuracy measured: a=1.0 -> 10, b typo -> lower
    assert a1["criteria"]["text_accuracy"]["source"] == "measured"
    assert a1["criteria"]["text_accuracy"]["score"] == 10.0
    assert b1["criteria"]["text_accuracy"]["score"] < 10.0
    assert a1["score"] > b1["score"]
    assert a1["rubric_hash"] == b1["rubric_hash"]
    # weighted total is scoring.py's arithmetic, not the judge's
    expected = sum(a1["weights"][n] * a1["criteria"][n]["score"] for n in a1["weights"])
    assert abs(a1["score"] - expected) < 1e-6

    agg = aggregate(run_dir)
    t = agg["tasks"]["text_to_image"]
    assert t["models"]["model-a"]["judged_n"] == 3
    assert len(t["pairs"]) == 1

    report = build_report(project, run_dir)
    html = report.read_text()
    for needle in ("model-a", "model-b", "W–T–L", "Judged", "Latency p50 / max",
                   "Success", "img-001", "Rubric hashes"):
        assert needle in html


def test_rate_limit_retry_visible_in_telemetry(project, fake_models_yaml,
                                               fake_env, monkeypatch):
    fake_a = FakeImageAdapter(model_tag="a", script=[RateLimited("429", retry_after=0.01)])
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    install_fake_ocr(monkeypatch, _ocr_by_model)
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "image", budget_usd=5.0)

    rows = [r for r in RunFiles(run_dir).read("telemetry") if r["model_id"] == "model-a"]
    assert [r["status"] for r in rows] == ["rate_limited", "ok"]
    assert [r["attempt"] for r in rows] == [1, 2]


def test_refusal_is_terminal_and_never_retried(project, fake_models_yaml,
                                               fake_env, monkeypatch):
    fake_a = FakeImageAdapter(model_tag="a", script=[
        SafetyRefusal("blocked"), SafetyRefusal("should never be reached")])
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    install_fake_ocr(monkeypatch, _ocr_by_model)
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "image", budget_usd=5.0)

    assert fake_a.calls == 1  # no retry on refusal
    rows = [r for r in RunFiles(run_dir).read("telemetry") if r["model_id"] == "model-a"]
    assert [r["status"] for r in rows] == ["refused"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["cells"]["img-001::model-a"]["state"] == "failed"
    assert "refused" in manifest["cells"]["img-001::model-a"]["reason"]


def test_provider_error_exhausts_three_attempts(project, fake_models_yaml,
                                                fake_env, monkeypatch):
    fake_a = FakeImageAdapter(model_tag="a", script=[
        ProviderError("boom", retryable=True)] * 5)
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    install_fake_ocr(monkeypatch, _ocr_by_model)
    monkeypatch.setattr("runner.generate.backoff_s", lambda a: 0.0)
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "image", budget_usd=5.0)

    rows = [r for r in RunFiles(run_dir).read("telemetry") if r["model_id"] == "model-a"]
    assert len(rows) == 3  # one row per attempt, retries visible


def test_invalid_output_regenerated_once_then_earned_zero(
        project, fake_models_yaml, fake_env, monkeypatch):
    fake_a = FakeImageAdapter(model_tag="a",
                              script=[blank_png(), blank_png(), blank_png()],
                              default_bytes=blank_png())
    fake_b = FakeImageAdapter(model_tag="b")
    judge = FakeJudgeAdapter()
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    install_fake_ocr(monkeypatch, _ocr_by_model)
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "image", budget_usd=5.0)

    assert fake_a.calls == 2  # one regeneration attempt, then stop
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["cells"]["img-001::model-a"]["state"] == "invalid"
    # the failed artefact is kept as evidence
    assert (run_dir / "outputs" / "image" / "img-001" / "model-a.invalid-1.png").exists()

    judge_run(project, run_dir, fake_models_yaml)
    # the judge was never called for the invalid cell
    assert all(r["model_id"] != "model-a"
               for r in RunFiles(run_dir).read("judge"))
    score_run(project, run_dir)
    srows = RunFiles(run_dir).read("scores")
    a = next(r for r in srows if r["model_id"] == "model-a")
    assert a["status"] == "invalid" and a["score"] == 0.0  # the one earned zero


def test_judge_failure_is_unjudged_never_zero(project, fake_models_yaml,
                                              fake_env, monkeypatch):
    fake_a = FakeImageAdapter(model_tag="a")
    fake_b = FakeImageAdapter(model_tag="b")
    judge = FakeJudgeAdapter(handler=lambda p, m: "THIS IS NOT JSON")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    install_fake_ocr(monkeypatch, _ocr_by_model)
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "image", budget_usd=5.0)
    counts = judge_run(project, run_dir, fake_models_yaml)

    assert counts["judged"] == 0 and counts["unjudged"] == 2
    jrows = RunFiles(run_dir).read("judge")
    assert all(r["status"] == "unjudged" for r in jrows)
    assert all(r["raw_response"] == "THIS IS NOT JSON" for r in jrows)  # kept
    # one repair retry per output: 2 calls each
    assert len(judge.calls) == 4

    score_run(project, run_dir)
    srows = RunFiles(run_dir).read("scores")
    assert all(r["status"] == "unjudged" and r["score"] is None for r in srows)
    agg = aggregate(run_dir)
    m = agg["tasks"]["text_to_image"]["models"]["model-a"]
    assert m["mean"] is None and m["unjudged"] == 1  # excluded, not a 0


def test_missing_key_hard_stop_before_spend(project, fake_models_yaml, monkeypatch):
    monkeypatch.setenv("FAKE_KEY_A", "test-key")
    monkeypatch.delenv("FAKE_KEY_B", raising=False)
    fake_a = FakeImageAdapter(model_tag="a")
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    with pytest.raises(RunRejected, match="FAKE_KEY_B"):
        run_generation(project, scenarios, models, "image", budget_usd=5.0)
    assert fake_a.calls == 0 and fake_b.calls == 0  # nothing was spent


def test_budget_preflight_refuses(project, fake_models_yaml, fake_env, monkeypatch):
    fake_a = FakeImageAdapter(model_tag="a")
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    # 6 cells x ~$0.07 ~ $0.41 estimated; a $0.10 budget must refuse up front
    with pytest.raises(RunRejected, match="exceeds"):
        run_generation(project, scenarios, models, "image", budget_usd=0.10)
    assert fake_a.calls == 0 and fake_b.calls == 0


def test_rubric_edit_rejects_rejudge(happy_run):
    rubric_path = happy_run["project"] / "configs" / "rubrics" / "image.yaml"
    text = rubric_path.read_text().replace("weight: 0.35", "weight: 0.30", 1)
    rubric_path.write_text(text.replace("weight: 0.20", "weight: 0.25", 1))
    with pytest.raises(RunRejected, match="NEW run"):
        judge_run(happy_run["project"], happy_run["run_dir"],
                  happy_run["models_yaml"])
