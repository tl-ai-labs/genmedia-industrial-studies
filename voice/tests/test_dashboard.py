"""
The cross-run dashboard's aggregation.

Rendering is presentational and not worth pinning, but the arithmetic under it
is: a rollup that quietly averages an unjudged cell as zero, or a paired
comparison that counts a tie as a win, would put a wrong verdict on a page
someone forwards to a customer. Those are what these tests hold.
"""

from __future__ import annotations

import re

import json
from pathlib import Path

import pytest

from runner.dashboard import (WIN_GAP, load_runs, render_dashboard,
                              rollup_models)


def _write_run(root: Path, run_id: str, cells: list[dict], scenario_hash: str = "h-orig") -> None:
    """Build a minimal but structurally real run folder."""
    d = root / run_id
    (d / "outputs" / "voice" / "s1").mkdir(parents=True)
    models = sorted({c["model"] for c in cells})
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "modality": "voice",
                "started_at": "2026-09-01T10:00:00+0530",
                "scenario_count": 1,
                "scenarios": [{"id": "s1", "task": "text_to_speech", "hash": scenario_hash,
                               "source": "scenarios/s1.yaml", "source_format": "yaml"}],
                "git_sha": "abc1234",
                "models": [{"id": m, "voice_map": {"female_mid_warm": f"{m}-voice"}} for m in models],
                "judge": {"provider_model": "test-judge"},
                "mos": {"predictor": "signal"},
                "calibration": {"passed": False, "reason": "not run"},
            }
        ),
        encoding="utf-8",
    )
    with (d / "checks.jsonl").open("w") as ch, (d / "scores.jsonl").open("w") as sc, \
         (d / "telemetry.jsonl").open("w") as tl:
        for c in cells:
            key = {"scenario_id": "s1", "model_id": c["model"]}
            ch.write(json.dumps({**key, "passed": c.get("passed", True),
                                 "failed_gates": [] if c.get("passed", True) else ["wer_within_max"],
                                 "gates": [{"gate": "decodes", "passed": True},
                                           {"gate": "wer_within_max", "passed": c.get("passed", True)}],
                                 "measurements": {"normalized_wer": c.get("wer", 0.0),
                                                  "duration_s": 60.0,
                                                  "audio_quality_1_5": c.get("q", 4.0),
                                                  "audio_quality_is_mos": False}}) + "\n")
            sc.write(json.dumps({**key, "task": "text_to_speech", "status": c["status"],
                                 "score": c["score"], "criterion_scores": {},
                                 "calibration_trusted": False}) + "\n")
            tl.write(json.dumps({**key, "attempt": 1, "status": "ok", "latency_ms": c.get("lat", 10000),
                                 "output": {"duration_s": 60.0},
                                 "voice": {"provider_voice_id": f"{c['model']}-voice"},
                                 "cost": {"micro_usd": c.get("cost", 50000), "usage_exact": True}}) + "\n")
        (d / "outputs" / "voice" / "s1" / f"{cells[0]['model']}.wav").write_bytes(b"RIFF")


@pytest.fixture
def runs_root(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "status": "scored", "score": 9.0, "cost": 90000, "lat": 8000, "q": 4.2},
        {"model": "beta", "status": "scored", "score": 8.8, "cost": 30000, "lat": 40000, "q": 3.5},
    ])
    _write_run(root, "2026-09-01_110000_voice-r2", [
        {"model": "alpha", "status": "scored", "score": 9.1, "cost": 90000, "lat": 8200, "q": 4.3},
        {"model": "beta", "status": "scored", "score": 8.6, "cost": 30000, "lat": 39000, "q": 3.4},
    ])
    return root


def test_loads_every_run_and_cell(runs_root):
    runs = load_runs(runs_root, "voice")
    assert [r.label for r in runs] == ["voice-r1", "voice-r2"]
    assert sum(len(r.cells) for r in runs) == 4


def test_rollup_averages_across_runs_and_reports_spread(runs_root):
    models = {m.model_id: m for m in rollup_models(load_runs(runs_root, "voice"))}
    assert models["alpha"].mean_score == pytest.approx(9.05)
    assert models["alpha"].score_spread == pytest.approx(0.1)
    assert models["beta"].score_spread == pytest.approx(0.2)


def test_rollup_is_ordered_best_first(runs_root):
    assert [m.model_id for m in rollup_models(load_runs(runs_root, "voice"))] == ["alpha", "beta"]


def test_each_model_gets_a_distinct_accent(runs_root):
    accents = [m.accent for m in rollup_models(load_runs(runs_root, "voice"))]
    assert len(set(accents)) == len(accents)


def test_unjudged_cells_are_excluded_from_the_mean_not_zeroed(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "status": "scored", "score": 9.0},
        {"model": "alpha2", "status": "unjudged", "score": None},
    ])
    models = {m.model_id: m for m in rollup_models(load_runs(root, "voice"))}
    # The unjudged model has no mean at all - it must not average in as 0.
    assert models["alpha2"].mean_score is None
    assert models["alpha2"].unjudged == 1
    assert models["alpha"].mean_score == pytest.approx(9.0)


def test_an_invalid_cell_is_counted_but_not_averaged_into_quality(tmp_path):
    """
    CHANGED 2026-09-03, deliberately. This test previously asserted that an
    invalid cell's 0.0 stayed in the quality mean. The principle behind that
    was right - a model must not hide a failure by having it discarded - but
    the implementation made the headline number unreadable: ten of sixteen
    cells were gated on TIMING, and the board showed 2.4/10 for two models
    that had tied at ~9.8 on the one scenario they both cleared. A clean read
    that is 2.5s too long for an ad slot is the wrong LENGTH, not bad audio.

    One number was being asked two questions. Now quality is meaned over
    scored cells, the failure is carried in `invalid` and `gate_pass_rate`,
    and ranking is gate-first - so failing more can never look like scoring
    higher. Nothing is discarded; it is reported in the field that means it.
    """
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-a", [
        {"model": "alpha", "status": "scored", "score": 9.0}])
    _write_run(root, "2026-09-01_110000_voice-b", [
        {"model": "alpha", "status": "invalid", "score": 0.0, "passed": False, "wer": 0.9}])

    m = rollup_models(load_runs(root, "voice"))[0]
    assert m.mean_score == pytest.approx(9.0)   # not 4.5
    assert m.scored_n == 1 and m.evaluated_n == 2   # the denominator travels with it
    assert m.invalid == 1                       # the failure is still counted
    assert m.gate_pass_rate == pytest.approx(0.5)


def test_failing_more_can_never_outrank_delivering_more(tmp_path):
    """
    The trap the gate-first ranking exists to close. `flaky` scores higher on
    the one cell it did not fail; `solid` cleared every cell. Ranking on
    quality alone would put the failing model first.
    """
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-a", [
        {"model": "solid", "status": "scored", "score": 9.0},
        {"model": "flaky", "status": "scored", "score": 9.9}])
    _write_run(root, "2026-09-01_110000_voice-b", [
        {"model": "solid", "status": "scored", "score": 9.0},
        {"model": "flaky", "status": "invalid", "score": 0.0, "passed": False, "wer": 0.9}])

    order = [m.model_id for m in rollup_models(load_runs(root, "voice"))]
    assert order == ["solid", "flaky"], order


def test_the_rendered_mean_always_carries_its_denominator(tmp_path):
    """9.7 over one cell and 9.7 over eight are not the same claim."""
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-a", [
        {"model": "alpha", "status": "scored", "score": 9.0}])
    _write_run(root, "2026-09-01_110000_voice-b", [
        {"model": "alpha", "status": "invalid", "score": 0.0, "passed": False, "wer": 0.9}])
    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    assert "(1 of 2)" in html


def test_a_gap_inside_the_band_names_no_winner(tmp_path):
    """
    THE BAND, THIRD REVISION (2026-09-04). Fixed 0.5 -> each model's measured
    run-to-run spread -> flat WIN_GAP, set by the study owner. Only a gap
    inside the band ties now.

    alpha means 9.00 and beta 8.98: a 0.02 gap, inside 0.05.
    """
    root = tmp_path / "runs"; root.mkdir()
    for rid, a, b in (("2026-09-01_100000_voice-r1", 9.00, 8.98),
                      ("2026-09-01_110000_voice-r2", 9.00, 8.98)):
        _write_run(root, rid, [{"model": "alpha", "status": "scored", "score": a},
                               {"model": "beta", "status": "scored", "score": b}])
    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    assert "Tie." in html
    assert "leads</b>" not in html


def test_a_gap_that_clears_the_band_names_a_winner(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    for rid in ("2026-09-01_100000_voice-r1", "2026-09-01_110000_voice-r2"):
        _write_run(root, rid, [
            {"model": "alpha", "status": "scored", "score": 9.5},
            {"model": "beta", "status": "scored", "score": 7.0},
        ])
    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    assert "alpha leads" in html
    assert f"clears the {WIN_GAP} decision band" in html


def test_a_decided_gap_smaller_than_its_own_noise_says_so(tmp_path):
    """
    The band no longer consults the measured spread, so the board can name a
    winner on a gap the runs have not shown to be real. It must SAY that
    where it happens - this is the only thing standing between a reader and
    a verdict that inverts on a re-run.

    alpha: 9.0 then 9.6 (spread 0.6). beta: 8.9 then 9.4 (spread 0.5).
    Mean gap 0.15 - past the 0.05 band, well inside a 0.6 floor.
    """
    root = tmp_path / "runs"; root.mkdir()
    for rid, a, b in (("2026-09-01_100000_voice-r1", 9.0, 8.9),
                      ("2026-09-01_110000_voice-r2", 9.6, 9.4)):
        _write_run(root, rid, [{"model": "alpha", "status": "scored", "score": a},
                               {"model": "beta", "status": "scored", "score": b}])
    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    # NB the apostrophe in "scenario's" is escaped in the rendered page.
    assert "SMALLER than this scenario" in html
    assert "measured" in html
    assert "re-run can move or invert it" in html


def test_every_tab_panel_is_populated(runs_root):
    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    panels = ("t-scenarios", "t-models", "t-repeats", "t-runs")
    for pid in panels:
        assert f'data-tab="{pid}"' in html
    assert html.count("<audio") >= 1
    assert html.count('class="runpanel') == len(panels)
    # Exactly one tab selected and exactly one panel visible on load.
    selected = re.findall(r'role="tab" aria-selected="(\w+)"', html)
    assert selected.count("true") == 1 and len(selected) == len(panels)
    # Exactly one panel visible on load; the rest carry `hidden`.
    assert len(re.findall(r'<section class="runpanel" data-tab="[\w-]+" hidden>', html)) == len(panels) - 1


def test_audio_paths_are_relative_to_the_runs_root(runs_root):
    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    assert 'src="2026-09-01_100000_voice-r1/outputs/voice/s1/' in html
    assert "file://" not in html and "/tmp/" not in html


def test_uncalibrated_runs_badge_every_quality_figure(runs_root):
    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    assert "pill-warning" in html and ">unc<" in html


def test_signal_predictor_is_never_called_a_mos(runs_root):
    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    assert "not a MOS" in html


def test_refuses_when_there_are_no_runs(tmp_path):
    empty = tmp_path / "runs"; empty.mkdir()
    with pytest.raises(SystemExit, match="no voice runs"):
        render_dashboard(empty, "voice")


def test_clips_live_in_the_model_column_the_verdict_compares(runs_root):
    """
    The clips used to sit in an Evidence tab of their own, so hearing what a
    verdict was about took a tab switch and a hunt for the same id. They are
    now inside the two columns being compared - one representative leads per
    model, the repeats behind a toggle, because they are the evidence the
    spread figure rests on and hiding them would make it unfalsifiable.
    """
    import re

    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    assert "t-evidence" not in html, "the Evidence tab is gone"
    panel = re.search(r'data-tab="t-scenarios">(.*?)</section>', html, re.S).group(1)
    cols = panel.split('<div class="col ')[1:]
    assert len(cols) == 2, "one column per model"
    for col in cols:
        lead = col.split('<details class="more"')[0]
        assert lead.count("<audio") == 1, "one lead clip in the column head"
        assert col.count("<audio") == 2, "the repeat is still on the page"
    assert "more clip" in panel


def test_the_representative_clip_is_the_median_not_the_best(runs_root):
    """
    A best-of-N clip is a flattering sample and would quietly disagree with
    the mean shown beside it.
    """
    import re

    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    panel = re.search(r'data-tab="t-scenarios">(.*?)</section>', html, re.S).group(1)
    # alpha scored 9.0 (r1) and 9.1 (r2); the median of two takes the upper
    # index, so r2 leads - but the point is it is chosen by rank, not by max,
    # and it carries a label saying so.
    assert panel.count("median take") == 2, "one per model column"
    for col in panel.split('<div class="col')[1:]:
        assert "median take" in col.split('<details class="more"')[0]


# --------------------------------------------------------------------------
# The repeats view. Added 2026-09-02: "spread" conflated run-to-run noise
# with between-scenario variation, and only the first of those can tell you
# whether a gap between two models is real.
# --------------------------------------------------------------------------

def test_a_scenario_run_once_reports_no_noise_floor_rather_than_zero(tmp_path):
    """
    A single measurement has no spread. Rendering 0.000 would claim perfect
    consistency from evidence that cannot show any.

    FIXTURE CORRECTED 2026-09-03: this used the two-run `runs_root`, where
    every scenario HAS a spread - so it could never exercise the case it
    describes, and passed on an "n/a" coming from somewhere else on the page.
    """
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-only", [
        {"model": "alpha", "status": "scored", "score": 9.0},
        {"model": "beta", "status": "scored", "score": 8.6}])

    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    assert "not measured" in html
    assert "±0.000" not in html
    m = {x.model_id: x for x in rollup_models(load_runs(root, "voice"))}
    assert m["alpha"].repeat_spread is None


def test_repeat_spread_is_none_until_a_scenario_is_run_twice():
    from runner.dashboard import ModelRollup

    once = ModelRollup(model_id="m", accent="#000", n=2, scores=[9.0, 8.0],
                       wers=[], durations=[], latencies=[], costs=[], audio_qs=[],
                       attempts=[], ok=2, unjudged=0, invalid=0,
                       by_scenario={"s1": [9.0], "s2": [8.0]})
    # Two scenarios, one run each: a full point of total spread, no noise.
    assert once.score_spread == 1.0
    assert once.repeat_spread is None
    assert once.repeated_scenarios == 0

    twice = ModelRollup(model_id="m", accent="#000", n=2, scores=[9.0, 8.8],
                        wers=[], durations=[], latencies=[], costs=[], audio_qs=[],
                        attempts=[], ok=2, unjudged=0, invalid=0,
                        by_scenario={"s1": [9.0, 8.8]})
    assert twice.repeat_spread == pytest.approx(0.2)
    assert twice.repeated_scenarios == 1


def test_total_spread_and_repeat_spread_measure_different_things():
    """
    The bug this guards: one model that is rock-steady but handles two
    scenarios very differently must NOT look noisy.
    """
    from runner.dashboard import ModelRollup

    m = ModelRollup(model_id="m", accent="#000", n=4, scores=[9.5, 9.5, 6.0, 6.0],
                    wers=[], durations=[], latencies=[], costs=[], audio_qs=[],
                    attempts=[], ok=4, unjudged=0, invalid=0,
                    by_scenario={"easy": [9.5, 9.5], "hard": [6.0, 6.0]})
    assert m.score_spread == 3.5      # looks wildly inconsistent
    assert m.repeat_spread == 0.0     # but repeats perfectly
    assert m.scenario_spread == 3.5   # the variation is between scenarios


# --------------------------------------------------------------------------
# A repeat is the same SCRIPT, not the same id. Added 2026-09-03 after
# vr-game-02 was run, found to carry a gate no correct reading could pass,
# fixed, and re-run under its own id. Keyed on the id alone those two runs
# look like a repeat, and the size of OUR EDIT would be published as this
# machine's noise floor - the number every "is this gap real" verdict is
# divided by.
# --------------------------------------------------------------------------

def test_an_edited_scenario_is_not_a_repeat_of_its_earlier_self(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-a", [
        {"model": "alpha", "status": "scored", "score": 9.0}], scenario_hash="h-before")
    _write_run(root, "2026-09-01_110000_voice-b", [
        {"model": "alpha", "status": "scored", "score": 6.0}], scenario_hash="h-after")

    m = {x.model_id: x for x in rollup_models(load_runs(root, "voice"))}["alpha"]
    # Same id, two definitions -> not a repeat, so no noise floor is claimed.
    assert m.repeat_spread is None
    assert m.repeated_scenarios == 0
    # The 3.0 difference is still visible as total spread; it is simply not
    # allowed to masquerade as run-to-run noise.
    assert m.score_spread == pytest.approx(3.0)


def test_two_runs_of_the_identical_scenario_are_a_repeat(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    for rid, score in (("2026-09-01_100000_voice-a", 9.0), ("2026-09-01_110000_voice-b", 8.8)):
        _write_run(root, rid, [{"model": "alpha", "status": "scored", "score": score}],
                   scenario_hash="h-same")
    m = {x.model_id: x for x in rollup_models(load_runs(root, "voice"))}["alpha"]
    assert m.repeated_scenarios == 1
    assert m.repeat_spread == pytest.approx(0.2)


def test_the_repeats_tab_excludes_a_stale_definition_and_says_so(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-old", [
        {"model": "alpha", "status": "scored", "score": 9.0},
        {"model": "beta", "status": "scored", "score": 8.0}], scenario_hash="h-before")
    for rid, a, b in (("2026-09-01_110000_voice-n1", 7.0, 6.9),
                      ("2026-09-01_120000_voice-n2", 7.1, 6.8)):
        _write_run(root, rid, [{"model": "alpha", "status": "scored", "score": a},
                               {"model": "beta", "status": "scored", "score": b}],
                   scenario_hash="h-after")

    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    assert "different version" in html
    # The excluded run's score must not appear as a repeat column.
    panel = html.split('data-tab="t-repeats"')[1].split("</section>")[0]
    assert "9.000" not in panel


def test_two_runs_sharing_a_label_are_not_collapsed_into_one_column(tmp_path):
    """
    A run's label is run_id.split("_")[-1], so every run of one scenario
    shares it - "voice-vr-game-02" named four different runs. Keyed on the
    label they collapsed into a single entry and the SAME score rendered in
    two columns, which reads as a model reproducing itself exactly when it
    had only been asked once. Columns are keyed on run_id.
    """
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-same", [
        {"model": "alpha", "status": "scored", "score": 9.0}])
    _write_run(root, "2026-09-01_110000_voice-same", [
        {"model": "alpha", "status": "scored", "score": 7.0}])
    assert len({r.label for r in load_runs(root, "voice")}) == 1, "fixture must share a label"

    from runner.dashboard import _duel, _scenario_blocks
    runs = load_runs(root, "voice")
    block = _scenario_blocks(runs, _duel(rollup_models(runs)))[0]
    assert block["n_passes"] == 2
    row = block["rows"][0]
    assert row["pass_scores"] == [9.0, 7.0]      # not [9.0, 9.0]
    assert row["spread"] == pytest.approx(2.0)   # not 0.0


def test_a_tie_is_never_dressed_as_a_win(tmp_path):
    """
    The filter and the badge both key on `result_class`, so a gap inside the
    decision band must classify as `tie` and be reachable only under "No
    winner" - never counted in the "Gemini wins" chip.

    Scores chosen so the two passes cancel to a mean gap of 0.005, inside
    the 0.05 band. (Under the previous noise-floor rule this test used a
    0.445 gap against a 0.5 floor; the band moved, the contract did not.)
    """
    root = tmp_path / "runs"; root.mkdir()
    for rid, a, b in (("2026-09-01_100000_voice-a", 9.00, 8.99),
                      ("2026-09-01_110000_voice-b", 8.50, 8.51)):
        _write_run(root, rid, [{"model": "gemini-x", "status": "scored", "score": a},
                               {"model": "other-y", "status": "scored", "score": b}],
                   scenario_hash="h")
    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    # Scoped to the CARDS - the filter chips carry data-res too, and matching
    # those would make this assertion pass no matter how a scenario ended.
    cards = re.findall(r'<article class="scard" data-ind="[^"]*" data-res="(\w+)"', html)
    # mean gap 0.005, inside the 0.05 band, so no winner.
    assert cards == ["tie"], cards
    assert "wins</span>" not in html.split('class="wbadge')[1][:90]


def test_every_scenario_card_carries_its_industry_and_prompt(runs_root):
    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    assert 'class="scard"' in html
    assert 'data-ind=' in html and 'data-res=' in html
    # The filter bar offers both axes.
    assert 'data-ind="all"' in html and 'data-res="gemini"' in html


# --------------------------------------------------------------------------
# Variants. A scenario with `variants:` sends a DIFFERENT script per variant
# and produces cells named `parent#variant`. The board renders one card per
# PARENT, and both of the joins below were written before that was true.
# --------------------------------------------------------------------------

def _write_variant_run(root: Path, run_id: str, parent: str, variants: list[str],
                       models: list[str]) -> None:
    """A run of one variant scenario, with its frozen source beside it."""
    d = root / run_id
    (d / "scenarios").mkdir(parents=True)
    (d / "scenarios" / f"{parent}.yaml").write_text(
        f"id: {parent}\nmodality: voice\ntask: text_to_speech\nvariants:\n"
        + "".join(f'  - id: {v}\n    title: "the {v} reading"\n'
                  f'    script: |\n      Read this the {v} way.\n' for v in variants),
        encoding="utf-8")
    sids = [f"{parent}#{v}" for v in variants]
    (d / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "modality": "voice", "started_at": "2026-09-01T10:00:00+0530",
        "scenario_count": len(sids),
        "scenarios": [{"id": s, "hash": "h-var"} for s in sids],
        "models": [{"id": m, "voice_map": {}} for m in models],
        "judge": {"provider_model": "test-judge"}, "mos": {"predictor": "signal"},
        "calibration": {"passed": False, "reason": "not run"},
    }), encoding="utf-8")
    with (d / "checks.jsonl").open("w") as ch, (d / "scores.jsonl").open("w") as sc, \
         (d / "telemetry.jsonl").open("w") as tl:
        for i, sid in enumerate(sids):
            for j, m in enumerate(models):
                (d / "outputs" / "voice" / sid).mkdir(parents=True, exist_ok=True)
                (d / "outputs" / "voice" / sid / f"{m}.wav").write_bytes(b"RIFF")
                key = {"scenario_id": sid, "model_id": m}
                ch.write(json.dumps({**key, "gates": [{"gate": "decodes", "passed": True}],
                                     "measurements": {"normalized_wer": 0.01,
                                                      "duration_s": 30.0}}) + "\n")
                sc.write(json.dumps({**key, "task": "text_to_speech", "status": "scored",
                                     "score": 9.0 - j * 0.5 + i * 0.1,
                                     "criterion_scores": {}, "calibration_trusted": False}) + "\n")
                tl.write(json.dumps({**key, "attempt": 1, "status": "ok", "latency_ms": 9000,
                                     "output": {"duration_s": 30.0},
                                     "cost": {"micro_usd": 40000, "usage_exact": True}}) + "\n")


@pytest.fixture
def variant_root(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    for rid in ("2026-09-01_100000_voice-v1", "2026-09-01_110000_voice-v2"):
        _write_variant_run(root, rid, "vr-x-01", ["bare", "nato"], ["alpha", "beta"])
    return root


def test_a_variant_scenario_is_one_card_that_still_shows_both_models(variant_root):
    """
    The join that broke the board. Both the side-by-side builder and the
    script reader matched `c.scenario_id == sid`, which was right while a
    card was one scenario id and silently wrong once a card became one
    PARENT - every cell is named `parent#variant`, so the match found
    nothing and the card rendered its verdict above an empty space where
    the two model columns belong.
    """
    html = render_dashboard(variant_root, "voice").read_text(encoding="utf-8")
    assert html.count('class="scard"') == 1, "two variants are one scenario"
    panel = re.search(r'data-tab="t-scenarios">(.*?)</section>', html, re.S).group(1)
    assert len(panel.split('<div class="col ')[1:]) == 2, "both models have a column"
    assert "<audio" in panel


def test_every_variant_script_is_shown_not_just_a_blank_box(variant_root):
    """
    `input.script` is absent on a variant scenario, so the prompt box came
    out empty - and the difference between the two scripts is the whole
    question those scenarios ask.
    """
    html = render_dashboard(variant_root, "voice").read_text(encoding="utf-8")
    assert "Read this the bare way." in html
    assert "Read this the nato way." in html
    assert "2 variants" in html


def test_the_scenario_count_is_parents_never_cells(variant_root):
    """
    Counting raw ids called a 17-scenario study a 102-scenario one, and
    contradicted the tab beside it.
    """
    html = render_dashboard(variant_root, "voice").read_text(encoding="utf-8")
    assert "1 scenarios · 8 clips · 2 runs" in html


def test_a_variant_clips_url_is_fetchable_not_a_fragment(variant_root):
    """
    A silent, total failure that looked fine. Variant clips live under a
    directory named `parent#variant`, and `#` in a URL opens the FRAGMENT -
    so `<audio src=".../vr-x-01#bare/alpha.wav">` asked the server for
    `.../vr-x-01`, got a 404, and rendered a player that did nothing when
    pressed. 192 of the real board's 242 players were dead this way, with
    nothing on the page or in the console to say so.

    The assertion is on the SRC, not on the encoding helper: a passing
    helper with an unencoded call site is exactly the state this was in.
    """
    import re

    html = render_dashboard(variant_root, "voice").read_text(encoding="utf-8")
    srcs = re.findall(r'<audio[^>]+src="([^"]+)"', html)
    assert srcs, "the page has players at all"
    for src in srcs:
        assert "#" not in src, f"{src} truncates at the fragment and cannot load"
        assert "%23" in src, f"{src} lost the directory the clip is actually in"
    # And the encoded name is the real one on disk.
    from urllib.parse import unquote
    for src in srcs:
        assert (variant_root / unquote(src)).exists(), f"{src} points at nothing"


def test_the_latency_column_is_a_real_median_not_a_mean(tmp_path):
    """
    The board printed `statistics.mean` under a heading that said "Latency
    p50" until 2026-09-04, while report.py used a real median - so the two
    surfaces disagreed about the same runs by whatever the tail was worth.

    Latencies 1000/1000/1000/7000: median 1000, mean 2500. Only one of those
    may appear under a p50 heading.
    """
    root = tmp_path / "runs"; root.mkdir()
    for rid, lat in (("2026-09-01_100000_voice-r1", 1000), ("2026-09-01_110000_voice-r2", 1000),
                     ("2026-09-01_120000_voice-r3", 1000), ("2026-09-01_130000_voice-r4", 7000)):
        _write_run(root, rid, [{"model": "alpha", "status": "scored", "score": 9.0, "lat": lat}])
    m = {x.model_id: x for x in rollup_models(load_runs(root, "voice"))}["alpha"]
    assert m.p50_latency == 1000, "median, not mean"
    assert m.mean_latency == 2500, "the mean is still available for the duel strip"
    assert m.p95_latency == pytest.approx(6100)
    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    assert "Latency p50 / p95" in html
    assert "2.5s" not in html, "the mean must not appear under a p50 heading"


def _mixed_root(tmp_path):
    """
    One model ahead on GATES, the other ahead on QUALITY.

    alpha clears every gate but scores 9.0; beta fails a gate on one run and
    scores 9.4. `rollup_models` ranks gate-first, so alpha is models[0] while
    beta is ahead on the quality gap - the exact split that broke `_overall`.
    """
    root = tmp_path / "runs"; root.mkdir()
    _write_run(root, "2026-09-01_100000_voice-r1", [
        {"model": "alpha", "status": "scored", "score": 9.0},
        {"model": "beta", "status": "scored", "score": 9.4}])
    _write_run(root, "2026-09-01_110000_voice-r2", [
        {"model": "alpha", "status": "scored", "score": 9.0},
        {"model": "beta", "status": "invalid", "score": 0.0, "passed": False}])
    return root


def test_the_leader_is_whoever_the_gap_points_at_not_whoever_sorts_first(tmp_path):
    """
    `_overall` returned models[0] unconditionally, and models[] is sorted
    GATE-first - so it printed "X leads" above a table showing X behind on
    quality. On the real bank it named ElevenLabs (gates 89.8%, quality
    93.4%) the leader over Gemini (gates 88.2%, quality 95.1%).

    Invisible while the noise floor decided, because every gap fell inside it
    and the answer was "Tie". A flat 0.05 band let the line run.
    """
    from runner.dashboard import _overall

    models = rollup_models(load_runs(_mixed_root(tmp_path), "voice"))
    assert models[0].model_id == "alpha", "ranked gate-first"
    assert models[0].mean_score < models[1].mean_score, "but behind on quality"

    v = _overall(models)
    assert v["winner"] is None, "neither model may be crowned on a split"
    assert v["verdict"] == "Split"
    assert "alpha leads" not in v["detail"] and v["verdict"] != "alpha leads"
    assert "beta" in v["detail"] and "alpha" in v["detail"], "both sides named"


def test_a_clean_lead_on_both_axes_still_names_a_winner(tmp_path):
    """The fix must not stop a genuine winner being named."""
    from runner.dashboard import _overall

    root = tmp_path / "runs"; root.mkdir()
    for rid in ("2026-09-01_100000_voice-r1", "2026-09-01_110000_voice-r2"):
        _write_run(root, rid, [{"model": "alpha", "status": "scored", "score": 9.5},
                               {"model": "beta", "status": "scored", "score": 7.0}])
    v = _overall(rollup_models(load_runs(root, "voice")))
    assert v["winner"] == "alpha" and v["verdict"] == "alpha leads"
