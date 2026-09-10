"""
Export run media to opaque ids: the public `dist/` and the private key.

THE BLIND, STATED ONCE. Every file the browser can reach - the page,
`items.json`, every file under `media/` - contains no model id, no run id
and no provenance metadata. The mapping from opaque id back to model lives
in `private/key.json`, which the server reads and never serves. The test
suite enforces the first half by walking `dist/` for any model id string.

ORDER IS NOT A CHANNEL EITHER. The two arms of an item are listed sorted by
their opaque id, which is a salted hash - so "first in the pair" carries no
information about which model it is. The page then flips left/right per
reviewer on top of that.

REGENERATION. `dist/` is wiped and rebuilt on every export, with a fresh
salt unless one is passed. Votes already cast reference item ids from the
key that produced them, and each vote record carries the resolved model
ids, so a re-export never orphans a vote - the correlation reads the votes,
not the key.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runs import MEDIA_KINDS, Pair, RunData, load_run
from .strip import strip_bytes

SITE_DIR = Path(__file__).resolve().parent.parent / "site"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _opaque(salt: str, *parts: str, n: int = 12) -> str:
    return hashlib.sha256("|".join((salt, *parts)).encode("utf-8")).hexdigest()[:n]


def _copy_media(src: Path, dst: Path) -> bool:
    data = src.read_bytes()
    data, stripped = strip_bytes(data, src.suffix)
    dst.write_bytes(data)
    return stripped


def export(runs: list[tuple[str, Path]], dist: Path, private: Path,
           site: Path | None = None, salt: str | None = None,
           extra: list[RunData] | None = None) -> dict[str, Any]:
    """`runs` is [(lane, run_dir)]; `extra` is already-loaded sources such as
    the voice dashboard import. Returns the summary that the CLI prints."""
    site = site or SITE_DIR
    salt = salt or secrets.token_hex(16)
    dist, private = Path(dist), Path(private)
    if dist.exists():
        shutil.rmtree(dist)
    (dist / "media").mkdir(parents=True)
    private.mkdir(parents=True, exist_ok=True)

    public_items: list[dict[str, Any]] = []
    key_items: dict[str, dict[str, Any]] = {}
    key_runs: dict[str, dict[str, str]] = {}
    summary: dict[str, Any] = {"generated": _utc(), "lanes": {}, "passthrough": [],
                               "ext_mismatch": [], "skipped": {}}

    sources = [load_run(Path(run_dir), lane=lane) for lane, run_dir in runs] + list(extra or [])
    for data in sources:
        key_runs.setdefault(data.lane, {})[data.run_id] = str(data.run_dir)
        summary["lanes"].setdefault(data.lane, {"runs": [], "items": 0})
        summary["lanes"][data.lane]["runs"].append(data.run_id)
        summary["skipped"][f"{data.lane}:{data.run_id}"] = data.skipped
        for pair in data.pairs:
            item_id = _opaque(salt, data.lane, data.run_id, pair.scenario_id, n=10)
            media_map: dict[str, str] = {}
            pair_files: list[str] = []
            for arm in pair.arms:
                assert arm.media is not None
                oid = _opaque(salt, data.lane, data.run_id, pair.scenario_id, arm.model_id)
                fname = f"item-{oid}{arm.media.suffix.lower()}"
                if not _copy_media(arm.media, dist / "media" / fname):
                    summary["passthrough"].append(fname)
                media_map[fname] = arm.model_id
                pair_files.append(fname)
            pair_files.sort()  # by opaque id: no model order survives
            if len({Path(f).suffix for f in pair_files}) > 1:
                summary["ext_mismatch"].append(f"{data.lane}:{pair.scenario_id}")
            inputs = []
            for label, path in pair.inputs:
                oid = _opaque(salt, data.lane, data.run_id, pair.scenario_id, "input", label)
                fname = f"input-{oid}{path.suffix.lower()}"
                _copy_media(path, dist / "media" / fname)
                inputs.append({"label": label, "media": fname,
                               "kind": MEDIA_KINDS[path.suffix.lower()]})
            public_items.append({
                "id": item_id, "lane": data.lane, "scenario_id": pair.scenario_id,
                "task": pair.task, "title": pair.title, "brief": pair.brief,
                "industry": pair.industry, "kind": pair.kind, "inputs": inputs,
                "pair": pair_files,
            })
            key_items[item_id] = {
                "lane": data.lane, "run_id": data.run_id, "scenario_id": pair.scenario_id,
                "media": media_map,
                "judge": {a.model_id: a.score for a in pair.arms},
            }
            summary["lanes"][data.lane]["items"] += 1

    lanes = sorted(summary["lanes"])
    (dist / "items.json").write_text(json.dumps(
        {"generated": summary["generated"], "lanes": lanes, "items": public_items},
        ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy2(site / "index.html", dist / "index.html")
    (private / "key.json").write_text(json.dumps(
        {"generated": summary["generated"], "salt": salt, "runs": key_runs,
         "items": key_items}, indent=1), encoding="utf-8")
    summary["n_items"] = len(public_items)
    summary["dist"] = str(dist)
    summary["key"] = str(private / "key.json")
    return summary


def format_summary(s: dict[str, Any]) -> str:
    lines = [f"exported {s['n_items']} items -> {s['dist']}",
             f"key (never served): {s['key']}"]
    for lane, info in sorted(s["lanes"].items()):
        lines.append(f"  {lane}: {info['items']} items from {', '.join(info['runs'])}")
    for run, skipped in s["skipped"].items():
        for sid, why in skipped:
            lines.append(f"  skipped {run} {sid}: {why}")
    if s["ext_mismatch"]:
        lines.append("  WARNING arms differ in file extension (a weak model tell): "
                     + ", ".join(s["ext_mismatch"]))
    if s["passthrough"]:
        lines.append(f"  {len(s['passthrough'])} files passed through without metadata "
                     "stripping (webm/mov/m4a/ogg) - see strip.py")
    return "\n".join(lines)
