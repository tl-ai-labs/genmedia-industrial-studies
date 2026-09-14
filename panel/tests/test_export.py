"""
The blinding test. Everything under dist/ - names and bytes - is walked for
every model id. If this test ever fails, the panel is not blind.
"""
import json
from pathlib import Path

from conftest import ALL_MODELS, GEM, GPT

from runner.export import export, format_summary
from runner.runs import load_run


def _export(fake_repo, tmp_path, salt="fixed"):
    dist, private = tmp_path / "dist", tmp_path / "private"
    s = export([("image", fake_repo["image"]), ("video", fake_repo["video"]),
                ("voice", fake_repo["voice"])], dist=dist, private=private, salt=salt)
    return s, dist, private


def test_nothing_public_names_a_model(fake_repo, tmp_path):
    s, dist, private = _export(fake_repo, tmp_path)
    leaks = []
    for p in dist.rglob("*"):
        if p.is_dir():
            continue
        rel = str(p.relative_to(dist))
        blob = p.read_bytes()
        for m in ALL_MODELS:
            if m in rel or m.encode() in blob:
                leaks.append((rel, m))
        # run ids and provider words too
        for word in (b"2026-09-01_000000_image", b"2026-09-03_000000_video",
                     b"2026-09-05_000000_voice", b"gemini", b"gpt", b"seedance",
                     b"omni", b"elevenlabs"):
            if word in blob.lower():
                leaks.append((rel, word.decode()))
    assert leaks == [], leaks
    # ...and the key does hold them, or the votes could never be resolved.
    key = json.loads((private / "key.json").read_text())
    resolved = {m for it in key["items"].values() for m in it["media"].values()}
    assert set(ALL_MODELS) <= resolved


def test_items_json_shape_and_pair_order_is_opaque(fake_repo, tmp_path):
    s, dist, private = _export(fake_repo, tmp_path)
    items = json.loads((dist / "items.json").read_text())
    assert items["lanes"] == ["image", "video", "voice"]
    assert s["n_items"] == 4 == len(items["items"])
    for it in items["items"]:
        assert set(it) == {"id", "lane", "scenario_id", "task", "title", "brief", "industry", "kind", "inputs", "pair"}
        assert len(it["pair"]) == 2 and it["pair"] == sorted(it["pair"])
        assert all(p.startswith("item-") for p in it["pair"])
        for p in it["pair"]:
            assert (dist / "media" / p).exists()
    brand = next(i for i in items["items"] if i["scenario_id"] == "IMG-BRAND-04")
    # bbox is not media; source + reference are, and are served under opaque names
    assert [i["label"] for i in brand["inputs"]] == ["source", "reference"]
    assert all(i["media"].startswith("input-") for i in brand["inputs"])
    voice = next(i for i in items["items"] if i["lane"] == "voice")
    assert voice["kind"] == "audio" and voice["brief"] == "Your reference is A B C."
    assert voice["scenario_id"] == "vr-ecom-06#bare"
    # industry comes from the lane's configs/industry_map.yaml, parent id for variants
    assert brand["industry"] == "Ecommerce & Retail" and voice["industry"] == "Ecommerce & Retail"
    assert next(i for i in items["items"] if i["scenario_id"] == "IMG-TXT-01")["industry"] == "Ads"


def test_pair_order_does_not_track_model(fake_repo, tmp_path):
    """Across many salts, the Gemini arm must land first about half the
    time - if it always did, 'first' would be a model label."""
    firsts = []
    for k in range(40):
        s, dist, private = _export(fake_repo, tmp_path / str(k), salt=f"s{k}")
        key = json.loads((private / "key.json").read_text())
        items = json.loads((dist / "items.json").read_text())
        txt = next(i for i in items["items"] if i["scenario_id"] == "IMG-TXT-01")
        firsts.append(key["items"][txt["id"]]["media"][txt["pair"][0]] == GEM)
    assert 8 < sum(firsts) < 32


def test_skips_and_warnings_are_reported(fake_repo, tmp_path):
    s, dist, private = _export(fake_repo, tmp_path)
    skipped = s["skipped"]["image:2026-09-01_000000_image"]
    assert skipped == [("IMG-FAIL-01", f"no media for {GPT}")]
    assert s["ext_mismatch"] == ["image:IMG-BRAND-04", "voice:vr-ecom-06#bare"]
    assert s["passthrough"] == []              # png, jpg, wav, mp3, mp4 all stripped
    text = format_summary(s)
    assert "skipped" in text and "WARNING" in text and "passed through" not in text


def test_video_prefers_preview_and_metadata_is_stripped(fake_repo, tmp_path):
    s, dist, private = _export(fake_repo, tmp_path)
    key = json.loads((private / "key.json").read_text())
    vid = next(v for v in key["items"].values() if v["lane"] == "video")
    for fname in vid["media"]:
        assert b"preview-" in (dist / "media" / fname).read_bytes()
        assert b"udta" not in (dist / "media" / fname).read_bytes()
    assert vid["judge"] == {"omni-flash-vertex": 7.45, "seedance-2-5": 9.0}


def test_reexport_wipes_dist_but_keeps_item_ids(fake_repo, tmp_path):
    s, dist, private = _export(fake_repo, tmp_path, salt="a")
    stale = dist / "media" / "item-stale.png"
    stale.write_bytes(b"x")
    before = {i["scenario_id"]: (i["id"], i["pair"]) for i in json.loads((dist / "items.json").read_text())["items"]}
    _export(fake_repo, tmp_path, salt="b")
    assert not stale.exists()
    after = {i["scenario_id"]: (i["id"], i["pair"]) for i in json.loads((dist / "items.json").read_text())["items"]}
    for sid in before:
        assert before[sid][0] == after[sid][0]          # item id: stable, names a scenario
        assert before[sid][1] != after[sid][1]          # media ids: salted, change every export


def test_load_run_reads_the_contract(fake_repo):
    data = load_run(fake_repo["image"])
    assert data.lane == "image" and data.run_id == "2026-09-01_000000_image"
    assert [p.scenario_id for p in data.pairs] == ["IMG-BRAND-04", "IMG-TXT-01"]
    p = data.pairs[1]
    assert {a.model_id for a in p.arms} == {GEM, GPT}
    assert p.kind == "image" and p.brief == "Design an event poster."
