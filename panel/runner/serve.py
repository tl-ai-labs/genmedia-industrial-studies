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
that item's pair, a side that is not left/right/tie, or a reviewer id that
is empty or absurd. Refusals are 400s with a reason; nothing is written.

ONE PROCESS, ONE LOCK. Appends are serialised with a threading lock; the
server is threaded so sixty reviewers loading media at once do not queue
behind each other. Run one instance per votes file.
"""

from __future__ import annotations

import json
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
PICKS = ("left", "right", "tie")


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
        return {
            "ts": _utc(),
            "lane": item["lane"],
            "run_id": item["run_id"],
            "scenario_id": item["scenario_id"],
            "item": item_id,
            "reviewer": reviewer,
            "pick": pick,
            "picked_model": picked,
            "left_model": left_model,
            "right_model": right_model,
        }

    def append(self, record: dict[str, Any]) -> int:
        with self.lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return sum(1 for _ in self.path.open("r", encoding="utf-8"))

    def voted(self, reviewer: str) -> list[str]:
        if not self.path.exists():
            return []
        out: list[str] = []
        with self.lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("reviewer") == reviewer and r.get("item") not in out:
                    out.append(r["item"])
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

    def end_headers(self):
        # The item list changes on re-export; media does not (its name is a
        # hash of its identity), so only the page and the list are no-store.
        if self.path.endswith((".html", ".json")) or self.path in ("/", ""):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/health":
            self.store.refresh_key()
            return self._json(HTTPStatus.OK, {"ok": True, "items": len(self.store.key["items"])})
        if url.path == "/api/votes":
            reviewer = (parse_qs(url.query).get("reviewer") or [""])[0].strip()
            if not REVIEWER_RE.match(reviewer):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad reviewer id"})
            return self._json(HTTPStatus.OK, {"reviewer": reviewer,
                                              "items": self.store.voted(reviewer)})
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
        return self._json(HTTPStatus.OK, {"ok": True, "item": record["item"],
                                          "pick": record["pick"], "total_votes": total})


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
