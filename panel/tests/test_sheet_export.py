"""The Google Sheet round trip: `sheet-export` writes the two tabs the Apps
Script reads, and the correlation reads the votes tab back as CSV."""
import csv
import json

from runner.cli import main
from runner.correlate import load_votes
from runner.export import export


def test_sheet_export_writes_key_and_votes_tabs(fake_repo, tmp_path):
    dist, private = tmp_path / "dist", tmp_path / "private"
    export([("image", fake_repo["image"])], dist=dist, private=private, salt="fixed")
    key = json.loads((private / "key.json").read_text())
    iid, it = next(iter(key["items"].items()))
    media = list(it["media"])
    votes = tmp_path / "votes.jsonl"
    old = {"ts": "2026-09-09T12:00:00.000Z", "lane": "image", "run_id": it["run_id"], "scenario_id": it["scenario_id"],
           "item": iid, "reviewer": "Sai", "pick": "right", "picked_model": it["media"][media[1]],
           "left_model": it["media"][media[0]], "right_model": it["media"][media[1]]}
    new = {"ts": "2026-09-11T10:00:00.000Z", "reviewer": "Priya", "lane": "image", "run_id": it["run_id"],
           "scenario_id": it["scenario_id"], "picked": None, "over": None, "reason": "both fine"}
    votes.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
    out_key, out_votes = tmp_path / "key.csv", tmp_path / "votes.csv"
    assert main(["sheet-export", "--key", str(private / "key.json"), "--votes", str(votes),
                 "--out-key", str(out_key), "--out-votes", str(out_votes)]) == 0

    rows = list(csv.DictReader(out_key.open(newline="")))
    assert len(rows) == sum(len(x["media"]) for x in key["items"].values())
    mine = [r for r in rows if r["item"] == iid]
    assert {r["media"]: r["model"] for r in mine} == it["media"]
    assert mine[0]["scenario_id"] == it["scenario_id"] and mine[0]["run_id"] == it["run_id"]

    vrows = list(csv.DictReader(out_votes.open(newline="")))
    assert [r["reviewer"] for r in vrows] == ["Sai", "Priya"]
    assert vrows[0]["picked"] == it["media"][media[1]] and vrows[0]["over"] == it["media"][media[0]]   # old shape resolved
    assert vrows[1]["picked"] == "" and vrows[1]["reason"] == "both fine"                             # tie, empty cells

    # ...and the correlation reads the sheet's CSV back as votes.
    back = load_votes(out_votes)
    assert [v["picked_model"] for v in back] == [it["media"][media[1]], None]
    assert back[1]["pick"] == "tie" and back[0]["side_known"] is False
