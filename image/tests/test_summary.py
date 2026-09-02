"""Telemetry summary + scenario-completion semantics.

The core rule under test: a scenario is completed ONLY when every required
model scored. One model failing while the other succeeds must never count
as completed."""
import pytest

from runner.generate import run_generation
from runner.judge import judge_run
from runner.loaders import enabled_models, load_models, load_scenarios
from runner.scoring import score_run
from runner.summary import (completion_counts, scenario_completion,
                            summarize_run, summarize_runs, write_csv)
from runner.adapters.base import SafetyRefusal
from tests.conftest import (FakeImageAdapter, FakeJudgeAdapter, gradient_png,
                            install_adapters, install_fake_ocr)


def _ocr(path):
    return ["TRAILHEAD 750", "FRESH FILTER COFFEE"]


def test_completion_pure_logic():
    manifest = {"cells": {
        "s1::a": {"scenario_id": "s1", "model_id": "a", "state": "scored", "task": "t"},
        "s1::b": {"scenario_id": "s1", "model_id": "b", "state": "scored", "task": "t"},
        "s2::a": {"scenario_id": "s2", "model_id": "a", "state": "scored", "task": "t"},
        "s2::b": {"scenario_id": "s2", "model_id": "b", "state": "failed", "task": "t"},
        "s3::a": {"scenario_id": "s3", "model_id": "a", "state": "measured", "task": "t"},
        "s3::b": {"scenario_id": "s3", "model_id": "b", "state": "measured", "task": "t"},
        "s4::a": {"scenario_id": "s4", "model_id": "a", "state": "scored", "task": "t"},
        "s4::b": {"scenario_id": "s4", "model_id": "b", "state": "skipped", "task": "t"},
        "s5::a": {"scenario_id": "s5", "model_id": "a", "state": "planned", "task": "t"},
        "s5::b": {"scenario_id": "s5", "model_id": "b", "state": "generated", "task": "t"},
    }}
    comp = scenario_completion(manifest)
    assert comp["s1"]["status"] == "completed"
    # one model succeeded, the other failed -> NOT completed, ever
    assert comp["s2"]["status"] == "incomplete"
    assert comp["s3"]["status"] == "awaiting_judgement"
    # a model that doesn't support the task is not required
    assert comp["s4"]["status"] == "completed"
    assert comp["s5"]["status"] == "in_progress"
    counts = completion_counts(manifest)
    assert counts == {"total": 5, "completed": 2, "incomplete": 1,
                      "awaiting_judgement": 1, "in_progress": 1}


class RefusesOneScenario(FakeImageAdapter):
    """Refuses exactly the scenario whose prompt carries the marker —
    deterministic regardless of thread scheduling order."""

    def run(self, req):
        if "TRAILHEAD" in req.text:          # img-001's prompt
            raise SafetyRefusal("blocked")
        return super().run(req)


@pytest.fixture
def mixed_run(project, fake_models_yaml, fake_env, monkeypatch):
    """model-a succeeds everywhere; model-b is refused on img-001."""
    fake_a = FakeImageAdapter(model_tag="a", default_bytes=gradient_png(phase=0))
    fake_b = RefusesOneScenario(model_tag="b", default_bytes=gradient_png(phase=3))
    judge = FakeJudgeAdapter(score=8.0)
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    install_fake_ocr(monkeypatch, _ocr)
    scenarios = load_scenarios(project / "scenarios", modality="image")
    models = enabled_models(load_models(fake_models_yaml), "image")
    run_dir = run_generation(project, scenarios, models, "image", budget_usd=5.0)
    judge_run(project, run_dir, fake_models_yaml)
    score_run(project, run_dir)
    return project, run_dir


def test_partial_scenario_never_completed(mixed_run):
    project, run_dir = mixed_run
    import json
    manifest = json.loads((run_dir / "manifest.json").read_text())

    # persisted in the manifest at every stage
    status = manifest["scenario_status"]
    # img-001: model-a scored, model-b refused -> incomplete (the user's exact case)
    assert status["img-001"] == "incomplete"
    assert status["img-002"] == "completed"
    assert status["img-003"] == "completed"


def test_summarize_run_metrics(mixed_run):
    project, run_dir = mixed_run
    s = summarize_run(run_dir)

    assert s["completion"] == {"total": 3, "completed": 2, "incomplete": 1}
    a, b = s["models"]["model-a"], s["models"]["model-b"]
    assert a["rating"]["n"] == 3 and 0 <= a["rating"]["mean"] <= 10
    assert a["success_rate"] == 1.0
    assert b["success_rate"] < 1.0
    assert b["statuses"].get("refused") == 1
    assert list(b["errors"])  # the refusal message is captured
    assert a["latency"]["min_ms"] <= a["latency"]["avg_ms"] <= a["latency"]["max_ms"]
    assert a["gen_cost_usd"] > 0
    assert s["judge_cost_usd"] > 0
    # per-cell rows exist for every non-skipped cell
    assert len(s["cells"]) == 6


def test_csv_export(mixed_run, tmp_path):
    project, run_dir = mixed_run
    summaries = summarize_runs(run_dir.parent)
    out = write_csv(summaries, tmp_path / "summary.csv")
    text = out.read_text()
    assert "img-001" in text and "incomplete" in text
    assert "model-b" in text and "refused" in text.lower() or "blocked" in text
