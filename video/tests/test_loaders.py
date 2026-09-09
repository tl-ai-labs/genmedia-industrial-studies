import textwrap

import pytest

from runner.loaders import (Scenario, effective_criteria, enabled_models,
                            load_models, load_rubric, load_scenarios)
from tests.conftest import REPO_ROOT


def test_shipped_smoke_scenarios_load():
    scenarios = load_scenarios(REPO_ROOT / "scenarios", modality="video")
    assert {s.id for s in scenarios} == {"vid-001-dolly-in", "vid-002-liquid-pour",
                                         "vid-003-bouncing-ball"}
    s1 = next(s for s in scenarios if s.id == "vid-001-dolly-in")
    assert s1.task == "text_to_video"
    assert s1.params["duration_s"] == 4
    assert s1.checks["min_duration_s"] == 3.5


def test_shipped_bank_loads_20_scenarios():
    scenarios = load_scenarios(REPO_ROOT / "scenarios" / "bank-video",
                               modality="video")
    ids = sorted(s.id for s in scenarios)
    assert len(ids) == 20
    assert ids[:2] == ["VID-CIN-01", "VID-CIN-02"]
    assert ids[-1] == "VID-PHY-10"
    for s in scenarios:
        assert s.task == "text_to_video"
        assert s.prompt and s.expected
        assert s.params["duration_s"] == 8
        assert s.params["resolution"] == "1080p"
        assert s.params["audio"] is False
        assert s.checks["max_duration_s"] == 9.0
        assert s.checks["min_width"] == 1280
        assert "scenario-bank" in s.tags
    # prompt is the sheet's, verbatim — spot-check the first row
    cin1 = next(s for s in scenarios if s.id == "VID-CIN-01")
    assert cin1.prompt == ("A slow dolly-in on a woman reading at a wooden table "
                           "by a window. Soft daylight. The camera moves steadily "
                           "forward for the full duration. No cuts.")
    # family duration floors: CIN pinned at 8s, PHY allows the sheet's 5-8s
    assert cin1.checks["min_duration_s"] == 7.0
    phy1 = next(s for s in scenarios if s.id == "VID-PHY-01")
    assert phy1.checks["min_duration_s"] == 4.5


def test_every_bank_scenario_maps_to_an_industry():
    import yaml
    imap = yaml.safe_load((REPO_ROOT / "configs" / "industry_map.yaml").read_text())
    scenarios = load_scenarios(REPO_ROOT / "scenarios" / "bank-video")
    for s in scenarios:
        assert s.id in imap["scenarios"], s.id
        assert imap["scenarios"][s.id]["primary"]


def test_full_video_bank_extraction_covers_all_60():
    """batches/video-v1.xlsx mirrors image-v1.xlsx (loader format) and, with
    bank-video/ + bank-video-pending/, covers every video row of the sheet;
    the industry map covers the same 60 ids exactly."""
    import yaml
    xl = load_scenarios(REPO_ROOT / "scenarios" / "batches" / "video-v1.xlsx")
    assert len(xl) == 60
    families = {}
    for s in xl:
        families[s.tags[0]] = families.get(s.tags[0], 0) + 1
    assert len(families) == 6 and all(n == 10 for n in families.values())
    bank = load_scenarios(REPO_ROOT / "scenarios" / "bank-video", modality="video")
    pending = load_scenarios(REPO_ROOT / "scenarios" / "bank-video-pending",
                             modality="video")
    assert len(bank) == 20 and len(pending) == 40
    assert {s.id for s in bank} | {s.id for s in pending} == {s.id for s in xl}
    imap = yaml.safe_load((REPO_ROOT / "configs" / "industry_map.yaml").read_text())
    assert set(imap["scenarios"]) == {s.id for s in xl}
    assert all(v["primary"] for v in imap["scenarios"].values())
    # the v1 bank is text_to_video only; reserved tasks live in pending
    assert {s.task for s in bank} == {"text_to_video"}
    assert {s.task for s in pending} >= {"image_to_video", "avatar_dialogue", "video_edit"}


def test_pending_inputs_are_frozen_assets_with_provenance():
    """Every declared input exists on disk next to a JSON sidecar whose sha256
    matches the bytes — a phantom or silently-replaced asset fails here, not
    mid-run. VID-EDIT-10 is derived at run time and must stay unwired."""
    import hashlib
    import json
    pending = load_scenarios(REPO_ROOT / "scenarios" / "bank-video-pending",
                             modality="video")
    wired = 0
    for s in pending:
        if s.id == "VID-EDIT-10":
            assert not s.inputs
            continue
        for role, rel in s.inputs.items():
            f = REPO_ROOT / rel
            assert f.exists(), f"{s.id} {role}: {rel} missing"
            side = json.loads(f.with_suffix(".json").read_text())
            assert side["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest(), \
                f"{s.id} {role}: sidecar sha256 does not match {rel}"
            assert side.get("generated_by") or side.get("reused_from") \
                or side.get("source"), f"{s.id} {role}: sidecar has no provenance"
            wired += 1
    assert wired >= 30    # I2V + AD + AVA stills and the EDIT clips
    ava4 = next(s for s in pending if s.id == "VID-AVA-04")
    assert ava4.input.get("language") == "hi" and ava4.input.get("script")


def test_weights_not_summing_to_one_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(textwrap.dedent("""
        id: bad-001
        modality: video
        task: text_to_video
        prompt: "x"
        expected: "x"
        criteria: [prompt_adherence, visual_fidelity]
        weights: {prompt_adherence: 0.5, visual_fidelity: 0.4}
    """))
    with pytest.raises(Exception, match="sum"):
        load_scenarios(bad)


def test_unknown_task_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nmodality: video\ntask: make_magic\nprompt: p\nexpected: e\n")
    with pytest.raises(Exception, match="unknown task"):
        load_scenarios(bad)


def test_task_modality_mismatch_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nmodality: image\ntask: text_to_video\nprompt: p\nexpected: e\n")
    with pytest.raises(Exception, match="belongs to modality"):
        load_scenarios(bad)


def test_reserved_video_tasks_are_legal_in_schema(tmp_path):
    for task in ("image_to_video", "video_edit", "avatar_dialogue"):
        f = tmp_path / f"{task}.yaml"
        f.write_text(f"id: x-{task}\nmodality: video\ntask: {task}\n"
                     f"prompt: p\nexpected: e\n")
        assert load_scenarios(f)[0].task == task


def test_rubric_loads_and_hashes():
    r = load_rubric(REPO_ROOT / "configs" / "rubrics", "video", "text_to_video")
    assert len(r.rubric_hash) == 64
    assert abs(sum(c.weight for c in r.criteria) - 1.0) < 1e-9
    # both the base and the per-task file contribute to the hash
    assert len(r.source_files) == 2
    r2 = load_rubric(REPO_ROOT / "configs" / "rubrics", "video", "text_to_video")
    assert r.rubric_hash == r2.rubric_hash  # stable


def test_rubric_weights_match_the_plan():
    r = load_rubric(REPO_ROOT / "configs" / "rubrics", "video", "text_to_video")
    weights = {c.name: c.weight for c in r.criteria}
    assert weights == {"prompt_adherence": 0.30, "visual_fidelity": 0.15,
                       "temporal_consistency": 0.15, "motion_coherence": 0.15,
                       "physics_plausibility": 0.15, "technical_compliance": 0.10}
    tech = r.criterion("technical_compliance")
    assert tech.judged_by == "measured"
    assert all(r.criterion(n).judged_by == "judge"
               for n in weights if n != "technical_compliance")


def test_effective_criteria_keep_all_video_weights():
    rubric = load_rubric(REPO_ROOT / "configs" / "rubrics", "video", "text_to_video")
    s = Scenario(id="x", modality="video", task="text_to_video",
                 prompt="p", expected="e", checks={})
    crits = effective_criteria(rubric, s)
    assert {c.name for c in crits} == {c.name for c in rubric.criteria}
    assert abs(sum(c.weight for c in crits) - 1.0) < 1e-9


def test_scenario_cannot_invent_criteria():
    rubric = load_rubric(REPO_ROOT / "configs" / "rubrics", "video", "text_to_video")
    s = Scenario(id="x", modality="video", task="text_to_video", prompt="p",
                 expected="e", checks={}, criteria=["sparkle"], weights={"sparkle": 1.0})
    with pytest.raises(ValueError, match="not defined"):
        effective_criteria(rubric, s)


def test_shipped_models_config():
    mf = load_models(REPO_ROOT / "configs" / "models.yaml")
    all_ids = {m.id for m in mf.video}
    assert all_ids == {"veo-3-1-vertex", "sora-2", "omni-flash-vertex",
                       "seedance-2-5"}
    for m in mf.video:
        assert m.price.unit in ("per_second", "per_token")
        assert m.price.est_usd_per_call > 0   # pre-flight is never a silent 0
        assert m.price.source and m.price.as_of
        assert m.display
        assert m.supports == ["text_to_video"]
        assert m.limits.max_concurrency >= 1
    # which arms are enabled is the human's budget/scope call and varies
    # between the pristine build and the live working copy — never asserted
    # here; only the wiring is
    by_id = {m.id: m for m in mf.video}
    assert by_id["seedance-2-5"].auth_env == "ARK_API_KEY"
    assert by_id["seedance-2-5"].adapter == "seedance_video"
    assert by_id["omni-flash-vertex"].adapter == "omni_video"
    assert by_id["omni-flash-vertex"].vertex is not None
    veo = next(m for m in mf.video if m.id == "veo-3-1-vertex")
    assert veo.vertex.project == "ai-studies-console" and veo.auth_env is None
    assert veo.provider_model == "veo-3.1-generate-001"
    sora = next(m for m in mf.video if m.id == "sora-2")
    assert sora.auth_env == "OPENAI_API_KEY" and sora.vertex is None
    assert sora.provider_model == "sora-2-2025-12-08"
    assert mf.judge["video"].temperature == 0
    assert mf.judge["video"].vertex is not None
