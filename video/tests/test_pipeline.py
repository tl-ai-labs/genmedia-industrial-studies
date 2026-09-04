"""End-to-end offline: run -> checks -> judge -> score -> report, through the
same registry seam a real provider uses. No network, no keys, no spend."""
import hashlib
import json

import pytest

from runner.adapters.base import ProviderError, RateLimited, SafetyRefusal
from runner.generate import RunRejected, run_generation
from runner.judge import judge_run
from runner.loaders import enabled_models, load_models, load_scenarios
from runner.report import build_report
from runner.scoring import aggregate, score_run
from runner.telemetry import RunFiles
from tests.conftest import (FakeJudgeAdapter, FakeVideoAdapter, broken_mp4,
                            install_adapters, minimal_mp4)

SCENARIO_IDS = ("vid-001-dolly-in", "vid-002-liquid-pour", "vid-003-bouncing-ball")


def _load(project, models_yaml):
    scenarios = load_scenarios(project / "scenarios", modality="video")
    models = enabled_models(load_models(models_yaml), "video")
    return scenarios, models


def _fakes():
    """model-a: Veo posture (requested seconds -> estimated cost);
    model-b: Sora posture (echoed seconds -> api_reported cost)."""
    fake_a = FakeVideoAdapter(model_tag="a",
                              default_bytes=minimal_mp4(duration_s=4.0,
                                                        width=1280, height=720),
                              usage={"seconds": 4, "seconds_source": "requested"})
    fake_b = FakeVideoAdapter(model_tag="b",
                              default_bytes=minimal_mp4(duration_s=4.0,
                                                        width=1280, height=720,
                                                        payload_bytes=4096),
                              usage={"seconds": 4.0})
    return fake_a, fake_b


@pytest.fixture
def happy_run(project, fake_models_yaml, fake_env, monkeypatch):
    fake_a, fake_b = _fakes()
    judge = FakeJudgeAdapter(score=8.0)
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios, models, "video",
                             budget_usd=20.0, workers=2)
    return {"project": project, "run_dir": run_dir, "models_yaml": fake_models_yaml,
            "fake_a": fake_a, "fake_b": fake_b, "judge": judge,
            "scenarios": scenarios, "models": models}


def test_phase0_walking_skeleton(happy_run):
    run_dir = happy_run["run_dir"]
    files = RunFiles(run_dir)

    # six clips on disk, browsable per scenario
    for sid in SCENARIO_IDS:
        d = run_dir / "outputs" / "video" / sid
        assert (d / "model-a.mp4").exists() and (d / "model-b.mp4").exists()

    # six telemetry rows carrying task, cost, latency, sha256
    rows = files.read("telemetry")
    assert len(rows) == 6
    for r in rows:
        assert r["task"] == "text_to_video"
        assert r["status"] == "ok"
        assert r["cost"]["micro_usd"] > 0 and isinstance(r["cost"]["micro_usd"], int)
        assert r["output"]["sha256"]
        assert r["latency_ms"] >= 0
        assert r["provider_version"]

    # per_second cost: requested seconds are labelled estimated (Veo posture),
    # echoed seconds are api_reported (Sora posture) — the label travels
    by_model = {}
    for r in rows:
        by_model.setdefault(r["model_id"], []).append(r)
    assert all(r["cost"]["usage_source"] == "estimated" for r in by_model["model-a"])
    assert all(r["cost"]["usage_source"] == "api_reported" for r in by_model["model-b"])
    total = sum(r["cost"]["micro_usd"] for r in rows)
    assert total == 3 * 1_600_000 + 3 * 400_000   # 4s x $0.40 | 4s x $0.10

    # frozen scenarios + manifest with cell states and effective weights
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert len(list((run_dir / "scenarios").glob("*.yaml"))) == 3
    assert all(c["state"] == "measured" for c in manifest["cells"].values())
    for sid in SCENARIO_IDS:
        w = manifest["effective_weights"][sid]
        assert "technical_compliance" in w
        assert abs(sum(w.values()) - 1.0) < 1e-6

    # deterministic checks ran off the real container headers
    checks = files.read("checks")
    assert len(checks) == 6
    for c in checks:
        assert c["passed"]
        assert c["measures"]["duration_s"] == pytest.approx(4.0)
        assert c["measures"]["width"] == 1280


def test_resume_never_pays_twice(happy_run):
    run_dir = happy_run["run_dir"]
    files = RunFiles(run_dir)
    calls_before = happy_run["fake_a"].calls + happy_run["fake_b"].calls
    rows_before = len(files.read("telemetry"))

    run_generation(happy_run["project"], happy_run["scenarios"],
                   happy_run["models"], "video", budget_usd=20.0,
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
        # the caveat is recorded, never silent: video media is not re-encoded
        assert r["media"]["mime"] == "video/mp4"
        assert r["media"]["metadata_stripped"] is False
        assert "not stripped" in r["media"]["note"]
        for name, c in r["criteria"].items():
            assert c["reasoning"]

    # judge saw the mp4 UNMODIFIED: same bytes as the file on disk
    disk_sha = {f"model-{t}": hashlib.sha256(
        (run_dir / "outputs" / "video" / SCENARIO_IDS[0] / f"model-{t}.mp4"
         ).read_bytes()).hexdigest() for t in "ab"}
    judged_shas = {hashlib.sha256(c["media_bytes"][0]).hexdigest()
                   for c in happy_run["judge"].calls}
    assert set(disk_sha.values()) <= judged_shas
    for call in happy_run["judge"].calls:
        assert call["mimes"] == ["video/mp4"]
        assert "one generated video clip" in call["prompt"]
        assert "clip duration" in call["prompt"]          # measured fact injected
        low = call["prompt"].lower()
        for forbidden in ("model-a", "model-b", "prov_a", "prov_b",
                          "prov-a-vid-1", "prov-b-vid-1"):
            assert forbidden not in low                   # blind stays blind

    sc = score_run(project, run_dir)
    assert sc["scored"] == 6 and sc["invalid"] == 0 and sc["unjudged"] == 0
    srows = files.read("scores")
    assert len(srows) == 6
    a1 = next(r for r in srows if r["model_id"] == "model-a"
              and r["scenario_id"] == SCENARIO_IDS[0])
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert all(c["state"] == "scored" for c in manifest["cells"].values())

    # technical_compliance is measured, in full spec -> 10; the weighted
    # total is scoring.py's arithmetic, not the judge's
    assert a1["criteria"]["technical_compliance"]["source"] == "measured"
    assert a1["criteria"]["technical_compliance"]["score"] == 10.0
    expected = sum(a1["weights"][n] * a1["criteria"][n]["score"] for n in a1["weights"])
    assert abs(a1["score"] - expected) < 1e-6

    agg = aggregate(run_dir)
    t = agg["tasks"]["text_to_video"]
    assert t["models"]["model-a"]["judged_n"] == 3
    assert len(t["pairs"]) == 1

    report = build_report(project, run_dir)
    html = report.read_text()
    for needle in ("model-a", "model-b", "W–T–L", "Judged", "Latency p50 / max",
                   "Success", SCENARIO_IDS[0], "Rubric hashes"):
        assert needle in html
    # clips render as playable inline video (small run -> data URIs)
    assert '<video class="out"' in html
    assert "data:video/mp4;base64," in html


def test_spec_shortfall_is_graded_not_invalid(project, fake_models_yaml,
                                              fake_env, monkeypatch, tmp_path):
    """A 720p delivery against a 1080p brief passes gates but loses
    technical_compliance points — capability gaps are graded facts."""
    import shutil
    import yaml
    bank = project / "scenarios-bank"
    bank.mkdir()
    shutil.copy2(REPO_BANK / "VID-CIN-01.yaml", bank / "VID-CIN-01.yaml")

    fake_a = FakeVideoAdapter(model_tag="a",
                              default_bytes=minimal_mp4(duration_s=8.0,
                                                        width=1920, height=1080),
                              usage={"seconds": 8, "seconds_source": "requested"})
    fake_b = FakeVideoAdapter(model_tag="b",
                              default_bytes=minimal_mp4(duration_s=8.0,
                                                        width=1280, height=720),
                              usage={"seconds": 8.0})
    judge = FakeJudgeAdapter(score=8.0)
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    scenarios = load_scenarios(bank, modality="video")
    models = enabled_models(load_models(fake_models_yaml), "video")
    run_dir = run_generation(project, scenarios, models, "video", budget_usd=20.0)
    judge_run(project, run_dir, fake_models_yaml)
    score_run(project, run_dir)

    srows = RunFiles(run_dir).read("scores")
    a = next(r for r in srows if r["model_id"] == "model-a")
    b = next(r for r in srows if r["model_id"] == "model-b")
    assert a["status"] == b["status"] == "scored"        # both valid
    assert a["criteria"]["technical_compliance"]["score"] == 10.0
    assert b["criteria"]["technical_compliance"]["score"] == pytest.approx(
        10.0 * 1280 / 1920, abs=0.01)
    assert a["score"] > b["score"]


REPO_BANK = __import__("pathlib").Path(__file__).resolve().parent.parent \
    / "scenarios" / "bank-video"


def test_rate_limit_retry_visible_in_telemetry(project, fake_models_yaml,
                                               fake_env, monkeypatch):
    fake_a = FakeVideoAdapter(model_tag="a",
                              script=[RateLimited("429", retry_after=0.01)])
    fake_b = FakeVideoAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "video", budget_usd=20.0)

    rows = [r for r in RunFiles(run_dir).read("telemetry") if r["model_id"] == "model-a"]
    assert [r["status"] for r in rows] == ["rate_limited", "ok"]
    assert [r["attempt"] for r in rows] == [1, 2]


def test_refusal_is_terminal_and_never_retried(project, fake_models_yaml,
                                               fake_env, monkeypatch):
    fake_a = FakeVideoAdapter(model_tag="a", script=[
        SafetyRefusal("blocked"), SafetyRefusal("should never be reached")])
    fake_b = FakeVideoAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "video", budget_usd=20.0)

    assert fake_a.calls == 1  # no retry on refusal
    rows = [r for r in RunFiles(run_dir).read("telemetry") if r["model_id"] == "model-a"]
    assert [r["status"] for r in rows] == ["refused"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    cell = manifest["cells"][f"{SCENARIO_IDS[0]}::model-a"]
    assert cell["state"] == "failed" and "refused" in cell["reason"]


def test_provider_error_exhausts_three_attempts(project, fake_models_yaml,
                                                fake_env, monkeypatch):
    fake_a = FakeVideoAdapter(model_tag="a", script=[
        ProviderError("boom", retryable=True)] * 5)
    fake_b = FakeVideoAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    monkeypatch.setattr("runner.generate.backoff_s", lambda a: 0.0)
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "video", budget_usd=20.0)

    rows = [r for r in RunFiles(run_dir).read("telemetry") if r["model_id"] == "model-a"]
    assert len(rows) == 3  # one row per attempt, retries visible


def test_invalid_output_regenerated_once_then_earned_zero(
        project, fake_models_yaml, fake_env, monkeypatch):
    fake_a = FakeVideoAdapter(model_tag="a",
                              script=[broken_mp4(), broken_mp4(), broken_mp4()],
                              default_bytes=broken_mp4())
    fake_b = FakeVideoAdapter(model_tag="b")
    judge = FakeJudgeAdapter()
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "video", budget_usd=20.0)

    assert fake_a.calls == 2  # one regeneration attempt, then stop
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["cells"][f"{SCENARIO_IDS[0]}::model-a"]["state"] == "invalid"
    # the failed artefact is kept as evidence
    assert (run_dir / "outputs" / "video" / SCENARIO_IDS[0]
            / "model-a.invalid-1.mp4").exists()

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
    fake_a = FakeVideoAdapter(model_tag="a")
    fake_b = FakeVideoAdapter(model_tag="b")
    judge = FakeJudgeAdapter(handler=lambda p, m: "THIS IS NOT JSON")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": judge})
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "video", budget_usd=20.0)
    counts = judge_run(project, run_dir, fake_models_yaml)

    assert counts["judged"] == 0 and counts["unjudged"] == 2
    jrows = RunFiles(run_dir).read("judge")
    assert all(r["status"] == "unjudged" for r in jrows)
    assert all(r["raw_response"] == "THIS IS NOT JSON" for r in jrows)  # kept
    assert len(judge.calls) == 4  # one repair retry per output

    score_run(project, run_dir)
    srows = RunFiles(run_dir).read("scores")
    assert all(r["status"] == "unjudged" and r["score"] is None for r in srows)
    agg = aggregate(run_dir)
    m = agg["tasks"]["text_to_video"]["models"]["model-a"]
    assert m["mean"] is None and m["unjudged"] == 1  # excluded, not a 0


def test_missing_key_hard_stop_before_spend(project, fake_models_yaml, monkeypatch):
    monkeypatch.setenv("FAKE_KEY_A", "test-key")
    monkeypatch.delenv("FAKE_KEY_B", raising=False)
    fake_a = FakeVideoAdapter(model_tag="a")
    fake_b = FakeVideoAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    with pytest.raises(RunRejected, match="FAKE_KEY_B"):
        run_generation(project, scenarios, models, "video", budget_usd=20.0)
    assert fake_a.calls == 0 and fake_b.calls == 0  # nothing was spent


def test_budget_preflight_refuses(project, fake_models_yaml, fake_env, monkeypatch):
    fake_a = FakeVideoAdapter(model_tag="a")
    fake_b = FakeVideoAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    # 6 cells: 3 x $1.60 + 3 x $0.40 = $6.00 estimated; a $1 budget must refuse
    with pytest.raises(RunRejected, match="exceeds"):
        run_generation(project, scenarios, models, "video", budget_usd=1.00)
    assert fake_a.calls == 0 and fake_b.calls == 0


def test_no_enabled_models_is_a_clean_rejection(project, monkeypatch):
    """With every video model disabled, the CLI's `run --modality video` path
    must reject cleanly, before any spend. The shipped config's enable flags
    are the human's choice and vary between copies, so the test forces every
    arm off itself — and belt-and-braces, it also removes ADC and keys so a
    regression here can never reach a real provider."""
    import re
    import runner.generate as gen
    from runner import cli
    monkeypatch.setattr(gen, "adc_available", lambda: False)
    for k in ("OPENAI_API_KEY", "ARK_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = project / "configs" / "models-all-off.yaml"
    cfg.write_text(re.sub(r"^(\s+enabled: )true", r"\1false",
                          (project / "configs" / "models.yaml").read_text(), flags=re.M))
    rc = cli.main(["run", "--modality", "video",
                   "--scenarios", str(project / "scenarios"),
                   "--models", str(cfg)])
    assert rc == 2
    assert not any((project / "runs").iterdir())  # no run folder was created


def test_rubric_edit_rejects_rejudge(happy_run):
    # any byte of the rubric stack changes the recorded hash — editing the
    # base file is enough even though the task override defines the criteria
    rubric_path = happy_run["project"] / "configs" / "rubrics" / "video.yaml"
    rubric_path.write_text(rubric_path.read_text()
                           .replace("# Base video rubric", "# Edited rubric", 1))
    with pytest.raises(RunRejected, match="NEW run"):
        judge_run(happy_run["project"], happy_run["run_dir"],
                  happy_run["models_yaml"])


def test_report_offers_a_filter_chip_per_winning_model(project, fake_models_yaml,
                                                       fake_env, monkeypatch):
    """Regression: the single-run report's evidence filter used to offer only
    'All results' and 'Ties' — the per-model 'X wins (n)' chips depended on a
    context key the template never received, so a reader could not filter
    the scenarios one model won (spotted on the 2026-09-03 pilot report).
    Here the blind judge scores by clip size, so model-b (bigger fake clip)
    wins every scenario and must get a chip with the right count."""
    fake_a, fake_b = _fakes()

    def by_size(prompt, media):
        schema = json.loads(prompt.strip().splitlines()[-1])
        score = 9.5 if len(media[0][0]) > 3000 else 6.0     # b's payload is 4096
        return json.dumps({"criteria": [{"name": c["name"], "score": score,
                                         "reasoning": "observed specifics"}
                                        for c in schema["criteria"]],
                           "overall_note": "sized"})

    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": FakeJudgeAdapter(handler=by_size)})
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios, models, "video",
                             budget_usd=20.0, workers=2)
    judge_run(project, run_dir, fake_models_yaml)
    score_run(project, run_dir)
    html = build_report(project, run_dir).read_text()

    assert html.count('data-win="model-b"') == 3          # every card: b won
    assert 'data-win="model-a"' not in html
    assert 'data-win="tie"' not in html
    # the filter offers exactly the winners that exist, with their counts
    assert 'data-dim="win" data-val="model-b">model-b wins <span class="n">(3)' in html
    assert 'data-val="model-a">model-a wins' not in html
    assert 'data-val="tie">Ties' not in html
