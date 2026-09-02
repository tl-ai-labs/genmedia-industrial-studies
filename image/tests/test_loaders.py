import textwrap

import pytest

from runner.loaders import (Scenario, effective_criteria, enabled_models,
                            load_models, load_rubric, load_scenarios)
from tests.conftest import REPO_ROOT


def test_shipped_scenarios_load():
    scenarios = load_scenarios(REPO_ROOT / "scenarios", modality="image")
    assert {s.id for s in scenarios} == {"img-001", "img-002", "img-003"}
    s1 = next(s for s in scenarios if s.id == "img-001")
    assert s1.task == "text_to_image"
    assert s1.checks["must_read_text"] == "TRAILHEAD 750"
    assert abs(sum(s1.weights.values()) - 1.0) < 1e-9


def test_weights_not_summing_to_one_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(textwrap.dedent("""
        id: bad-001
        modality: image
        task: text_to_image
        prompt: "x"
        expected: "x"
        criteria: [prompt_adherence, visual_quality]
        weights: {prompt_adherence: 0.5, visual_quality: 0.4}
    """))
    with pytest.raises(Exception, match="sum"):
        load_scenarios(bad)


def test_unknown_task_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nmodality: image\ntask: make_magic\nprompt: p\nexpected: e\n")
    with pytest.raises(Exception, match="unknown task"):
        load_scenarios(bad)


def test_task_modality_mismatch_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nmodality: voice\ntask: text_to_image\nprompt: p\nexpected: e\n")
    with pytest.raises(Exception, match="belongs to modality"):
        load_scenarios(bad)


def test_reserved_task_is_legal_in_schema(tmp_path):
    ok = tmp_path / "ok.yaml"
    ok.write_text("id: x\nmodality: image\ntask: reference_style\nprompt: p\nexpected: e\n")
    assert load_scenarios(ok)[0].task == "reference_style"


def test_csv_loader(tmp_path):
    csv_file = tmp_path / "batch.csv"
    csv_file.write_text(
        "id,task,prompt,expected,required_text\n"
        'sheet-1,text_to_image,"A red mug on a desk","Red mug centred",MUG CO\n'
        'sheet-2,text_to_image,"A blue sky","Blue sky only",\n')
    scenarios = load_scenarios(csv_file)
    assert len(scenarios) == 2
    assert scenarios[0].modality == "image"
    assert scenarios[0].checks["must_read_text"] == "MUG CO"
    assert "must_read_text" not in scenarios[1].checks


def test_xlsx_loader(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "task", "prompt", "expected", "required_text", "title", "tags"])
    ws.append(["x-001", "text_to_image", "A red mug", "Red mug centred", "MUG CO",
               "Mug hero", "ecommerce, text"])
    ws.append(["x-002", "text_to_image", "A blue sky", "Blue sky only", "", "", ""])
    ws.append([None, None, None, None, None, None, None])   # blank row ignored
    path = tmp_path / "batch.xlsx"
    wb.save(path)

    scenarios = load_scenarios(path)
    assert [s.id for s in scenarios] == ["x-001", "x-002"]
    assert scenarios[0].checks["must_read_text"] == "MUG CO"
    assert scenarios[0].title == "Mug hero"
    assert scenarios[0].tags == ["ecommerce", "text"]
    assert "must_read_text" not in scenarios[1].checks


def test_xlsx_missing_column_rejected(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["id", "task", "prompt"])
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ValueError, match="missing columns"):
        load_scenarios(path)


def test_csv_missing_column_rejected(tmp_path):
    csv_file = tmp_path / "batch.csv"
    csv_file.write_text("id,task,prompt\nx,text_to_image,p\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_scenarios(csv_file)


def test_rubric_loads_and_hashes():
    r = load_rubric(REPO_ROOT / "configs" / "rubrics", "image", "text_to_image")
    assert len(r.rubric_hash) == 64
    assert abs(sum(c.weight for c in r.criteria) - 1.0) < 1e-9
    r2 = load_rubric(REPO_ROOT / "configs" / "rubrics", "image", "text_to_image")
    assert r.rubric_hash == r2.rubric_hash  # stable


def test_effective_weights_redistribute_without_text():
    rubric = load_rubric(REPO_ROOT / "configs" / "rubrics", "image", "text_to_image")
    s = Scenario(id="x", modality="image", task="text_to_image",
                 prompt="p", expected="e", checks={})   # no must_read_text
    crits = effective_criteria(rubric, s)
    names = {c.name for c in crits}
    assert "text_accuracy" not in names
    assert abs(sum(c.weight for c in crits) - 1.0) < 1e-9
    # 0.35 / 0.85 proportional redistribution
    pa = next(c for c in crits if c.name == "prompt_adherence")
    assert abs(pa.weight - 0.35 / 0.85) < 1e-9


def test_scenario_cannot_invent_criteria():
    rubric = load_rubric(REPO_ROOT / "configs" / "rubrics", "image", "text_to_image")
    s = Scenario(id="x", modality="image", task="text_to_image", prompt="p",
                 expected="e", checks={}, criteria=["sparkle"], weights={"sparkle": 1.0})
    with pytest.raises(ValueError, match="not defined"):
        effective_criteria(rubric, s)


def test_shipped_models_config():
    mf = load_models(REPO_ROOT / "configs" / "models.yaml")
    image = enabled_models(mf, "image")
    # enabled set varies with the comparison being run; every known block
    # must keep existing, and at least one model must be live
    assert len(image) >= 1
    all_ids = {m.id for m in mf.image}
    assert {"gemini-3-1-flash-image", "gemini-3-1-flash-image-vertex",
            "gemini-3-pro-image-vertex", "gpt-image-1-medium",
            "gpt-image-2-medium", "gpt-image-2-high"} <= all_ids
    all_image_ids = {m.id for m in mf.image}
    assert {"gemini-3-1-flash-image", "gpt-image-1-medium"} <= all_image_ids
    for m in mf.image:
        assert m.price.source and m.price.as_of
        assert m.limits.max_concurrency >= 1
    assert mf.judge["image"].temperature == 0
    assert mf.judge["image"].vertex is not None
    assert enabled_models(mf, "voice") == []  # phase 2 blocks exist but are off
