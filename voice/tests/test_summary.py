"""
The per-run summary.

It is the file a dashboard reads, so the rules it must not break are the same
ones the report must not break: unjudged is excluded rather than zeroed,
invalid is an earned zero, absent measurements stay absent, and a scenario
answered by only some of the models is not a result.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.summary import build_summary, write_summary
from runner.telemetry import RunPaths


def _run(root: Path, run_id: str, cells: list[dict], incomplete: list[dict] | None = None) -> RunPaths:
    d = root / run_id
    d.mkdir(parents=True)
    models = sorted({c["model"] for c in cells})
    (d / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "modality": "voice", "started_at": "2026-09-01T10:00:00+0530",
        "git_sha": "abc1234", "scenario_set_hash": "hash",
        "models": [{"id": m, "provider": "p", "provider_model": m, "adapter": "a",
                    "voice_map": {"female_mid_warm": "v"}, "price": {"unit": "per_1k_chars"}}
                   for m in models],
        "judge": {"provider_model": "test-judge"}, "mos": {"predictor": "signal"},
        "calibration": {"passed": False, "reason": "not run"},
    }), encoding="utf-8")

    # build_scores RE-COMPUTES from checks + judge records rather than reading
    # scores.jsonl, so a fixture without judge rows produces `unjudged` cells.
    with (d / "checks.jsonl").open("w") as ch, (d / "scores.jsonl").open("w") as sc, \
         (d / "telemetry.jsonl").open("w") as tl, (d / "judge.jsonl").open("w") as jd:
        for c in cells:
            key = {"scenario_id": c.get("scenario", "s1"), "model_id": c["model"]}
            ch.write(json.dumps({**key, "passed": c.get("passed", True),
                                 "failed_gates": c.get("failed_gates", []),
                                 "measurements": {"normalized_wer": c.get("wer", 0.0),
                                                  "duration_s": c.get("dur", 60.0),
                                                  "audio_quality_1_5": c.get("q", 4.0)}}) + "\n")
            sc.write(json.dumps({**key, "task": "text_to_speech", "status": c["status"],
                                 "score": c["score"],
                                 "criterion_scores": c.get("crit", {"text_accuracy": 10.0}),
                                 "calibration_trusted": False}) + "\n")
            if c["status"] == "scored":
                jd.write(json.dumps({**key, "status": "judged", "blind_label": "A",
                                     "judge_model": "test-judge", "rubric_hash": "h",
                                     "prompt_sha256": "h",
                                     "scores": c.get("judge", {"pronunciation": 10.0,
                                                               "naturalness": 10.0,
                                                               "clarity": 10.0,
                                                               "audio_quality": 10.0}),
                                     "reasoning": {}}) + "\n")
            if c.get("gen", True):
                tl.write(json.dumps({**key, "attempt": 1, "status": "ok",
                                     "latency_ms": c.get("lat", 10000),
                                     "cost": {"micro_usd": c.get("cost", 50000),
                                              "usage_exact": c.get("exact", True)}}) + "\n")
            else:
                tl.write(json.dumps({**key, "attempt": 1, "status": "provider_error",
                                     "error": "boom"}) + "\n")
        for inc in incomplete or []:
            tl.write(json.dumps({"scenario_id": inc["scenario_id"], "step": "scenario",
                                 "status": "incomplete", "models_done": inc["done"],
                                 "models_expected": inc["expected"],
                                 "missing": inc["missing"]}) + "\n")
    return RunPaths(root, run_id)


@pytest.fixture
def paths(tmp_path):
    return _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "status": "scored", "score": 9.0, "lat": 8000, "cost": 90000, "wer": 0.01},
        {"model": "beta", "status": "scored", "score": 8.0, "lat": 40000, "cost": 30000, "wer": 0.02},
    ])


def test_reports_latency_avg_min_max(paths):
    s = build_summary(paths)
    a = next(m for m in s["models"] if m["model_id"] == "alpha")
    assert a["latency_ms"] == {"n": 1, "avg": 8000.0, "min": 8000.0, "max": 8000.0, "spread": 0.0}


def test_reports_a_rating_summary_per_model(paths):
    s = build_summary(paths)
    a = next(m for m in s["models"] if m["model_id"] == "alpha")
    assert a["rating"]["mean"] == 9.0
    assert a["rating"]["scored"] == 1 and a["rating"]["attempted"] == 1
    assert a["rating"]["calibration_trusted"] is False


def test_carries_cost_split_and_whether_it_is_exact(paths):
    s = build_summary(paths)
    a = next(m for m in s["models"] if m["model_id"] == "alpha")
    assert a["cost_micro_usd"]["generation"] == 90000
    assert a["cost_micro_usd"]["all_exact"] is True
    assert s["cost_micro_usd_total"] == 120000


def test_estimated_cost_is_flagged_so_it_cannot_read_as_measured(tmp_path):
    p = _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "status": "scored", "score": 9.0, "exact": False},
    ])
    a = build_summary(p)["models"][0]
    assert a["cost_micro_usd"]["all_exact"] is False


def test_unjudged_is_excluded_from_the_mean_not_zeroed(tmp_path):
    p = _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "scenario": "s1", "status": "scored", "score": 8.0},
        {"model": "alpha", "scenario": "s2", "status": "unjudged", "score": None},
    ])
    r = build_summary(p)["models"][0]["rating"]
    assert r["mean"] == 8.0          # not 4.0
    assert r["unjudged"] == 1 and r["scored"] == 1 and r["attempted"] == 2


def test_invalid_counts_as_an_earned_zero(tmp_path):
    p = _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "scenario": "s1", "status": "scored", "score": 8.0},
        {"model": "alpha", "scenario": "s2", "status": "invalid", "score": 0.0,
         "passed": False, "failed_gates": ["wer_within_max"]},
    ])
    m = build_summary(p)["models"][0]
    assert m["rating"]["mean"] == 4.0        # (8 + 0) / 2 — the zero is real
    assert m["rating"]["invalid"] == 1
    assert m["reliability"]["gate_failures"] == {"wer_within_max": 1}


def test_absent_measurements_are_null_never_zero(tmp_path):
    p = _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "status": "unjudged", "score": None, "gen": False},
    ])
    m = build_summary(p)["models"][0]
    assert m["rating"]["mean"] is None
    assert m["latency_ms"] is None
    assert m["reliability"]["cells_ok"] == 0


def test_an_incomplete_scenario_is_named_not_averaged_over(tmp_path):
    """
    One model finished, the other did not. The scenario must be reported as
    incomplete so a dashboard can exclude it — a half-answered scenario is
    not comparable and must not read as a result for the arm that finished.
    """
    p = _run(tmp_path, "2026-09-01_100000_voice-r1",
             [{"model": "alpha", "status": "scored", "score": 9.0}],
             incomplete=[{"scenario_id": "s1", "done": 1, "expected": 2, "missing": ["beta"]}])
    s = build_summary(p)
    assert s["counts"]["scenarios_incomplete"] == 1
    assert s["counts"]["scenarios_complete"] == 0
    assert s["incomplete_scenarios"][0]["missing"] == ["beta"]


def test_generation_failures_are_listed_with_their_reason(tmp_path):
    p = _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "status": "unjudged", "score": None, "gen": False},
    ])
    f = build_summary(p)["failures"]
    assert len(f) == 1 and f[0]["status"] == "provider_error" and "boom" in f[0]["error"]


def test_per_criterion_averages_show_where_a_model_wins(tmp_path):
    p = _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "scenario": "s1", "status": "scored", "score": 9.0,
         "crit": {"text_accuracy": 10.0, "naturalness": 8.0}},
        {"model": "alpha", "scenario": "s2", "status": "scored", "score": 9.0,
         "crit": {"text_accuracy": 10.0, "naturalness": 6.0}},
    ])
    avg = build_summary(p)["models"][0]["rating"]["per_criterion_avg"]
    assert avg == {"naturalness": 7.0, "text_accuracy": 10.0}


def test_summary_is_derived_and_rebuildable(paths):
    out = write_summary(paths)
    assert out.name == "summary.json"
    first = out.read_text()
    out.unlink()
    assert write_summary(paths).read_text() == first


def test_provenance_travels_with_the_numbers(paths):
    s = build_summary(paths)
    # A number without its judge, rubric and calibration state is not
    # quotable, so the summary carries them beside the models.
    for k in ("git_sha", "scenario_set_hash", "judge", "calibration", "mos_predictor"):
        assert k in s


# --------------------------------------------------------------------------
# The exclusion, end to end. Detecting an incomplete scenario is not enough -
# every consumer of the numbers has to act on it, or the arm that finished
# gets credited for a comparison that never happened.
# --------------------------------------------------------------------------

def test_incomplete_scenarios_are_readable_from_telemetry(tmp_path):
    from runner.telemetry import incomplete_scenarios

    p = _run(tmp_path, "2026-09-01_100000_voice-r1",
             [{"model": "alpha", "status": "scored", "score": 9.0}],
             incomplete=[{"scenario_id": "s1", "done": 1, "expected": 2, "missing": ["beta"]}])
    assert incomplete_scenarios(p) == {"s1"}


def test_scoring_refuses_to_credit_the_arm_that_finished(tmp_path):
    """
    THE point of the requirement. alpha finished s1 and beta did not, so s1
    must contribute NOTHING to alpha's mean - not its score, and not a zero.
    s1 and s2 are given different judge scores so the two are distinguishable.
    """
    from runner.report import build_scores
    from runner.rubrics import load_rubric
    from runner.scoring import summarise

    root = tmp_path / "runs"; root.mkdir()
    high = {"pronunciation": 10.0, "naturalness": 10.0, "clarity": 10.0, "audio_quality": 10.0}
    low = {"pronunciation": 4.0, "naturalness": 4.0, "clarity": 4.0, "audio_quality": 4.0}
    p = _run(root, "2026-09-01_100000_voice-r1",
             [{"model": "alpha", "scenario": "s1", "status": "scored", "score": 9.0, "judge": high},
              {"model": "alpha", "scenario": "s2", "status": "scored", "score": 7.0, "judge": low}],
             incomplete=[{"scenario_id": "s1", "done": 1, "expected": 2, "missing": ["beta"]}])

    class S:
        def __init__(self, i): self.id, self.task = i, "text_to_speech"
    root_dir = Path(__file__).resolve().parent.parent
    rubric = load_rubric(root_dir / "configs", "voice", "text_to_speech")
    cells, _, _ = build_scores(p, [S("s1"), S("s2")], {"text_to_speech": rubric}, root_dir)

    s1 = next(c for c in cells if c.scenario_id == "s1")
    s2 = next(c for c in cells if c.scenario_id == "s2")
    assert s1.status == "incomplete" and s1.score is None
    assert s2.status == "scored" and s2.score is not None

    summary = summarise(cells, "alpha", "text_to_speech")
    # The mean is s2's score ALONE - s1 neither raises it nor zeroes it.
    assert summary.mean == pytest.approx(s2.score)
    assert summary.judged == 1 and summary.attempted == 2


def test_the_dashboard_excludes_it_too(tmp_path):
    from runner.dashboard import load_runs, rollup_models

    root = tmp_path / "runs"; root.mkdir()
    _run(root, "2026-09-01_100000_voice-r1",
         [{"model": "alpha", "scenario": "s1", "status": "scored", "score": 9.0},
          {"model": "alpha", "scenario": "s2", "status": "scored", "score": 7.0}],
         incomplete=[{"scenario_id": "s1", "done": 1, "expected": 2, "missing": ["beta"]}])
    m = rollup_models(load_runs(root, "voice"))[0]
    assert m.mean_score == pytest.approx(7.0)
    assert all(c.score is None for c in
               [x for r in load_runs(root, "voice") for x in r.cells if x.scenario_id == "s1"])


def test_a_complete_run_is_untouched_by_any_of_this(tmp_path):
    """The exclusion must not fire when every model answered."""
    from runner.dashboard import load_runs, rollup_models

    root = tmp_path / "runs"; root.mkdir()
    _run(root, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "scenario": "s1", "status": "scored", "score": 9.0},
        {"model": "beta", "scenario": "s1", "status": "scored", "score": 8.0},
    ])
    models = {m.model_id: m for m in rollup_models(load_runs(root, "voice"))}
    assert models["alpha"].mean_score == pytest.approx(9.0)
    assert models["beta"].mean_score == pytest.approx(8.0)


# --------------------------------------------------------------------------
# One scenario, one run. A run is the evidence for ONE question, so the
# scenario is both the unit of completion and the unit of filing.
# --------------------------------------------------------------------------

def test_per_scenario_mode_mints_one_run_per_scenario(tmp_path, monkeypatch, capsys):
    import argparse
    from runner import cli

    sc = tmp_path / "scenarios"; sc.mkdir()
    for sid in ("voi-a", "voi-b", "voi-c"):
        (sc / f"{sid}.yaml").write_text(
            f"id: {sid}\nmodality: voice\ntask: text_to_speech\n"
            f"input: {{script: hello there}}\nexpected: ok\n", encoding="utf-8")

    seen: list[tuple[str, str]] = []

    def fake_single(args):
        # Each pass must be pointed at exactly ONE scenario file.
        loaded = cli.load_scenarios(Path(args.scenarios), "voice")
        assert len(loaded) == 1, f"a pass saw {len(loaded)} scenarios, expected 1"
        seen.append((loaded[0].id, args.label))
        args.minted_run_id = f"run-{loaded[0].id}"
        return 0

    real = cli.cmd_run
    monkeypatch.setattr(cli, "cmd_run",
                        lambda a: fake_single(a) if getattr(a, "_single", False) else real(a))

    args = argparse.Namespace(configs="configs", scenarios=str(sc), runs=str(tmp_path / "runs"),
                              modality="voice", label=None, bundle=False, run=None, yes=True)
    rc = cli._run_per_scenario(args)

    assert rc == 0
    assert [s for s, _ in seen] == ["voi-a", "voi-b", "voi-c"]
    assert args.minted_run_ids == ["run-voi-a", "run-voi-b", "run-voi-c"]


def test_each_pass_is_labelled_with_its_own_scenario(tmp_path, monkeypatch):
    """Run ids must be distinguishable, or three runs land in one folder name."""
    import argparse
    from runner import cli

    sc = tmp_path / "scenarios"; sc.mkdir()
    for sid in ("voi-a", "voi-b"):
        (sc / f"{sid}.yaml").write_text(
            f"id: {sid}\nmodality: voice\ntask: text_to_speech\n"
            f"input: {{script: hello there}}\nexpected: ok\n", encoding="utf-8")

    labels: list[str] = []
    real = cli.cmd_run
    monkeypatch.setattr(cli, "cmd_run", lambda a: (labels.append(a.label), 0)[1]
                        if getattr(a, "_single", False) else real(a))

    args = argparse.Namespace(configs="configs", scenarios=str(sc), runs=str(tmp_path / "runs"),
                              modality="voice", label="demo", bundle=False, run=None, yes=True)
    cli._run_per_scenario(args)
    assert labels == ["demo-voi-a", "demo-voi-b"]
    assert len(set(labels)) == len(labels)


def test_bundle_flag_keeps_every_scenario_in_one_run(tmp_path, monkeypatch):
    """--bundle must NOT take the per-scenario path."""
    import argparse
    from runner import cli

    called = {"per_scenario": False}
    monkeypatch.setattr(cli, "_run_per_scenario",
                        lambda a: (called.__setitem__("per_scenario", True), 0)[1])
    # A bundled call falls through to the real body, which will fail on the
    # missing config - what matters is that it did not fan out.
    args = argparse.Namespace(configs=str(tmp_path / "nope"), scenarios=str(tmp_path),
                              runs=str(tmp_path), modality="voice", bundle=True, run=None)
    try:
        cli.cmd_run(args)
    except Exception:
        pass
    assert called["per_scenario"] is False
