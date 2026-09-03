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

from runner.dashboard import load_runs, render_dashboard, rollup_models


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


def test_a_close_result_renders_as_a_declared_tie(runs_root):
    out = render_dashboard(runs_root, "voice")
    html = out.read_text(encoding="utf-8")
    # alpha leads by 0.35 - inside the 0.5 band, so no winner may be named.
    assert "Tie." in html
    assert "inside the 0.5 band" in html
    assert "beats" not in html


def test_a_clear_result_names_a_winner(tmp_path):
    root = tmp_path / "runs"; root.mkdir()
    for i, rid in enumerate(("2026-09-01_100000_voice-r1", "2026-09-01_110000_voice-r2")):
        _write_run(root, rid, [
            {"model": "alpha", "status": "scored", "score": 9.5},
            {"model": "beta", "status": "scored", "score": 7.0},
        ])
    html = render_dashboard(root, "voice").read_text(encoding="utf-8")
    assert "beats" in html
    assert "alpha" in html


def test_every_tab_panel_is_populated(runs_root):
    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    panels = ("p-models", "p-runs", "p-paired", "p-repeats", "p-evidence")
    for pid in panels:
        assert f'id="{pid}"' in html
    assert html.count("<audio") >= 1
    # Every panel has a nav button and exactly one is visible on load.
    assert html.count('class="panel"') == len(panels)
    assert html.count('role="tab"') == len(panels)
    # Exactly one tab selected and exactly one panel visible on load. Counted
    # on the button tags themselves - a bare count also matches the CSS rule
    # `nav.tabs button[aria-selected="true"]`, which is not a tab.
    buttons = re.findall(r'<button role="tab" aria-selected="(\w+)"', html)
    assert buttons.count("true") == 1 and len(buttons) == len(panels)
    assert len(re.findall(r'<section class="panel" id="p-\w+" hidden>', html)) == len(panels) - 1


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


def test_evidence_leads_with_one_clip_per_model_and_hides_the_repeats(runs_root):
    """
    Three near-identical cards per model buried the thing worth looking at.
    One representative leads; the repeats stay on the page behind a toggle,
    because they are the evidence the spread figure rests on.
    """
    import re

    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    panel = re.search(r'id="p-evidence" hidden>(.*?)</section>', html, re.S).group(1)
    lead = panel.split('<details class="more"')[0]
    assert lead.count("<audio") == 2, "one lead clip per model"
    assert panel.count("<audio") == 4, "every clip is still on the page"
    assert "more clip" in panel


def test_the_representative_clip_is_the_median_not_the_best(runs_root):
    """
    A best-of-N clip is a flattering sample and would quietly disagree with
    the mean shown one tab over.
    """
    import re

    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    panel = re.search(r'id="p-evidence" hidden>(.*?)</section>', html, re.S).group(1)
    lead = panel.split('<details class="more"')[0]
    # alpha scored 9.0 (r1) and 9.1 (r2); the median of two takes the upper
    # index, so r2 leads - but the point is it is chosen by rank, not by max,
    # and it carries a label saying which run it came from.
    assert "median run" in lead
    assert lead.count("median run") == 2


# --------------------------------------------------------------------------
# The repeats view. Added 2026-09-02: "spread" conflated run-to-run noise
# with between-scenario variation, and only the first of those can tell you
# whether a gap between two models is real.
# --------------------------------------------------------------------------

def test_a_scenario_run_once_reports_no_noise_floor_rather_than_zero(runs_root):
    """
    A single measurement has no spread. Rendering 0.000 would claim perfect
    consistency from evidence that cannot show any.
    """
    html = render_dashboard(runs_root, "voice").read_text(encoding="utf-8")
    assert "noise not measured" in html or "n/a" in html
    assert "noise ±0.000" not in html


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
    assert "9.000" not in html.split('id="p-repeats"')[1].split("</section>")[0]
