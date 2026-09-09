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
    """batches/video-v1.xlsx is the CATALOGUE of all 60 rows; bank-video/,
    bank-video-pending/ and bank-video-unwired/ are the runnable sets and
    together cover it exactly. The industry map covers the same 60 ids.

    The catalogue sheet is read as data, not through load_scenarios: since
    2026-09-09 image_to_video and video_edit are buildable, so constructing a
    runnable Scenario for those rows REQUIRES the input asset, and the flat
    sheet (id/task/title/prompt/expected/required_text/tags) has nowhere to
    put one. Rejecting them there is correct — you cannot run an edit with no
    source clip — so coverage is checked against the rows themselves.
    """
    import openpyxl
    import yaml
    wb = openpyxl.load_workbook(
        REPO_ROOT / "scenarios" / "batches" / "video-v1.xlsx",
        read_only=True, data_only=True)
    rows = list(wb["scenarios"].iter_rows(values_only=True))
    hdr = [str(c) for c in rows[0]]
    xl = [dict(zip(hdr, r)) for r in rows[1:]]
    assert len(xl) == 60
    families = {}
    for r in xl:
        fam = str(r["tags"]).split(",")[0].strip()
        families[fam] = families.get(fam, 0) + 1
    assert len(families) == 6 and all(n == 10 for n in families.values())

    bank = load_scenarios(REPO_ROOT / "scenarios" / "bank-video", modality="video")
    pending = load_scenarios(REPO_ROOT / "scenarios" / "bank-video-pending",
                             modality="video")
    # Asset-fed scenarios whose inputs do not exist yet are parked in
    # bank-video-unwired/ (see its README). They are read as raw YAML here
    # because they deliberately cannot be loaded: a buildable asset-fed task
    # with no asset is rejected, which is the whole reason they are parked.
    unwired = [yaml.safe_load(f.read_text()) for f in
               sorted((REPO_ROOT / "scenarios" / "bank-video-unwired").glob("*.yaml"))]
    assert len(bank) == 20 and len(pending) + len(unwired) == 40
    xl_ids = {r["id"] for r in xl}
    assert ({s.id for s in bank} | {s.id for s in pending}
            | {d["id"] for d in unwired}) == xl_ids
    imap = yaml.safe_load((REPO_ROOT / "configs" / "industry_map.yaml").read_text())
    assert set(imap["scenarios"]) == xl_ids
    assert all(v["primary"] for v in imap["scenarios"].values())
    # Ads is a use case under e-commerce/retail now, not an industry of its own
    # (4 Sep review) — it must not appear as primary or as an 'also'
    for v in imap["scenarios"].values():
        assert v["primary"] != "Ads"
        assert "Ads" not in (v.get("also") or [])
    # the v1 bank is text_to_video only; the asset-fed families live outside it
    assert {s.task for s in bank} == {"text_to_video"}
    assert ({s.task for s in pending} | {d["task"] for d in unwired}
            ) >= {"image_to_video", "avatar_dialogue", "video_edit"}


def test_pending_inputs_are_frozen_assets_with_provenance():
    """Every declared input exists on disk next to a JSON sidecar whose sha256
    matches the bytes — a phantom or silently-replaced asset fails here, not
    mid-run."""
    import hashlib
    import json
    pending = load_scenarios(REPO_ROOT / "scenarios" / "bank-video-pending",
                             modality="video")
    wired = 0
    for s in pending:
        for role, rel in (s.inputs or {}).items():
            f = REPO_ROOT / rel
            assert f.exists(), f"{s.id} {role}: {rel} missing"
            side = json.loads(f.with_suffix(".json").read_text())
            assert side["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest(), \
                f"{s.id} {role}: sidecar sha256 does not match {rel}"
            assert side.get("generated_by") or side.get("reused_from") \
                or side.get("source"), f"{s.id} {role}: sidecar has no provenance"
            wired += 1
    assert wired >= 17
    ava4 = next(s for s in pending if s.id == "VID-AVA-04")
    assert ava4.input.get("language") == "hi" and ava4.input.get("script")


def test_unwired_scenarios_are_parked_not_stubbed():
    """The parked set must genuinely lack its assets. A stub file here would
    silently turn a missing product still into a comparison of the wrong
    thing, which is worse than a scenario that cannot run."""
    import yaml
    from runner.lifecycle import BUILD_TASKS
    files = sorted((REPO_ROOT / "scenarios" / "bank-video-unwired").glob("*.yaml"))
    assert files, "the parked set should not be empty while assets are missing"
    for f in files:
        d = yaml.safe_load(f.read_text())
        needed = BUILD_TASKS.get(d["task"], {}).get("inputs", [])
        assert needed, f"{d['id']}: parked but its task needs no asset"
        for role in needed:
            rel = (d.get("inputs") or {}).get(role)
            assert rel is None or not (REPO_ROOT / rel).exists(), \
                f"{d['id']} has its {role} asset — move it to bank-video-pending/"
        # and it must genuinely be unloadable, which is why it is parked
        with pytest.raises(Exception, match="input asset"):
            load_scenarios(f)
    readme = REPO_ROOT / "scenarios" / "bank-video-unwired" / "README.md"
    assert readme.exists(), "the parked set must say what each scenario needs"
    body = readme.read_text()
    for f in files:
        sid = yaml.safe_load(f.read_text())["id"]
        assert sid in body, f"{sid} is parked but not listed in the README"


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


def test_reserved_video_task_is_legal_in_schema(tmp_path):
    """avatar_dialogue stays reserved: schema-legal, not buildable."""
    f = tmp_path / "avatar_dialogue.yaml"
    f.write_text("id: x-ava\nmodality: video\ntask: avatar_dialogue\n"
                 "prompt: p\nexpected: e\n")
    assert load_scenarios(f)[0].task == "avatar_dialogue"


def test_built_asset_fed_tasks_demand_their_input(tmp_path):
    """image_to_video and video_edit became buildable on 2026-09-09. A
    buildable asset-fed task without its asset must be REJECTED at load,
    not run against the prompt alone — a clip generated from the brief
    while ignoring the product still is not the comparison we claimed."""
    for task, role in (("image_to_video", "reference"), ("video_edit", "source")):
        f = tmp_path / f"{task}.yaml"
        f.write_text(f"id: x-{task}\nmodality: video\ntask: {task}\n"
                     f"prompt: p\nexpected: e\n")
        with pytest.raises(Exception, match=f"role '{role}'"):
            load_scenarios(f)


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
        assert m.supports == ["text_to_video", "image_to_video", "video_edit"]
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
