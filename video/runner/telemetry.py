"""The ONE writer — nothing else appends JSONL (plan §20).

Append-only, thread-safe, one JSON object per line. Rows are never edited;
a correction is a new run.

Reading tolerates one specific real-world corruption: an editor that
pretty-printed the file on save (it has happened repeatedly). The records are
intact, only the one-per-line layout is gone — so the reader falls back to
stream-decoding concatenated JSON objects, warns loudly, and `python -m
runner.cli fixjsonl --run <id>` restores the canonical layout (backup kept).
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, row: dict) -> None:
        line = json.dumps(row, ensure_ascii=False, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()


class RunFiles:
    """All JSONL streams of one run folder, plus readers."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.telemetry = JsonlWriter(self.run_dir / "telemetry.jsonl")
        self.checks = JsonlWriter(self.run_dir / "checks.jsonl")
        self.judge = JsonlWriter(self.run_dir / "judge.jsonl")
        self.scores = JsonlWriter(self.run_dir / "scores.jsonl")

    def read(self, name: str) -> list[dict]:
        path = self.run_dir / f"{name}.jsonl"
        if not path.exists():
            return []
        text = path.read_text()
        try:
            return [json.loads(line) for line in text.splitlines()
                    if line.strip()]
        except json.JSONDecodeError:
            rows = _decode_stream(text)
            print(f"warning: {path} is not one-record-per-line (an editor "
                  f"reformatted it?) — recovered {len(rows)} record(s) by "
                  f"stream parse. Restore the canonical layout with: "
                  f"python -m runner.cli fixjsonl --run {self.run_dir.name}",
                  file=sys.stderr)
            return rows


def _decode_stream(text: str) -> list[dict]:
    """Decode concatenated / pretty-printed JSON objects. Raises on anything
    that is not purely a sequence of JSON values — recovery never guesses."""
    dec = json.JSONDecoder()
    rows, idx, n = [], 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        obj, idx = dec.raw_decode(text, idx)
        rows.append(obj)
    return rows


def fix_jsonl(run_dir: Path) -> dict:
    """Rewrite every *.jsonl in the run folder to canonical one-record-per-line
    form. Content is untouched — records are parsed and re-serialised only when
    the file is NOT already canonical; the reformatted original is kept next to
    it as <name>.jsonl.reformatted.bak so the incident stays visible."""
    run_dir = Path(run_dir)
    result = {"repaired": [], "ok": []}
    for path in sorted(run_dir.glob("*.jsonl")):
        text = path.read_text()
        try:
            [json.loads(line) for line in text.splitlines() if line.strip()]
            result["ok"].append(path.name)
            continue
        except json.JSONDecodeError:
            pass
        rows = _decode_stream(text)
        bak = path.with_suffix(".jsonl.reformatted.bak")
        bak.write_text(text)
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        tmp.replace(path)
        result["repaired"].append(f"{path.name} ({len(rows)} records)")
    return result
