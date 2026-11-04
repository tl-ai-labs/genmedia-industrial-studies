"""`merge-key`: a dated round's items added to the vote store's live key."""
import json

import pytest

from runner.cli import main, merge_keys

BASE = {"generated": "2026-10-12T00:00:00Z", "salt": "live",
        "runs": {"image": {"2026-10-09_225158_image": "image/runs/2026-10-09_225158_image"}},
        "items": {"aaaaaaaaaa": {"lane": "image", "run_id": "2026-10-09_225158_image", "scenario_id": "S1",
                                 "media": {"item-1.jpg": "gemini-x", "item-2.jpg": "gpt-y"}}}}
ROUND = {"generated": "2026-11-04T00:00:00Z", "round": "2026-11-03", "salt": "round",
         "runs": {"image": {"2026-11-03_115853_image": "image/runs/2026-11-03_115853_image"}},
         "items": {"bbbbbbbbbb": {"lane": "image", "run_id": "2026-11-03_115853_image", "scenario_id": "S1",
                                  "media": {"item-3.jpg": "gemini-x", "item-4.jpg": "gpt-y"}}}}


def test_merge_adds_the_round_and_keeps_the_live_key(tmp_path):
    b, r, out = tmp_path / "base.json", tmp_path / "round.json", tmp_path / "merged.json"
    b.write_text(json.dumps(BASE))
    r.write_text(json.dumps(ROUND))
    assert main(["merge-key", "--base", str(b), "--add", str(r), "--out", str(out)]) == 0
    m = json.loads(out.read_text())
    assert m["salt"] == "live"
    assert m["items"]["aaaaaaaaaa"] == BASE["items"]["aaaaaaaaaa"]
    assert m["items"]["bbbbbbbbbb"]["run_id"] == "2026-11-03_115853_image"
    assert set(m["runs"]["image"]) == {"2026-10-09_225158_image", "2026-11-03_115853_image"}
    assert m["rounds"]["2026-11-03"]["salt"] == "round"


def test_merge_is_idempotent_and_refuses_a_conflicting_item():
    once = merge_keys(BASE, ROUND)
    assert merge_keys(once, ROUND)["items"] == once["items"]
    clash = json.loads(json.dumps(ROUND))
    clash["items"]["aaaaaaaaaa"] = {**BASE["items"]["aaaaaaaaaa"], "media": {"item-9.jpg": "gemini-x", "item-2.jpg": "gpt-y"}}
    with pytest.raises(SystemExit):
        merge_keys(BASE, clash)
