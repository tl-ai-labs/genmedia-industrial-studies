"""End-to-end offline: run -> checks -> judge -> score -> report, through the
same registry seam a real provider uses. No network, no keys, no spend."""
import hashlib
import json
import re

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
    for needle in ("model-a", "model-b", "W–T–L", "Judged", "Latency p50",
                   "Latency max", "Success", SCENARIO_IDS[0], "Rubric hashes"):
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


# --------------------------------------------------------------------------
# the two deliverables: an internal report and a client report, one context
# --------------------------------------------------------------------------

@pytest.fixture
def scored_run(happy_run):
    """A judged + scored run, ready to report on."""
    judge_run(happy_run["project"], happy_run["run_dir"], happy_run["models_yaml"])
    score_run(happy_run["project"], happy_run["run_dir"])
    return happy_run


def _both(project, run_dir):
    out = build_report(project, run_dir)
    client = run_dir / "report-client.html"
    return out, out.read_text(), client, client.read_text()


def test_report_writes_internal_and_client_side_by_side(scored_run):
    """Both files, every time, from one context — no flag to forget and no
    way for the two to disagree about a number."""
    out, internal, client, client_html = _both(scored_run["project"],
                                               scored_run["run_dir"])
    assert out.name == "report.html"          # the caller's contract is unchanged
    assert client.exists() and len(client_html) > 2000
    assert "<!doctype html>" in client_html.lower()


def test_client_report_drops_internal_diagnostics(scored_run):
    """The client deliverable carries quality, latency, reliability and the
    per-unit generation cost — but not judge counts, attempt counts, our
    judging spend or the run's total bill."""
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])

    for key in ("judged", "below_5", "judge_cost", "success", "attempts"):
        assert f'data-row="{key}"' in internal, key
        assert f'data-row="{key}"' not in client, key
    for key in ("mean", "worst", "lat_p50", "lat_max", "gen_cost"):   # in both
        assert f'data-row="{key}"' in internal and f'data-row="{key}"' in client

    assert 'class="tiles"' in internal and 'class="tiles"' not in client
    assert "Rubric hashes" in internal and "Rubric hashes" not in client
    # unit cost yes, our totals no
    assert "Generation cost" in internal and "Generation cost" not in client
    assert "Judging cost" in internal and "Judging cost" not in client
    assert "Totals: generation" in internal and "Totals:" not in client
    assert "billed generation cost for one" in client      # the basis is stated


def test_client_verdict_keeps_the_cost_tiebreaker(scored_run):
    """Once a cost row is on the page, silently dropping 'cheaper: X' from the
    verdict would be inconsistent — and that fact does not always favour the
    Gemini arm, so hiding it would flatter one side."""
    from runner.report import _client_prose
    note = ("tie on quality (mean gap 0.01 < 0.5, no 70% win rate). "
            "Broken only by facts: cheaper: Rival; faster p50: Gem")
    out = _client_prose(note)
    assert "cheaper: Rival" in out                   # kept, not stripped
    assert "mean gap 0.1 pp < 5 pp" in out           # still restated in %


def test_client_shows_percentages_where_internal_shows_points(scored_run):
    """8.0 out of 10 reads as 80% for the client and stays 8.00 internally.
    Expected strings come from aggregate(), never hard-coded."""
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    mean = aggregate(scored_run["run_dir"])["tasks"]["text_to_video"]["models"]["model-a"]["mean"]

    assert f"{mean:.2f}" in internal
    assert f"{mean * 10:.1f}%" in client
    assert f"{mean:.2f}" not in client
    assert "percentage of the 10-point rubric" in client


def test_gemini_first_puts_the_google_arm_left():
    from runner.report import _gemini_first
    assert _gemini_first(["z-gpt", "a-gemini-pro"],
                         {"a-gemini-pro": "Google", "z-gpt": "OpenAI"}) \
        == ["a-gemini-pro", "z-gpt"]
    # vendor beats alphabet: the Google arm wins even from the back of the list
    assert _gemini_first(["a-seedance", "z-omni"],
                         {"z-omni": "Google", "a-seedance": "ByteDance"}) \
        == ["z-omni", "a-seedance"]
    # no Google arm anywhere -> plain alphabetical, never a hidden dependency
    assert _gemini_first(["model-b", "model-a"], {}) == ["model-a", "model-b"]


def test_google_arm_is_the_first_column(project, fake_models_yaml, fake_env,
                                        monkeypatch):
    """Wiring test, not a unit test: the ordering helper can be correct while
    a template still renders the columns alphabetically."""
    text = fake_models_yaml.read_text().replace("provider: prov_b",
                                                "provider: google-vertex")
    path = project / "configs" / "models-google-b.yaml"
    path.write_text(text)

    fake_a, fake_b = _fakes()
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": FakeJudgeAdapter(score=8.0)})
    scenarios = load_scenarios(project / "scenarios", modality="video")
    models = enabled_models(load_models(path), "video")
    run_dir = run_generation(project, scenarios, models, "video",
                             budget_usd=20.0, workers=2)
    judge_run(project, run_dir, path)
    score_run(project, run_dir)
    html = build_report(project, run_dir).read_text()

    head = html[html.index('<table class="mx">'):html.index("</thead>")]
    assert head.index("model-b") < head.index("model-a")
    assert 'class="num g">model-b' in head        # the Gemini-side column mark


def test_scenario_rows_carry_sort_keys_and_the_control(scored_run):
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    for html in (internal, client):
        assert 'class="scnlist"' in html          # sortable container
        assert "data-sort" in html
        for attr in ('data-i="', 'data-g="', 'data-c="', 'data-d="'):
            assert attr in html
        for value in ("id", "g-desc", "g-asc", "c-desc", "c-asc",
                      "d-desc", "d-asc"):
            assert f'value="{value}"' in html


def test_expand_collapse_present_in_both_reports(scored_run):
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    for html in (internal, client):
        assert 'data-act="expand"' in html and 'data-act="collapse"' in html


def test_win_threshold_is_stated_as_5_percent_and_0_05(scored_run):
    """One threshold, shown in the caller's own terms: a win needs more than
    5% of the rubric scale, which is 0.05 as a fraction and 0.5 of the 10
    points. All three name the same number and must never disagree."""
    from runner.report import TIE_BAND
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    assert TIE_BAND == 0.5 and TIE_BAND / 10 == 0.05
    for html in (internal, client):
        assert "5%" in html
        assert "0.05 of the rubric scale" in html
    assert "0.5 of the 10 points" in internal      # points form: internal only
    assert "0.5 of the 10 points" not in client


def test_win_counts_agree_across_every_surface(scored_run):
    """'Handle the count everywhere': the filter chips, the W–T–L column and
    the per-family wins are three different code paths off one threshold. With
    two arms they must produce identical counts, or the page contradicts
    itself in front of a client."""
    import re as _re
    from runner.scoring import aggregate
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    agg = aggregate(scored_run["run_dir"])
    task = next(iter(agg["tasks"].values()))
    pair = task["pairs"][0]                     # exactly two arms in this lane
    expected = {pair["a"]: pair["wins_a"], pair["b"]: pair["wins_b"],
                "tie": pair["ties"]}

    for html in (internal, client):
        chips = dict(_re.findall(
            r'data-dim="win" data-val="([^"]+)">[^<]*<span class="n">\((\d+)\)', html))
        cards = _re.findall(r'data-win="([^"]+)"', html)
        for key, n in expected.items():
            # the per-scenario winner (report side) and the paired W-T-L
            # (scoring side) are separate code paths off the one threshold
            assert cards.count(key) == n, (key, n, cards.count(key))
            if n and key in chips:
                assert int(chips[key]) == n     # and the chip label agrees
        # every scenario is accounted for exactly once
        assert len(cards) == len(task["scenarios"]) == sum(expected.values())


def test_metric_row_delta_follows_each_metric_direction():
    """Lower-better metrics must not render a smaller number as a loss."""
    from runner.report import _metric_rows

    def m(mean, lat, worst=5.0):
        return {"mean": mean, "worst": worst, "wtl": "1-0-0", "judged_n": 1,
                "eligible": 1, "below_5": 0, "gen_cost_per_scenario_usd": 0.1,
                "judge_cost_per_scenario_usd": 0.01, "latency_p50_ms": lat,
                "latency_max_ms": lat, "success_rate": 1.0, "mean_attempts": 1.0}

    rows = {r["key"]: r for r in
            _metric_rows({"g": m(8.0, 3000), "r": m(7.0, 5000)}, ["g", "r"])}
    assert rows["mean"]["delta"] == pytest.approx(1.0)
    assert rows["mean"]["delta_class"] == "up"          # higher score = ahead
    assert rows["lat_p50"]["delta"] == pytest.approx(-2000)
    assert rows["lat_p50"]["delta_class"] == "up"       # faster = ahead
    assert rows["mean"]["hi"] and rows["lat_p50"]["hi"] # both highlighted

    slower = {r["key"]: r for r in
              _metric_rows({"g": m(8.0, 9000), "r": m(7.0, 5000)}, ["g", "r"])}
    assert slower["lat_p50"]["delta_class"] == "down"   # slower = behind

    tied = {r["key"]: r for r in
            _metric_rows({"g": m(8.0, 3000), "r": m(8.0, 3000)}, ["g", "r"])}
    assert tied["mean"]["delta_class"] == ""            # equal = neither
    # a single-model lane has nothing to compare against
    solo = {r["key"]: r for r in _metric_rows({"g": m(8.0, 3000)}, ["g"])}
    assert solo["mean"]["delta"] is None


def test_client_verdict_prose_restates_the_band_in_percent():
    """The stored verdict is never edited; the client copy only restates the
    tie band in the units that report uses."""
    from runner.report import _client_prose
    assert _client_prose("mean gap 0.85 >= 0.5") == "mean gap 8.5 pp >= 5 pp"
    assert _client_prose("") == "" and _client_prose(None) is None
    note = ("tie on quality (mean gap 0.01 < 0.5, no 70% win rate). "
            "Broken only by facts: higher success rate: Gem; faster p50: Gem")
    assert "mean gap 0.1 pp < 5 pp" in _client_prose(note)

def test_self_contained_leaves_small_runs_alone(scored_run, monkeypatch):
    """A run already under the inline budget is embedded as-is: no re-encode,
    no preview folder, and no disclosure claiming one happened."""
    import runner.report as rep
    called = []
    monkeypatch.setattr(rep, "_build_previews",
                        lambda paths, run_dir: called.append(1) or {})
    out = build_report(scored_run["project"], scored_run["run_dir"],
                       self_contained=True)
    html = out.read_text()
    assert called == []                                   # nothing transcoded
    assert "data:video/mp4;base64," in html                # originals inlined
    assert not (scored_run["run_dir"] / "previews").exists()
    assert "re-encoded" not in html


def test_self_contained_falls_back_when_ffmpeg_is_missing(scored_run, monkeypatch):
    """No ffmpeg means path references and the existing visible warning — never
    a page whose players are silently empty."""
    import shutil as _sh

    import runner.report as rep
    monkeypatch.setattr(rep, "VIDEO_INLINE_TOTAL_MAX", 1)   # force the big-run path
    monkeypatch.setattr(_sh, "which", lambda name: None)
    out = build_report(scored_run["project"], scored_run["run_dir"],
                       self_contained=True)
    html = out.read_text()
    assert "data:video/mp4;base64," not in html
    assert "referenced by path" in html
    assert "re-encoded" not in html


def test_client_only_difference_column_is_percentage_only(scored_run):
    """The difference column belongs to the client deliverable only, and every
    value in it is a percentage: percentage points for ratings, a relative
    percentage for speed. The internal table stays two columns wide."""
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    assert ">Difference<" in client
    assert ">Difference<" not in internal
    assert 'class="num d' not in internal
    body = client[client.index('<table class="mx">'):client.index("</table>")]
    # no bare point values leak into the column: every rendered delta is a %
    deltas = re.findall(r'<td class="num d[^"]*"><span class="dv">(.*?)</span>', body)
    assert deltas, "no difference cells rendered"
    for d in deltas:
        assert d.endswith(("pp", "%")) or "dash" in d, d


def test_relative_difference_is_computed_against_the_rival():
    from runner.report import _metric_rows

    def m(mean, lat):
        return {"mean": mean, "worst": 5.0, "wtl": "1-0-0", "judged_n": 1,
                "eligible": 1, "below_5": 0, "gen_cost_per_scenario_usd": 0.1,
                "judge_cost_per_scenario_usd": 0.01, "latency_p50_ms": lat,
                "latency_max_ms": lat, "success_rate": 1.0, "mean_attempts": 1.0}

    rows = {r["key"]: r for r in
            _metric_rows({"g": m(8.0, 20000), "r": m(7.0, 40000)}, ["g", "r"])}
    assert rows["lat_p50"]["delta_rel"] == pytest.approx(-50.0)   # half the time
    assert rows["lat_p50"]["delta_class"] == "up"                 # and that is good
    # a zero denominator must not raise or invent a number
    zero = {r["key"]: r for r in
            _metric_rows({"g": m(8.0, 20000), "r": m(7.0, 0)}, ["g", "r"])}
    assert zero["lat_p50"]["delta_rel"] is None
