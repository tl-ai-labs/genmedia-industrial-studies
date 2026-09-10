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
    with urllib.request.urlopen(url) as r:
        return r.status, json.loads(r.read())


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
                     {"reviewer": "sai", "item": it["id"], "left": left, "right": right, "pick": "right"})
    assert st == 200 and body["ok"] and body["total_votes"] == 1
    rec = json.loads(served["votes"].read_text().splitlines()[0])
    media = served["key"]["items"][it["id"]]["media"]
    assert rec["lane"] == "image" and rec["scenario_id"] == "IMG-TXT-01"
    assert rec["reviewer"] == "sai" and rec["pick"] == "right"
    assert rec["left_model"] == media[left] and rec["right_model"] == media[right]
    assert rec["picked_model"] == media[right]
    assert rec["run_id"] == "2026-09-01_000000_image" and rec["ts"].endswith("Z")


def test_tie_records_no_model(served):
    it = served["items"][0]
    st, _ = _post(served["base"] + "/api/vote",
                  {"reviewer": "a", "item": it["id"], "left": it["pair"][0], "right": it["pair"][1], "pick": "tie"})
    assert st == 200
    rec = json.loads(served["votes"].read_text().splitlines()[-1])
    assert rec["pick"] == "tie" and rec["picked_model"] is None


@pytest.mark.parametrize("bad", [
    {"reviewer": "", "pick": "left"},
    {"reviewer": "x" * 65, "pick": "left"},
    {"reviewer": "ok", "item": "nope", "pick": "left"},
    {"reviewer": "ok", "pick": "up"},
    {"reviewer": "ok", "pick": "left", "swap_pair": True},
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
    assert body["items"] == []
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
