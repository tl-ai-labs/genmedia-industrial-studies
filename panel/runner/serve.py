"""
The panel server: static `dist/` plus one endpoint that records a vote.

WHY A SERVER AT ALL. The plan says "append to a local JSON file, no
database". A static page cannot append to a file, so something has to sit
between the browser and `votes.jsonl` - and that something is also the only
place the blind can be resolved safely. The page posts what the reviewer
saw (two opaque media ids, a side); the server looks the models up in the
private key and writes the resolved record. The browser never holds the
mapping, so there is nothing in devtools to find.

WHAT IT REFUSES. A vote for an item it does not know, a pair that is not
that item's pair, a side that is not left/right/tie, a reviewer id that
is empty or absurd, an email that is not one, or a reason over REASON_MAX
characters. Refusals are 400s with a reason; nothing is written.

WHAT IS STORED (2026-09-11, Sai: "we just need votes and reasons: when,
reviewer, lane, scenario id, over, reason"). One line per vote:

    {"ts", "reviewer", "lane", "run_id", "scenario_id", "picked", "over", "reason"}

`picked` is the model the reviewer chose and `over` the one it beat, both
resolved here from the key; a "can't tell" stores null for both. No item
id, no side, no media id, no email. `run_id` stays because the correlation
needs it to find the judge's scores for that scenario - it names a study
run, not a person or a file. The reviewer's NAME is the identity: it is
what `GET /api/votes?reviewer=` matches, so a reviewer who comes back on
another day sees what they already chose. Lines written before this date
carry the older shape (pick/left_model/right_model/picked_model, an item
id, sometimes an email) and are read as they are - the file is
append-only and is never rewritten.

WHO MAY CALL. The studies console renders the voting page itself, from
another origin, so every response carries CORS headers and OPTIONS is
answered. The votes are as public as the panel URL already is.

WHERE THE MEDIA IS. Not necessarily here. Hosted, the pairs' files are
served by the studies console (Sai, 2026-09-11: no media in the cloud
bucket) and this server holds only items.json, the key and the votes;
`GET /api/config` tells the page where to load media from, taken from the
PANEL_MEDIA_BASE environment variable (default `media`, i.e. this server's
own dist/media, which is what a laptop session uses).

WHAT COMES BACK. The reviewer's last pick per item is returned as the
picked MEDIA id (the opaque filename the page already shows), never as a
model name - the blind holds on the way out as well as on the way in.

ONE PROCESS, ONE LOCK. Appends are serialised with a threading lock; the
server is threaded so sixty reviewers loading media at once do not queue
behind each other. Run one instance per votes file.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

REVIEWER_RE = re.compile(r"^[A-Za-z0-9 ._@+-]{1,64}$")
REASON_MAX = 500
PICKS = ("left", "right", "tie")


def picked_of(r: dict[str, Any]) -> Any:
    """The chosen model of a stored vote, whichever shape the line has."""
    return r["picked"] if "picked" in r else r.get("picked_model")


def over_of(r: dict[str, Any]) -> Any:
    """The beaten model of a stored vote, whichever shape the line has."""
    if "over" in r:
        return r["over"]
    picked = r.get("picked_model")
    if picked is None:
        return None
    return r.get("right_model") if r.get("left_model") == picked else r.get("left_model")


def media_base() -> str:
    """Base URL (absolute, or relative to the page) the media is served from,
    trailing slash dropped so `base + "/" + file` is always one slash."""
    return (os.environ.get("PANEL_MEDIA_BASE") or "media").rstrip("/")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class VoteStore:
    """Append-only JSONL. The whole record is written by the server so the
    file stands on its own - the correlation never needs the key."""

    def __init__(self, path: Path, key: dict[str, Any], key_path: Path | None = None):
        self.path = Path(path)
        self.key = key
        self.key_path = Path(key_path) if key_path else None
        self.key_mtime = self.key_path.stat().st_mtime if self.key_path else None
        self.lock = threading.Lock()

    def refresh_key(self) -> None:
        """A re-export while the server runs writes a new key with a new
        salt; every item id changes. Without this, the page (which reloads
        items.json freely) would post ids the server has never seen and
        every vote would be refused as "unknown item"."""
        if self.key_path is None:
            return
        try:
            mtime = self.key_path.stat().st_mtime
        except OSError:
            return
        if mtime != self.key_mtime:
            self.key = json.loads(self.key_path.read_text(encoding="utf-8"))
            self.key_mtime = mtime

    def resolve(self, body: dict[str, Any]) -> dict[str, Any]:
        """Validate a posted vote and return the record to write.
        Raises ValueError with the reason to send back."""
        self.refresh_key()
        reviewer = str(body.get("reviewer") or "").strip()
        if not REVIEWER_RE.match(reviewer):
            raise ValueError("reviewer id must be 1-64 plain characters")
        reason = str(body.get("reason") or "").strip()
        if len(reason) > REASON_MAX:
            raise ValueError(f"reason must be at most {REASON_MAX} characters")
        item_id = str(body.get("item") or "")
        item = self.key["items"].get(item_id)
        if item is None:
            raise ValueError("unknown item")
        left, right = str(body.get("left") or ""), str(body.get("right") or "")
        media = item["media"]
        if left == right or left not in media or right not in media:
            raise ValueError("left/right are not this item's pair")
        pick = str(body.get("pick") or "")
        if pick not in PICKS:
            raise ValueError("pick must be left, right or tie")
        left_model, right_model = media[left], media[right]
        picked = {"left": left_model, "right": right_model, "tie": None}[pick]
        over = {"left": right_model, "right": left_model, "tie": None}[pick]
        return {
            "ts": _utc(),
            "reviewer": reviewer,
            "lane": item["lane"],
            "run_id": item["run_id"],
            "scenario_id": item["scenario_id"],
            "picked": picked,
            "over": over,
            "reason": reason or None,
        }

    def append(self, record: dict[str, Any]) -> int:
        with self.lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return sum(1 for _ in self.path.open("r", encoding="utf-8"))

    def picks(self, reviewer: str) -> dict[str, dict[str, Any]]:
        """One reviewer's LAST vote per item, keyed by the item id the CURRENT
        export uses - matched by what the vote names (lane, run, scenario), so
        votes from an earlier export survive a re-export.

        Each entry is {"media", "reason"}: the picked MEDIA id (None for a
        "can't tell") so the page can light the right side whichever way it
        is flipped today, and the reason. No model name leaves this method.
        Reads both shapes on disk (see the module docstring)."""
        if not self.path.exists():
            return {}
        self.refresh_key()
        items = self.key["items"]
        by_scenario = {(it["lane"], it["run_id"], it["scenario_id"]): iid for iid, it in items.items()}
        out: dict[str, dict[str, Any]] = {}
        with self.lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("reviewer") != reviewer:
                    continue
                iid = by_scenario.get((r.get("lane"), r.get("run_id"), r.get("scenario_id")), r.get("item"))
                item = items.get(iid) if iid else None
                if item is None:
                    continue
                picked = picked_of(r)
                media = next((m for m, model in item["media"].items()
                              if picked is not None and model == picked), None)
                out[iid] = {"media": media, "reason": r.get("reason") or None}
        return out

    def voted(self, reviewer: str) -> list[str]:
        """Item ids this reviewer has answered (see `picks`)."""
        return list(self.picks(reviewer))

    def results(self) -> list[dict[str, Any]]:
        """Every reviewer's LAST vote per scenario, models NAMED, newest first -
        for the studies console's results page: when, reviewer, lane,
        scenario id, picked, over, reason. Keyed on what the vote names
        (reviewer, lane, run, scenario) so a re-export does not split a
        reviewer's history. Reads both shapes on disk. This is the one
        response that carries a model name."""
        if not self.path.exists():
            return []
        last: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        with self.lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                last[(str(r.get("reviewer") or ""), str(r.get("lane")), str(r.get("run_id")), str(r.get("scenario_id")))] = r
        out = [{
            "ts": r.get("ts"), "reviewer": r.get("reviewer"), "lane": r.get("lane"),
            "scenario_id": r.get("scenario_id"), "picked": picked_of(r), "over": over_of(r),
            "reason": r.get("reason") or None,
        } for r in last.values()]
        out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return out


class PanelHandler(SimpleHTTPRequestHandler):
    store: VoteStore  # set via functools.partial in make_server

    def __init__(self, *args, store: VoteStore, directory: str, **kwargs):
        self.store = store
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, fmt, *args):  # quiet: media requests are noise
        pass

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def end_headers(self):
        # The item list changes on re-export; media does not (its name is a
        # hash of its identity), so only the page and the list are no-store.
        if self.path.endswith((".html", ".json")) or self.path in ("/", ""):
            self.send_header("Cache-Control", "no-store")
        self._cors()
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/config":
            return self._json(HTTPStatus.OK, {"media_base": media_base()})
        if url.path == "/api/health":
            self.store.refresh_key()
            return self._json(HTTPStatus.OK, {"ok": True, "items": len(self.store.key["items"])})
        if url.path == "/api/votes":
            reviewer = (parse_qs(url.query).get("reviewer") or [""])[0].strip()
            if not REVIEWER_RE.match(reviewer):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad reviewer id"})
            picks = self.store.picks(reviewer)
            return self._json(HTTPStatus.OK, {"reviewer": reviewer, "items": list(picks), "picks": picks})
        if url.path == "/api/results":
            votes = self.store.results()
            return self._json(HTTPStatus.OK, {"votes": votes, "total": len(votes)})
        if url.path.startswith("/api/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "no such endpoint"})
        return super().do_GET()

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path != "/api/vote":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "no such endpoint"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            record = self.store.resolve(body)
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
        total = self.store.append(record)
        return self._json(HTTPStatus.OK, {"ok": True, "scenario_id": record["scenario_id"],
                                          "picked": record["picked"], "total_votes": total})


def make_server(dist: Path, key_path: Path, votes_path: Path,
                host: str = "0.0.0.0", port: int = 8765) -> ThreadingHTTPServer:
    dist, key_path = Path(dist).resolve(), Path(key_path).resolve()
    if not (dist / "items.json").exists():
        raise SystemExit(f"{dist} has no items.json - run `export` first")
    if not key_path.exists():
        raise SystemExit(f"{key_path} missing - run `export` first")
    if key_path.is_relative_to(dist):
        raise SystemExit("refusing to serve: the key sits inside dist/ and would be public")
    key = json.loads(key_path.read_text(encoding="utf-8"))
    store = VoteStore(Path(votes_path), key, key_path=key_path)
    handler = partial(PanelHandler, store=store, directory=str(dist))
    return ThreadingHTTPServer((host, port), handler)
