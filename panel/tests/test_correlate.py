import json
import math

import pytest

from conftest import ELEV, GEM, GPT, GTTS, OMNI, SEED

from runner.correlate import correlate, dedupe, reference_model, render_markdown
from runner.stats import cohen_kappa, spearman, wilson


# ------------------------------------------------------------------ stats

def test_spearman_known_values():
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # ties get average ranks: (1,1,2) vs (1,2,3)
    r = spearman([1, 1, 2], [1, 2, 3])
    assert r == pytest.approx(math.sqrt(3) / 2, abs=1e-9)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2], [1, 2]) is None


def test_kappa_known_values():
    assert cohen_kappa(list("aabb"), list("aabb")) == pytest.approx(1.0)
    assert cohen_kappa(list("aabb"), list("bbaa")) == pytest.approx(-1.0)
    assert cohen_kappa(list("aabb"), list("abab")) == pytest.approx(0.0)
    assert cohen_kappa(list("aaaa"), list("aaaa")) is None
    assert cohen_kappa([], []) is None


def test_wilson_interval():
    lo, hi = wilson(7, 10)
    assert 0.39 < lo < 0.40 and 0.88 < hi < 0.90
    assert wilson(0, 0) is None
    assert wilson(0, 5)[0] == 0.0 and wilson(5, 5)[1] == 1.0


# -------------------------------------------------------------- correlate

def _vote(lane, run, sid, reviewer, picked, left, right):
    return {"ts": "2026-09-09T00:00:00Z", "lane": lane, "run_id": run, "scenario_id": sid,
            "item": f"{lane}-{sid}", "reviewer": reviewer,
            "pick": "tie" if picked is None else ("left" if picked == left else "right"),
            "picked_model": picked, "left_model": left, "right_model": right}


def _write(path, votes):
    path.write_text("".join(json.dumps(v) + "\n" for v in votes))


def test_reference_model_prefers_the_gemini_arm():
    assert reference_model([GPT, GEM]) == GEM
    assert reference_model([SEED, OMNI]) == OMNI
    assert reference_model(["b-model", "a-model"]) == "a-model"


def test_dedupe_keeps_last_vote_per_reviewer_item():
    a = _vote("image", "r", "S", "sai", GEM, GEM, GPT)
    b = _vote("image", "r", "S", "sai", GPT, GEM, GPT)
    assert dedupe([a, b]) == [b]
    assert len(dedupe([a, _vote("image", "r", "S", "kim", GEM, GEM, GPT)])) == 2


def test_agreement_per_lane_and_overall(fake_repo, tmp_path):
    img, vid, voc = "2026-09-01_000000_image", "2026-09-03_000000_video", "2026-09-05_000000_voice"
    votes = []
    # IMG-TXT-01: judge says GPT (8.0 vs 9.0). Panel 3-1 GPT -> agree.
    for r, pick in (("a", GPT), ("b", GPT), ("c", GPT), ("d", GEM), ("e", None)):
        votes.append(_vote("image", img, "IMG-TXT-01", r, pick, GEM, GPT))
    # IMG-BRAND-04: judge tie (9.1 vs 8.9 inside 0.5). Panel 3-0 GEM -> 3-way disagree, not "decided".
    for r in ("a", "b", "c"):
        votes.append(_vote("image", img, "IMG-BRAND-04", r, GEM, GPT, GEM))
    # VID-CIN-01: judge says Seedance (7.45 vs 9.0). Panel 2-1 Omni -> disagree.
    for r, pick in (("a", OMNI), ("b", OMNI), ("c", SEED)):
        votes.append(_vote("video", vid, "VID-CIN-01", r, pick, OMNI, SEED))
    # voice: judge tie (0.80 vs 0.82 inside 0.05). Panel 1-1 -> tie -> 3-way agree.
    votes.append(_vote("voice", voc, "vr-ecom-06#bare", "a", GTTS, GTTS, ELEV))
    votes.append(_vote("voice", voc, "vr-ecom-06#bare", "b", ELEV, GTTS, ELEV))
    vp = tmp_path / "votes.jsonl"
    _write(vp, votes)

    overrides = {("image", img): fake_repo["image"], ("video", vid): fake_repo["video"],
                 ("voice", voc): fake_repo["voice"]}
    rep = correlate(vp, key_path=None, overrides=overrides)

    L = rep["lanes"]
    assert set(L) == {"image", "video", "voice"}
    assert L["image"]["n_scenarios"] == 2 and L["image"]["n_votes"] == 8
    assert L["image"]["n_reviewers"] == 5
    assert L["image"]["n_decided"] == 1 and L["image"]["agree_decided"] == 1.0
    assert L["image"]["agree_3way"] == 0.5 and L["image"]["n_3way"] == 2
    assert L["image"]["cant_tell_share"] == pytest.approx(1 / 8)
    assert L["image"]["tie_band"] == 0.5
    assert L["video"]["n_decided"] == 1 and L["video"]["agree_decided"] == 0.0
    assert L["voice"]["n_decided"] == 0 and L["voice"]["agree_decided"] is None
    assert L["voice"]["agree_3way"] == 1.0 and L["voice"]["tie_band"] == 0.05

    O = rep["overall"]
    assert O["n_scenarios"] == 4 and O["n_decided"] == 2
    assert O["agree_decided"] == 0.5 and O["agree_decided_ci"] is not None
    assert O["agree_3way"] == 0.5 and O["n_3way"] == 4
    assert O["n_reviewers"] == 5

    rows = {(r["lane"], r["scenario_id"]): r for r in rep["scenarios"]}
    txt = rows[("image", "IMG-TXT-01")]
    assert txt["ref_model"] == GEM and txt["votes_ref"] == 1 and txt["votes_other"] == 3 and txt["votes_tie"] == 1
    assert txt["human"] == "other" and txt["judge_verdict"] == "other" and txt["agree"] is True
    assert txt["human_margin"] == pytest.approx(-0.5) and txt["judge_delta"] == pytest.approx(-1.0)
    brand = rows[("image", "IMG-BRAND-04")]
    assert brand["human"] == "ref" and brand["judge_verdict"] == "tie" and brand["agree"] is False
    vidr = rows[("video", "VID-CIN-01")]
    assert vidr["ref_model"] == OMNI and vidr["human"] == "ref" and vidr["judge_verdict"] == "other"
    assert rep["notes"] == []

    md = render_markdown(rep)
    assert "| **image**" in md and "| **overall**" in md and "IMG-TXT-01" in md
    assert "**no**" in md   # the disagreements are printed, not smoothed


def test_tie_band_override_changes_the_judge_verdict(fake_repo, tmp_path):
    img = "2026-09-01_000000_image"
    vp = tmp_path / "v.jsonl"
    _write(vp, [_vote("image", img, "IMG-BRAND-04", "a", GEM, GEM, GPT)])
    ov = {("image", img): fake_repo["image"]}
    assert correlate(vp, overrides=ov)["scenarios"][0]["judge_verdict"] == "tie"
    assert correlate(vp, overrides=ov, tie_bands={"image": 0.1})["scenarios"][0]["judge_verdict"] == "ref"


def test_missing_score_is_no_score_not_a_crash(fake_repo, tmp_path):
    img = "2026-09-01_000000_image"
    vp = tmp_path / "v.jsonl"
    _write(vp, [_vote("image", img, "IMG-FAIL-01", "a", GEM, GEM, GPT)])
    rep = correlate(vp, overrides={("image", img): fake_repo["image"]})
    r = rep["scenarios"][0]
    assert r["judge_verdict"] == "no score" and r["agree"] is None and r["judge_delta"] is None
    assert rep["lanes"]["image"]["n_3way"] == 0


def test_unlocatable_run_is_a_clear_error(tmp_path):
    vp = tmp_path / "v.jsonl"
    _write(vp, [_vote("image", "no-such-run", "S", "a", GEM, GEM, GPT)])
    with pytest.raises(SystemExit, match="--run image:no-such-run="):
        correlate(vp)


def test_key_locates_runs(fake_repo, tmp_path):
    img = "2026-09-01_000000_image"
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"runs": {"image": {img: str(fake_repo["image"])}}, "items": {}}))
    vp = tmp_path / "v.jsonl"
    _write(vp, [_vote("image", img, "IMG-TXT-01", "a", GPT, GEM, GPT)])
    rep = correlate(vp, key_path=key)
    assert rep["scenarios"][0]["judge_verdict"] == "other"
