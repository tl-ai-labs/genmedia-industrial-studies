"""End-to-end offline: run -> checks -> judge -> score -> report, through the
same registry seam a real provider uses. No network, no keys, no spend."""
import json
import re

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
    for needle in ("model-a", "model-b", "W–T–L", "Judged", "Latency p50",
                   "Latency max", "Success", "img-001", "Rubric hashes"):
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

def test_report_offers_a_filter_chip_per_winning_model(project, fake_models_yaml,
                                                       fake_env, monkeypatch):
    """Regression: the single-run report's evidence filter used to offer only
    'All results' and 'Ties' — the per-model 'X wins (n)' chips depended on a
    context key the template never received, so a reader could not filter
    the scenarios one model won (spotted on the 2026-09-03 pilot report).
    Here the blind judge scores by clip size, so model-b (bigger fake clip)
    wins every scenario and must get a chip with the right count."""
    # a flat image compresses far smaller than a gradient, so the two arms
    # are separable by byte length alone — no model name reaches the judge
    small, big = blank_png(), gradient_png(phase=3)
    assert len(big) > len(small)
    cutoff = (len(small) + len(big)) // 2
    fake_a = FakeImageAdapter(model_tag="a", default_bytes=small)
    fake_b = FakeImageAdapter(model_tag="b", default_bytes=big)

    def by_size(prompt, media):
        schema = json.loads(prompt.strip().splitlines()[-1])
        score = 9.5 if len(media[0][0]) > cutoff else 6.0     # b's png is larger
        return json.dumps({"criteria": [{"name": c["name"], "score": score,
                                         "reasoning": "observed specifics"}
                                        for c in schema["criteria"]],
                           "overall_note": "sized"})

    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": FakeJudgeAdapter(handler=by_size)})
    install_fake_ocr(monkeypatch,
                     lambda path: ["TRAILHEAD 750", "FRESH FILTER COFFEE"])
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios, models, "image",
                             budget_usd=5.0, workers=2)
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


def test_client_shows_percentages_where_internal_shows_points(scored_run):
    """8.0 out of 10 reads as 80% for the client and stays 8.00 internally.
    Expected strings come from aggregate(), never hard-coded."""
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    mean = aggregate(scored_run["run_dir"])["tasks"]["text_to_image"]["models"]["model-a"]["mean"]

    assert f"{mean:.2f}" in internal
    assert f"{mean * 10:.1f}%" in client
    assert f"{mean:.2f}" not in client
    assert "percentage of the 10-point rubric" in client


def test_google_arm_is_the_first_column(project, fake_models_yaml, fake_env,
                                        monkeypatch):
    """Wiring test, not a unit test: the ordering helper can be correct while
    a template still renders the columns alphabetically."""
    text = fake_models_yaml.read_text().replace("provider: prov_b",
                                                "provider: google-vertex")
    path = project / "configs" / "models-google-b.yaml"
    path.write_text(text)

    fake_a = FakeImageAdapter(model_tag="a", default_bytes=gradient_png(phase=0))
    fake_b = FakeImageAdapter(model_tag="b", default_bytes=gradient_png(phase=3))
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b,
                                   "fake_judge": FakeJudgeAdapter(score=8.0)})
    install_fake_ocr(monkeypatch, _ocr_by_model)
    scenarios = load_scenarios(project / "scenarios", modality="image")
    models = enabled_models(load_models(path), "image")
    run_dir = run_generation(project, scenarios, models, "image",
                             budget_usd=5.0, workers=2)
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


def test_win_rule_is_stated_as_any_margin(scored_run):
    """Only an identical score is a tie; both reports say so in words."""
    from runner.report import TIE_BAND
    _, internal, _, client = _both(scored_run["project"], scored_run["run_dir"])
    assert TIE_BAND == 0.0
    for html in (internal, client):
        assert "by any margin" in html
        assert "Only an identical score" in html
    assert "99% vs 100%" in client and "100% vs 100%" in client   # in each audience's units
    assert "9.9 vs 10" in internal and "10 vs 10" in internal


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


def _token_billed_models_yaml(project):
    """model-a estimated at $0.01 a call but actually billing $1.60.

    The pre-flight therefore passes and only the mid-run guard can stop it,
    which is the case a flat est_usd_per_call gets wrong whenever real usage
    varies from call to call.
    """
    text = """
version: 1
image:
  - id: model-a
    enabled: true
    adapter: fake_a
    provider: prov_a
    provider_model: "prov-a-img-1"
    auth_env: FAKE_KEY_A
    supports: [text_to_image, image_edit]
    limits: {max_concurrency: 2}
    params: {size: "1024x1024"}
    price: {unit: per_token, usd_out_per_1m: 40.0, est_usd_per_call: 0.01,
            source: test, as_of: 2026-08-31}
  - id: model-b
    enabled: true
    adapter: fake_b
    provider: prov_b
    provider_model: "prov-b-img-1"
    auth_env: FAKE_KEY_B
    supports: [text_to_image]
    limits: {max_concurrency: 2}
    params: {size: "1024x1024"}
    price: {unit: per_token, usd_in_per_1m: 10.0, usd_out_per_1m: 40.0,
            est_usd_per_call: 0.07, source: test, as_of: 2026-08-31}
judge:
  image:
    adapter: fake_judge
    provider: prov_j
    provider_model: "prov-judge-1"
    auth_env: FAKE_KEY_J
    temperature: 0
    price: {unit: per_token, usd_in_per_1m: 0.5, usd_out_per_1m: 3.0,
            est_usd_per_call: 0.003, source: test, as_of: 2026-08-31}
"""
    path = project / "configs" / "models-fake-token.yaml"
    path.write_text(text)
    return path


def test_provider_cap_refuses_a_plan_it_cannot_cover(project, fake_models_yaml,
                                                     fake_env, monkeypatch):
    """Pre-flight, per provider: 3 x $0.067 of prov_a cannot fit a $0.10 cap.

    The total budget is deliberately generous, so a rejection can only have
    come from the provider cap.
    """
    fake_a = FakeImageAdapter(model_tag="a")
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    with pytest.raises(RunRejected, match="prov_a"):
        run_generation(project, scenarios, models, "image", budget_usd=5.0,
                       provider_caps={"prov_a": 0.10})
    assert fake_a.calls == 0 and fake_b.calls == 0   # nothing was spent


def test_provider_cap_aborts_mid_run_and_leaves_other_arms_alone(
        project, fake_models_yaml, fake_env, monkeypatch):
    """The cap binds prov_a alone; prov_b finishes its whole set.

    prov_a bills $1.60 a call against a $2.50 cap: two calls land ($3.20 of
    actual spend, since the guard tests the $0.01 estimate), the third is
    refused. prov_b is uncapped and unaffected — that separation is the
    entire point of the flag.
    """
    fake_a = FakeImageAdapter(model_tag="a", usage={"output_tokens": 40_000})
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    models_yaml = _token_billed_models_yaml(project)
    scenarios, models = _load(project, models_yaml)
    run_dir = run_generation(project, scenarios, models, "image",
                             budget_usd=20.0, workers=1,
                             provider_caps={"prov_a": 2.50})

    assert fake_a.calls == 2, "prov_a should stop at its own cap"
    assert fake_b.calls == 3, "prov_b is uncapped and must run its whole set"

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["state"] == "aborted"
    assert manifest["budget_by_provider"] == {"prov_a": 2.50}

    spent = {}
    for row in RunFiles(run_dir).read("telemetry"):
        if row.get("cost"):
            spent[row["provider"]] = spent.get(row["provider"], 0) + row["cost"]["micro_usd"]
    assert spent["prov_a"] == 3_200_000      # 2 x $1.60, stopped before a third
    assert spent["prov_b"] == 210_000        # 3 x $0.07, untouched


def test_provider_cap_counts_what_that_provider_already_spent_on_resume(
        project, fake_models_yaml, fake_env, monkeypatch):
    """Resume reads prior spend per provider, not just in total.

    Without that, every resume would hand each provider a fresh full cap and
    the balance this flag exists to protect would drain a batch at a time.
    """
    fake_a = FakeImageAdapter(model_tag="a")
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    run_dir = run_generation(project, scenarios[:1], models, "image",
                             budget_usd=5.0, provider_caps={"prov_a": 1.00})
    assert fake_a.calls == 1                     # $0.067 of prov_a is now spent

    # $0.067 already gone + $0.067 estimated for the next prov_a cell = $0.134,
    # which no longer fits a $0.10 cap.
    with pytest.raises(RunRejected, match="prov_a"):
        run_generation(project, scenarios[:2], models, "image", budget_usd=5.0,
                       run_id=run_dir.name, provider_caps={"prov_a": 0.10})
    assert fake_a.calls == 1                     # and it did not pay again


def test_a_cap_on_a_provider_that_is_not_running_is_refused(
        project, fake_models_yaml, fake_env, monkeypatch):
    """A typo'd provider name would protect nothing at all, silently."""
    fake_a = FakeImageAdapter(model_tag="a")
    fake_b = FakeImageAdapter(model_tag="b")
    install_adapters(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})
    scenarios, models = _load(project, fake_models_yaml)
    with pytest.raises(RunRejected, match="openai_typo"):
        run_generation(project, scenarios, models, "image", budget_usd=5.0,
                       provider_caps={"openai_typo": 80.0})
    assert fake_a.calls == 0 and fake_b.calls == 0


@pytest.mark.parametrize("bad", ["openai", "=80", "openai=eighty",
                                 "openai=0", "openai=-5"])
def test_malformed_provider_cap_is_an_error_not_a_shrug(bad):
    from runner.cli import _parse_provider_caps
    with pytest.raises(ValueError):
        _parse_provider_caps([bad])


def test_provider_caps_parse_and_reject_duplicates():
    from runner.cli import _parse_provider_caps
    assert _parse_provider_caps(["openai=80", "google-vertex=30.5"]) == {
        "openai": 80.0, "google-vertex": 30.5}
    assert _parse_provider_caps([]) == {}
    with pytest.raises(ValueError, match="twice"):
        _parse_provider_caps(["openai=80", "openai=90"])
