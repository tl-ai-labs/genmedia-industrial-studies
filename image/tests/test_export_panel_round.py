"""tools/export_panel_round.py: a run exported as a dated blind-panel round.
The public half names no model or run, a set shows its frames keyed by
frame 1, and the key maps every voted file back to its model. Offline."""
import importlib.util
import json

import yaml
from PIL import Image

from tests.conftest import REPO_ROOT

spec = importlib.util.spec_from_file_location("export_panel_round", REPO_ROOT / "tools" / "export_panel_round.py")
epr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epr)

MODELS = ("gemini-nano-banana-2-1-vertex", "gpt-image-2-5-sunburst-high")
RUN_ID = "2026-11-03_000000_image"


def _png(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 40, 40)).save(path)


def _fake_run(tmp_path):
    run = tmp_path / RUN_ID
    (run / "scenarios").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"run_id": RUN_ID}))
    src = run / "inputs" / "EDIT-1" / "src-a.jpg"
    _png(src, (2000, 1000))
    scenarios = [
        {"id": "EDIT-1", "task": "image_edit", "title": "An edit", "prompt": "Make it blue.",
         "inputs": {"source": "assets/none/src-a.jpg"}, "meta": {"industry": "Advertising & agencies"}},
        {"id": "SET-1", "task": "text_to_image", "title": "A set", "prompt": "One hero, two scenes.",
         "output_set": {"mode": "each", "members": [{"name": "Dawn", "brief": "At dawn."}, {"name": "Dusk", "brief": "At dusk."}]},
         "meta": {"industry": "Film, OTT & micro-drama"}},
    ]
    scores = []
    for sc in scenarios:
        (run / "scenarios" / f"{sc['id']}.yaml").write_text(yaml.safe_dump(sc))
        for m in MODELS:
            _png(run / "outputs" / "image" / sc["id"] / f"{m}.png", (2400, 1200))
            scores.append({"scenario_id": sc["id"], "model_id": m, "score": 8.0})
            if "output_set" in sc:
                for i in (1, 2):
                    _png(run / "outputs" / "image" / sc["id"] / "frames" / f"{m}__f{i}.png", (1024, 1024))
    (run / "scores.jsonl").write_text("\n".join(json.dumps(r) for r in scores) + "\n")
    return run


def test_round_export_is_blind_and_keyed(tmp_path):
    run = _fake_run(tmp_path)
    out, key_path = tmp_path / "site" / "2026-11-03", tmp_path / "private" / "key-2026-11-03.json"
    summary = epr.export(run, "2026-11-03", out, key_path, salt="fixed")
    assert summary["items"] == 2 and summary["files"] == 2 + 4 + 1   # edit pair, set frames, one input

    items = {i["scenario_id"]: i for i in json.loads((out / "items.json").read_text())["items"]}
    key = json.loads(key_path.read_text())
    edit, sset = items["EDIT-1"], items["SET-1"]
    assert [i["label"] for i in edit["inputs"]] == ["source"]
    assert edit["pair"] == sorted(edit["pair"]) and "frames" not in edit
    assert sorted(sset["frames"]) == sset["pair"]                        # keyed by frame 1, the voted file
    assert [f["label"] for f in sset["frames"][sset["pair"][0]]] == ["1 · Dawn", "2 · Dusk"]
    assert sset["brief"].endswith("One image each:\n1. Dawn: At dawn.\n2. Dusk: At dusk.")

    for it in items.values():
        k = key["items"][it["id"]]
        assert k["run_id"] == RUN_ID and set(k["media"]) == set(it["pair"]) and set(k["media"].values()) == set(MODELS)
    with Image.open(out / "media" / edit["pair"][0]) as im:
        assert max(im.size) == epr.OUTPUT_PX and im.format == "JPEG"
    with Image.open(out / "media" / edit["inputs"][0]["media"]) as im:
        assert max(im.size) == epr.INPUT_PX
    public = (out / "items.json").read_text() + " ".join(p.name for p in (out / "media").iterdir())
    for tell in (*MODELS, RUN_ID, "gemini", "gpt"):
        assert tell not in public

    again = tmp_path / "again"
    epr.export(run, "2026-11-03", again, tmp_path / "k2.json", salt="fixed")
    assert sorted(p.name for p in (again / "media").iterdir()) == sorted(p.name for p in (out / "media").iterdir())
