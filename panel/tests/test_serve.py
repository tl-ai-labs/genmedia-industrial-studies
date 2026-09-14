import json
import threading
import urllib.error
import urllib.request

import pytest

from runner.export import export
from runner.serve import make_server


@pytest.fixture
def served(fake_repo, tmp_path):
    dist, private = tmp_path / "dist", tmp_path / "private"
    export([("image", fake_repo["image"]), ("voice", fake_repo["voice"])],
           dist=dist, private=private, salt="fixed")
    votes = tmp_path / "votes.jsonl"
    srv = make_server(dist, private / "key.json", votes, host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    key = json.loads((private / "key.json").read_text())
    items = json.loads((dist / "items.json").read_text())["items"]
    yield {"base": base, "votes": votes, "key": key, "items": items, "dist": dist}
    srv.shutdown()
    srv.server_close()


def _get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_vote_is_resolved_against_the_key(served):
    it = next(i for i in served["items"] if i["scenario_id"] == "IMG-TXT-01")
    left, right = it["pair"][1], it["pair"][0]           # reviewer saw them flipped
    st, body = _post(served["base"] + "/api/vote",
                     {"reviewer": "sai", "item": it["id"], "left": left, "right": right, "pick": "right",
                      "reason": "  crisper text  "})
    assert st == 200 and body["ok"] and body["total_votes"] == 1
    rec = json.loads(served["votes"].read_text().splitlines()[0])
    media = served["key"]["items"][it["id"]]["media"]
    assert rec["lane"] == "image" and rec["scenario_id"] == "IMG-TXT-01"
    assert rec["reviewer"] == "sai" and rec["reason"] == "crisper text"          # reason trimmed
    assert rec["picked"] == media[right] and rec["over"] == media[left]
    assert rec["run_id"] == "2026-09-01_000000_image" and rec["ts"].endswith("Z")
    # Exactly the fields asked for, nothing that names a side, an item or a person beyond the name.
    assert set(rec) == {"ts", "reviewer", "lane", "run_id", "scenario_id", "picked", "over", "reason"}


def test_tie_records_no_model(served):
    it = served["items"][0]
    st, _ = _post(served["base"] + "/api/vote",
                  {"reviewer": "a", "item": it["id"], "left": it["pair"][0], "right": it["pair"][1], "pick": "tie"})
    assert st == 200
    rec = json.loads(served["votes"].read_text().splitlines()[-1])
    assert rec["picked"] is None and rec["over"] is None
    assert rec["reason"] is None                                     # absent reason is null, not ""


@pytest.mark.parametrize("bad", [
    {"reviewer": "", "pick": "left"},
    {"reviewer": "x" * 65, "pick": "left"},
    {"reviewer": "ok", "item": "nope", "pick": "left"},
    {"reviewer": "ok", "pick": "up"},
    {"reviewer": "ok", "pick": "left", "swap_pair": True},
    {"reviewer": "ok", "pick": "left", "reason": "x" * 501},
])
def test_bad_votes_are_refused_and_not_written(served, bad):
    it = served["items"][0]
    other = served["items"][1]
    body = {"item": it["id"], "left": it["pair"][0], "right": it["pair"][1]}
    body.update({k: v for k, v in bad.items() if k != "swap_pair"})
    if bad.get("swap_pair"):
        body["right"] = other["pair"][0]                 # media from a different item
    st, resp = _post(served["base"] + "/api/vote", body)
    assert st == 400 and "error" in resp
    assert not served["votes"].exists()


def test_votes_by_reviewer_and_health(served):
    a, b = served["items"][0], served["items"][1]
    for it in (a, b, a):
        _post(served["base"] + "/api/vote",
              {"reviewer": "r1", "item": it["id"], "left": it["pair"][0], "right": it["pair"][1], "pick": "left"})
    st, body = _get(served["base"] + "/api/votes?reviewer=r1")
    assert st == 200 and body["items"] == [a["id"], b["id"]]
    st, body = _get(served["base"] + "/api/votes?reviewer=nobody")
    assert body["items"] == [] and body["picks"] == {}
    st, body = _get(served["base"] + "/api/votes?reviewer=")
    assert st == 400
    st, body = _get(served["base"] + "/api/health")
    assert body["ok"] and body["items"] == len(served["items"])


def test_static_is_served_and_private_is_not(served, tmp_path):
    with urllib.request.urlopen(served["base"] + "/items.json") as r:
        assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
    with urllib.request.urlopen(served["base"] + "/") as r:
        assert b"GenMedia blind panel" in r.read()
    for path in ("/private/key.json", "/../private/key.json", "/key.json", "/votes.jsonl"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(served["base"] + path)
        assert e.value.code == 404


def test_refuses_key_inside_dist(fake_repo, tmp_path):
    dist = tmp_path / "dist"
    export([("image", fake_repo["image"])], dist=dist, private=dist / "private", salt="x")
    with pytest.raises(SystemExit, match="public"):
        make_server(dist, dist / "private" / "key.json", tmp_path / "v.jsonl", host="127.0.0.1", port=0)


def test_earlier_votes_stay_done_after_reexport(served, fake_repo, tmp_path):
    it = served["items"][0]
    _post(served["base"] + "/api/vote",
          {"reviewer": "r", "item": it["id"], "left": it["pair"][0], "right": it["pair"][1], "pick": "left"})
    export([("image", fake_repo["image"]), ("voice", fake_repo["voice"])],
           dist=served["dist"], private=served["dist"].parent / "private", salt="another")
    st, body = _get(served["base"] + "/api/votes?reviewer=r")
    assert body["items"] == [it["id"]]


def test_reexport_while_serving_is_picked_up(served, fake_repo, tmp_path):
    """A new export = new salt = new item ids. The running server must vote
    against the new key, not the one it started with."""
    _get(served["base"] + "/api/health")
    export([("image", fake_repo["image"])], dist=served["dist"], private=served["dist"].parent / "private", salt="new-salt")
    items = json.loads((served["dist"] / "items.json").read_text())["items"]
    it = items[0]
    st, body = _post(served["base"] + "/api/vote",
                     {"reviewer": "r", "item": it["id"], "left": it["pair"][0], "right": it["pair"][1], "pick": "left"})
    assert st == 200, body
    st, body = _get(served["base"] + "/api/health")
    assert body["items"] == len(items)


def test_previous_pick_and_reason_come_back_by_name(served):
    """A reviewer who returns sees what they chose: the LAST vote per item,
    as the picked media id (never a model name) plus the reason."""
    it = served["items"][0]
    left, right = it["pair"][0], it["pair"][1]
    _post(served["base"] + "/api/vote", {"reviewer": "Sai", "item": it["id"],
                                         "left": left, "right": right, "pick": "left", "reason": "first thought"})
    _post(served["base"] + "/api/vote", {"reviewer": "Sai", "item": it["id"],
                                         "left": right, "right": left, "pick": "left"})   # flipped page, changed mind
    st, body = _get(served["base"] + "/api/votes?reviewer=Sai")
    assert st == 200
    assert body["picks"] == {it["id"]: {"media": right, "reason": None}}
    raw = json.dumps(body)
    for model in served["key"]["items"][it["id"]]["media"].values():
        assert model not in raw                                          # the blind holds on the way out


def test_reason_is_kept_and_a_tie_has_no_media(served):
    it = served["items"][1]
    _post(served["base"] + "/api/vote", {"reviewer": "p", "item": it["id"],
                                         "left": it["pair"][0], "right": it["pair"][1], "pick": "tie", "reason": "both fine"})
    _, body = _get(served["base"] + "/api/votes?reviewer=p")
    assert body["picks"][it["id"]] == {"media": None, "reason": "both fine"}


def test_lines_in_the_older_shape_still_restore_and_report(served):
    """Before 2026-09-11 a line named the side and both models by side. The
    file is append-only, so those lines stay as they are and must read the
    same as new ones."""
    it = served["items"][0]
    media = served["key"]["items"][it["id"]]["media"]
    old = {"ts": "2026-09-09T12:00:00.000Z", "lane": it["lane"], "run_id": "2026-09-01_000000_image",
           "scenario_id": it["scenario_id"], "item": it["id"], "reviewer": "Sai", "pick": "right",
           "picked_model": media[it["pair"][1]], "left_model": media[it["pair"][0]], "right_model": media[it["pair"][1]]}
    served["votes"].write_text(json.dumps(old) + "\n")
    _, body = _get(served["base"] + "/api/votes?reviewer=Sai")
    assert body["picks"][it["id"]]["media"] == it["pair"][1]
    _, body = _get(served["base"] + "/api/results")
    assert body["votes"][0]["picked"] == media[it["pair"][1]] and body["votes"][0]["over"] == media[it["pair"][0]]


def test_results_name_the_models_and_keep_the_last_vote_per_reviewer(served):
    a, b = served["items"][0], served["items"][1]
    _post(served["base"] + "/api/vote", {"reviewer": "r1", "item": a["id"], "left": a["pair"][0], "right": a["pair"][1], "pick": "left"})
    _post(served["base"] + "/api/vote", {"reviewer": "r1", "item": a["id"], "left": a["pair"][0], "right": a["pair"][1], "pick": "right",
                                         "reason": "changed my mind"})
    _post(served["base"] + "/api/vote", {"reviewer": "r2", "item": b["id"], "left": b["pair"][0], "right": b["pair"][1], "pick": "tie"})
    st, body = _get(served["base"] + "/api/results")
    assert st == 200 and body["total"] == 2 and len(body["votes"]) == 2
    mine = next(v for v in body["votes"] if v["reviewer"] == "r1")
    media = served["key"]["items"][a["id"]]["media"]
    assert set(mine) == {"ts", "reviewer", "lane", "scenario_id", "picked", "over", "reason"}
    assert mine["picked"] == media[a["pair"][1]] and mine["over"] == media[a["pair"][0]]
    assert mine["reason"] == "changed my mind" and mine["scenario_id"] == a["scenario_id"]
    tie = next(v for v in body["votes"] if v["reviewer"] == "r2")
    assert tie["picked"] is None and tie["over"] is None
    assert body["votes"][0]["ts"] >= body["votes"][1]["ts"]                   # newest first


def test_cors_lets_the_console_call_from_another_origin(served):
    with urllib.request.urlopen(served["base"] + "/items.json") as r:
        assert r.headers["Access-Control-Allow-Origin"] == "*"
    req = urllib.request.Request(served["base"] + "/api/vote", method="OPTIONS")
    with urllib.request.urlopen(req) as r:
        assert r.status == 204
        assert "POST" in r.headers["Access-Control-Allow-Methods"]
        assert "Content-Type" in r.headers["Access-Control-Allow-Headers"]


def test_media_base_is_the_servers_to_say(served, monkeypatch):
    """A laptop session serves its own dist/media; hosted, the console serves
    the media and PANEL_MEDIA_BASE points the page there."""
    monkeypatch.delenv("PANEL_MEDIA_BASE", raising=False)
    _, body = _get(served["base"] + "/api/config")
    assert body == {"media_base": "media"}
    monkeypatch.setenv("PANEL_MEDIA_BASE", "https://studies-dev.adlc.tilicho.in/reports/genmedia-blind-panel/media/")
    _, body = _get(served["base"] + "/api/config")
    assert body["media_base"] == "https://studies-dev.adlc.tilicho.in/reports/genmedia-blind-panel/media"
