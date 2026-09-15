"""
The human-review layer: corrections and observations, kept apart.

WHAT THESE TESTS HOLD. Three things that would each put a wrong or a
misleading number on a client-facing page:

  1. A correction changes the automated result to the right one AND leaves
     the original beside it - on the cell, on both boards.
  2. An observation never moves a score, a gate rate or a winner. It is
     rendered in its own block and nowhere else.
  3. A correction that names a clip, a gate or a run that does not exist is
     refused loudly, never skipped.

Plus the region provenance: where each model was served from is on both
pages, from the manifest when the run recorded it and from the config -
labelled as such - when it did not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from runner.client_report import build, render_client_report
from runner.dashboard import (load_runs, load_runs_reviewed, render_dashboard,
                              rollup_models, served_rows)
from runner.review import (ReviewError, apply_corrections, load_review,
                           review_context)

GEM, EL = "gemini-3-1-flash-tts", "elevenlabs-v3"


def _wav(path: Path, seconds: float = 0.3, sr: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    sf.write(str(path), (0.2 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr)


def _run(root: Path, run_id: str, cells: list[dict], sid: str = "vr-ecom-01",
         served: bool = True) -> None:
    """A run folder with one scenario. `served=False` mimics a pre-2026-09-14 manifest."""
    d = root / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenarios").mkdir(exist_ok=True)
    (d / "scenarios" / "s.yaml").write_text(
        f'id: {sid}\nmodality: voice\ntask: text_to_speech\ntitle: "A readback"\n'
        f'input:\n  script: |\n    Your order 4 4 1 9 ships today.\n', encoding="utf-8")
    models = sorted({c["model"] for c in cells})

    def _m(m):
        rec = {"id": m, "voice_map": {}}
        if served:
            rec.update({"region": "us-central1" if m == GEM else "us",
                        "region_note": "note for " + m,
                        "served_from": ("Vertex AI · us-central1" if m == GEM
                                        else "ElevenLabs direct API · us")})
        return rec

    man = {
        "run_id": run_id, "modality": "voice", "started_at": "2026-09-10T10:00:00+0530",
        "scenario_count": 1,
        "scenarios": [{"id": sid, "task": "text_to_speech", "hash": "h"}],
        "models": [_m(m) for m in models],
        "judge": {"provider_model": "gemini-2.5-flash",
                  **({"region": "us-central1", "region_note": "judge note",
                      "served_from": "Vertex AI · us-central1"} if served else {})},
        "asr": {"provider_model": "medium",
                **({"region": "local", "region_note": "on the box",
                    "served_from": "local machine · no region"} if served else {})},
        "mos": {"predictor": "signal"},
        "calibration": {"passed": False, "reason": "not run"},
    }
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    with (d / "checks.jsonl").open("w") as ch, (d / "scores.jsonl").open("w") as sc, \
         (d / "telemetry.jsonl").open("w") as tl:
        for c in cells:
            key = {"scenario_id": sid, "model_id": c["model"]}
            _wav(d / "outputs" / "voice" / sid / f"{c['model']}.wav")
            gates = [{"gate": "decodes", "passed": True},
                     {"gate": "must_say", "passed": c.get("must_say", True)}]
            ch.write(json.dumps({**key, "gates": gates,
                                 "measurements": {"normalized_wer": c.get("wer", 0.02),
                                                  "duration_s": 0.3},
                                 "transcript_raw": "your order 4 4 1 9 ships today"}) + "\n")
            invalid = not c.get("must_say", True)
            sc.write(json.dumps({**key, "task": "text_to_speech",
                                 "status": "invalid" if invalid else "scored",
                                 "score": 0.0 if invalid else c["score"],
                                 "criterion_scores": {}, "calibration_trusted": False}) + "\n")
            tl.write(json.dumps({**key, "attempt": 1, "status": "ok", "latency_ms": 5000,
                                 "output": {"duration_s": 0.3},
                                 "cost": {"micro_usd": 40000, "usage_exact": True}}) + "\n")


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "runs"; r.mkdir()
    # Gemini's p1 clip was GATED by must_say - the case a homophone in the
    # transcript produces on a correct reading.
    _run(r, "2026-09-10_100000_voice-p1", [
        {"model": GEM, "score": 9.0, "must_say": False},
        {"model": EL, "score": 8.0}])
    _run(r, "2026-09-10_110000_voice-p2", [
        {"model": GEM, "score": 9.2},
        {"model": EL, "score": 8.4}])
    return r


def _review(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "review" / "human-review.yaml"
    p.parent.mkdir(exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


REVIEW = f"""
reviewers:
  - id: sai
    name: Sai Nadh
corrections:
  - scenario_id: vr-ecom-01
    model_id: {GEM}
    run_label: voice-p1
    field: gate
    gate: must_say
    was: false
    now: true
    reason: the clip says "ships today" clearly; the transcript wrote "chips today"
    reviewer: sai
    date: 2026-09-14
  - scenario_id: vr-ecom-01
    model_id: {EL}
    run_label: voice-p2
    field: score
    was: 8.4
    now: 7.0
    reason: the judge scored text_accuracy on a transcript that dropped a digit the clip did read
    reviewer: sai
    date: 2026-09-14
observations:
  - scenario_id: vr-ecom-01
    reviewer: sai
    date: 2026-09-14
    model_id: {GEM}
    notes: Digits are paced for writing down. Slight upward inflection on "today".
    preference: {GEM}
    tags: [pacing]
  - scenario_id: vr-ecom-01
    reviewer: sai
    date: 2026-09-14
    notes: Both clips are intelligible; neither sounds like a call-centre agent.
"""


# ------------------------------------------------------------- loading ---

def test_a_missing_review_file_is_the_normal_empty_state(tmp_path):
    r = load_review(tmp_path / "nope.yaml")
    assert r.empty and r.path is None
    assert load_review(None).empty


def test_a_correction_needs_its_reason_reviewer_and_date(tmp_path):
    for missing in ("reason", "reviewer", "date"):
        text = REVIEW.replace(f"    {missing}: ", f"    x_{missing}: ", 1)
        with pytest.raises(ReviewError, match=missing):
            load_review(_review(tmp_path, text))


def test_a_correction_field_must_be_one_the_instrument_measured(tmp_path):
    with pytest.raises(ReviewError, match="field"):
        load_review(_review(tmp_path, REVIEW.replace("field: gate", "field: vibe", 1)))


def test_an_unknown_reviewer_is_refused_when_reviewers_are_declared(tmp_path):
    with pytest.raises(ReviewError, match="reviewer 'ravi'"):
        load_review(_review(tmp_path, REVIEW.replace("reviewer: sai", "reviewer: ravi", 1)))


# ---------------------------------------------------------- correcting ---

def test_a_gate_correction_clears_the_clip_but_invents_no_score(root, tmp_path):
    runs, review, applied = load_runs_reviewed(root, "voice", _review(tmp_path, REVIEW))
    p1 = next(r for r in runs if r.label == "voice-p1")
    gem = next(c for c in p1.cells if c.model_id == GEM)
    assert gem.status == "unjudged" and gem.score is None, \
        "cleared on review, but the judge never heard it - nobody makes up its score"
    assert gem.gates_passed == 2 and all(g["passed"] for g in gem.gates)
    assert gem.corrected and gem.corrections[0]["was"] is False and gem.corrections[0]["now"] is True
    assert "cleared" in gem.review_note


def test_a_score_correction_keeps_the_original_beside_it(root, tmp_path):
    runs, _, applied = load_runs_reviewed(root, "voice", _review(tmp_path, REVIEW))
    p2 = next(r for r in runs if r.label == "voice-p2")
    el = next(c for c in p2.cells if c.model_id == EL)
    assert el.score == 7.0 and el.corrections[0]["was"] == 8.4
    # And the rollup is over the CORRECTED score.
    m = {m.model_id: m for m in rollup_models(runs)}
    assert m[EL].mean_score == pytest.approx((8.0 + 7.0) / 2)


def test_no_review_renders_the_instrument_untouched(root, tmp_path):
    _review(tmp_path, REVIEW)
    runs, review, applied = load_runs_reviewed(root, "voice", None)
    assert review.empty and applied == []
    p2 = next(r for r in runs if r.label == "voice-p2")
    assert next(c for c in p2.cells if c.model_id == EL).score == 8.4


def test_a_correction_that_matches_no_clip_is_refused_not_skipped(root, tmp_path):
    text = REVIEW.replace("run_label: voice-p1", "run_label: voice-p9", 1)
    with pytest.raises(ReviewError, match="matches no clip"):
        load_runs_reviewed(root, "voice", _review(tmp_path, text))


def test_a_correction_naming_a_gate_the_clip_does_not_have_is_refused(root, tmp_path):
    text = REVIEW.replace("gate: must_say", "gate: no_clipping", 1)
    with pytest.raises(ReviewError, match="no gate named"):
        load_runs_reviewed(root, "voice", _review(tmp_path, text))


def test_a_correction_written_against_a_different_run_is_refused(root, tmp_path):
    """`was` is a guard: if the run says something else, the file is stale."""
    text = REVIEW.replace("was: 8.4", "was: 9.9", 1)
    with pytest.raises(ReviewError, match="written against a different run|recorded score"):
        load_runs_reviewed(root, "voice", _review(tmp_path, text))


def test_a_human_cannot_score_a_clip_the_judge_never_scored(root, tmp_path):
    """A score correction on a gated clip would be a human score wearing the judge's badge."""
    text = REVIEW.replace("field: gate\n    gate: must_say\n    was: false\n    now: true",
                          "field: score\n    now: 9.5")
    with pytest.raises(ReviewError, match="no automated score to correct"):
        load_runs_reviewed(root, "voice", _review(tmp_path, text))


def test_a_gate_failed_on_review_invalidates_the_clip(root, tmp_path):
    text = f"""
corrections:
  - {{scenario_id: vr-ecom-01, model_id: {EL}, run_label: voice-p2, field: gate,
     gate: must_say, now: false, reason: it says "chips", reviewer: sai, date: 2026-09-14}}
"""
    runs, _, _ = load_runs_reviewed(root, "voice", _review(tmp_path, text))
    p2 = next(r for r in runs if r.label == "voice-p2")
    el = next(c for c in p2.cells if c.model_id == EL)
    assert el.status == "invalid" and el.score is None
    assert {m.model_id: m for m in rollup_models(runs)}[EL].invalid == 1


# ---------------------------------------------------------- observing ---

def test_observations_move_no_number(root, tmp_path):
    """The whole point. Same runs, with and without the observations."""
    obs_only = REVIEW.split("observations:")[0].replace("corrections:", "corrections: []\n_ignored:") \
        + "observations:" + REVIEW.split("observations:")[1]
    bare = build(root, "voice")
    with_obs = build(root, "voice", review_path=_review(tmp_path, obs_only))
    assert with_obs["human"]["n_observations"] == 2 and with_obs["human"]["n_corrections"] == 0
    for key in ("metrics", "strip", "overall_verdict", "gemini_wins", "other_wins"):
        assert with_obs[key] == bare[key], f"{key} moved because a human wrote a note"
    a, b = bare["scenarios"][0], with_obs["scenarios"][0]
    assert (a["winner"], a["gap"], a["verdict"]) == (b["winner"], b["gap"], b["verdict"])
    assert b["human"] and not a["human"]


def test_the_human_block_is_beside_the_verdict_not_inside_it(root, tmp_path):
    ctx = build(root, "voice", review_path=_review(tmp_path, REVIEW))
    s = ctx["scenarios"][0]
    notes = [o["notes"] for o in s["human"]]
    assert any("paced for writing down" in n for n in notes)
    assert s["human"][0]["preference"] == GEM
    # The page-level table puts the automated verdict and the preference side by side.
    row = ctx["human"]["per_scenario"][0]
    assert row["id"] == "vr-ecom-01" and row["preferences"] == [GEM]
    assert row["automated"]  # whatever the verdict is, it is stated, not merged


# ----------------------------------------------------------- rendering ---

def test_both_pages_mark_corrections_and_keep_the_original(root, tmp_path):
    rp = _review(tmp_path, REVIEW)
    client = render_client_report(root, "voice", review_path=rp).read_text(encoding="utf-8")
    board = render_dashboard(root, "voice", review_path=rp).read_text(encoding="utf-8")
    for html in (client, board):
        assert "corrected" in html
        assert "chips today" in html, "the reason travels to the page"
        assert "8.4" in html and "7.0" in html, "original and corrected, both"
        assert "Human review" in html
        assert "paced for writing down" in html
        assert "not part of" in html, "the block says it is not part of the score"
    assert 'id="human"' in client and 'id="human"' in board


def test_the_review_summary_names_who_and_when(root, tmp_path):
    runs, review, applied = load_runs_reviewed(root, "voice", _review(tmp_path, REVIEW))
    ctx = review_context(review, applied)
    assert ctx["reviewer_names"] == "Sai Nadh" and ctx["date_span"] == "2026-09-14"
    assert ctx["n_corrections"] == 2 and ctx["n_cells_corrected"] == 2
    assert ctx["by_field"] == {"gate": 1, "wer": 0, "score": 1}


# ------------------------------------------------------------- region ---

def test_where_each_model_was_served_from_is_on_both_pages(root):
    served = served_rows(load_runs(root, "voice"))
    by = {r["model_id"]: r for r in served["models"]}
    assert by[GEM]["served_from"] == "Vertex AI · us-central1" and by[GEM]["source"] == "manifest"
    assert by[EL]["region"] == "us" and not served["from_config"]
    assert served["judge"]["served_from"] == "Vertex AI · us-central1"
    client = render_client_report(root, "voice").read_text(encoding="utf-8")
    board = render_dashboard(root, "voice").read_text(encoding="utf-8")
    for html in (client, board):
        assert "Vertex AI · us-central1" in html and "ElevenLabs direct API · us" in html
        assert "note for " + EL in html


def test_a_run_that_predates_the_field_reads_the_config_and_says_so(tmp_path):
    r = tmp_path / "runs"; r.mkdir()
    _run(r, "2026-09-05_100000_voice-p1", [{"model": GEM, "score": 9.0},
                                           {"model": EL, "score": 8.0}], served=False)
    served = served_rows(load_runs(r, "voice"))
    by = {x["model_id"]: x for x in served["models"]}
    # configs/models.yaml knows both of these arms.
    assert by[GEM]["source"] == "config" and by[GEM]["region"] == "us-central1"
    assert by[EL]["source"] == "config" and by[EL]["region"] == "us"
    assert served["from_config"]
    html = render_client_report(r, "voice").read_text(encoding="utf-8")
    assert "predate" in html, "the page says the region was read back, not recorded"


def test_an_arm_the_config_does_not_know_is_not_recorded_rather_than_guessed(tmp_path):
    r = tmp_path / "runs"; r.mkdir()
    _run(r, "2026-09-05_100000_voice-p1", [{"model": "mystery-tts", "score": 9.0},
                                           {"model": EL, "score": 8.0}], served=False)
    by = {x["model_id"]: x for x in served_rows(load_runs(r, "voice"))["models"]}
    assert by["mystery-tts"]["served_from"] == "not recorded"
    assert by["mystery-tts"]["source"] == "unknown"


def test_the_manifest_records_region_for_every_arm_the_judge_and_the_asr():
    """The run record answers the question itself from now on."""
    from runner.models import load_registry

    reg = load_registry(Path(__file__).resolve().parent.parent / "configs")
    for m in reg.models:
        if m.modality != "voice":
            continue
        assert m.region and m.region_note, f"{m.id} has no region on record"
        assert m.served_from
    assert reg.judges["voice"].region == "us-central1"
    assert reg.asr.region == "local" and "no region" in reg.asr.served_from
    live = [m for m in reg.models if m.enabled and m.modality == "voice"]
    assert {m.served_from for m in live} == {"Vertex AI · us-central1",
                                              "ElevenLabs direct API · us"}
    # And the manifest writer carries all three.
    src = (Path(__file__).resolve().parent.parent / "runner" / "cli.py").read_text()
    assert src.count('"served_from"') >= 3


def test_the_clip_macro_shows_cleared_on_review_not_gate_failed(root, tmp_path):
    html = render_client_report(root, "voice", review_path=_review(tmp_path, REVIEW)) \
        .read_text(encoding="utf-8")
    assert "cleared on review, not scored" in html
    assert re.search(r"cleared its gates on human review", html)
