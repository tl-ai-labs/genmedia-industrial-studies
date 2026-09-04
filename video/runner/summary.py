"""Telemetry summaries across runs + scenario-completion semantics.

Completion rule: a scenario counts as COMPLETED only when every required
model (i.e. every non-skipped cell) reached `scored`. One model failing,
invalid, or still pending keeps the scenario out of the completed bucket —
partial completion is never dressed up as done.

    completed           every required model scored
    incomplete          at least one required model failed / invalid
    awaiting_judgement  all outputs valid; judging or scoring still pending
    in_progress         some cells not yet generated / checked
    skipped             no model supports the task
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .telemetry import RunFiles

BLOCKING = {"failed", "invalid"}
VALID_PIPELINE = {"measured", "judged", "scored"}


def scenario_completion(manifest: dict) -> dict:
    """sid -> {status, models: {model_id: cell state}}."""
    out: dict = {}
    for cell in manifest.get("cells", {}).values():
        s = out.setdefault(cell["scenario_id"], {"models": {}, "status": None})
        s["models"][cell["model_id"]] = cell["state"]
    for s in out.values():
        required = {m: st for m, st in s["models"].items() if st != "skipped"}
        if not required:
            s["status"] = "skipped"
        elif any(st in BLOCKING for st in required.values()):
            s["status"] = "incomplete"
        elif all(st == "scored" for st in required.values()):
            s["status"] = "completed"
        elif all(st in VALID_PIPELINE for st in required.values()):
            s["status"] = "awaiting_judgement"
        else:
            s["status"] = "in_progress"
    return out


def completion_counts(manifest: dict) -> dict:
    comp = scenario_completion(manifest)
    counts: dict = {"total": len(comp)}
    for s in comp.values():
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return counts


def refresh_scenario_status(manifest_obj) -> dict:
    """Persist the per-scenario status into manifest.json (called after every
    pipeline stage so the folder always states what is and isn't done)."""
    comp = scenario_completion(manifest_obj.data)
    with manifest_obj._lock:
        manifest_obj.data["scenario_status"] = {
            sid: s["status"] for sid, s in sorted(comp.items())}
        manifest_obj.save()
    return comp


# --------------------------------------------------------------------------
# per-run summary
# --------------------------------------------------------------------------

def _lat_stats(values: list) -> dict | None:
    if not values:
        return None
    v = sorted(values)
    return {"avg_ms": round(sum(v) / len(v)), "min_ms": v[0], "max_ms": v[-1],
            "p50_ms": v[len(v) // 2], "n": len(v)}


def summarize_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    files = RunFiles(run_dir)
    telemetry = files.read("telemetry")
    scores = files.read("scores")
    judge_rows = files.read("judge")

    models: dict = {}
    cells: dict = {}
    for cell in manifest.get("cells", {}).values():
        if cell["state"] == "skipped":
            continue
        m = models.setdefault(cell["model_id"], {
            "ratings": [], "latencies": [], "attempts": [], "gen_micro": 0,
            "usage": {}, "statuses": {}, "errors": {}, "eligible": 0, "ok": 0})
        m["eligible"] += 1
        cells[(cell["scenario_id"], cell["model_id"])] = {
            "run_id": manifest["run_id"], "task": cell["task"],
            "scenario_id": cell["scenario_id"], "model_id": cell["model_id"],
            "state": cell["state"], "score": None, "score_status": None,
            "latency_ms": None, "attempts": 0, "cost_usd": None, "error": ""}

    for r in telemetry:
        key = (r["scenario_id"], r["model_id"])
        m = models.get(r["model_id"])
        if m is None or key not in cells:
            continue
        cells[key]["attempts"] += 1
        m["statuses"][r["status"]] = m["statuses"].get(r["status"], 0) + 1
        if r["status"] == "ok":
            m["ok"] += 1
            m["latencies"].append(r.get("latency_ms") or 0)
            m["attempts"].append(r.get("attempt", 1))
            cost = r.get("cost", {})
            m["gen_micro"] += cost.get("micro_usd", 0)
            cells[key]["latency_ms"] = r.get("latency_ms")
            cells[key]["cost_usd"] = round(cost.get("micro_usd", 0) / 1e6, 6)
            for k, v in (r.get("usage") or {}).items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    m["usage"][k] = m["usage"].get(k, 0) + v
        else:
            err = (r.get("error") or r["status"])[:120]
            m["errors"][err] = m["errors"].get(err, 0) + 1
            cells[key]["error"] = err

    latest_scores: dict = {}
    for r in scores:
        latest_scores[(r["scenario_id"], r["model_id"])] = r
    for key, r in latest_scores.items():
        if key in cells:
            cells[key]["score"] = r.get("score")
            cells[key]["score_status"] = r.get("status")
        if r.get("score") is not None and r["model_id"] in models:
            models[r["model_id"]]["ratings"].append(r["score"])

    for mid, m in models.items():
        ratings = m.pop("ratings")
        m["rating"] = ({"mean": round(sum(ratings) / len(ratings), 2),
                        "min": round(min(ratings), 2),
                        "max": round(max(ratings), 2), "n": len(ratings)}
                       if ratings else None)
        m["latency"] = _lat_stats(m.pop("latencies"))
        att = m.pop("attempts")
        m["mean_attempts_to_success"] = round(sum(att) / len(att), 2) if att else None
        m["success_rate"] = round(m["ok"] / m["eligible"], 4) if m["eligible"] else None
        m["gen_cost_usd"] = round(m.pop("gen_micro") / 1e6, 4)

    # per-scenario latency: each model's latency plus stats across models
    scenario_latency: dict = {}
    for (sid, mid), c in cells.items():
        if c["latency_ms"] is not None:
            scenario_latency.setdefault(sid, {"models": {}})["models"][mid] = c["latency_ms"]
    for sid, s in scenario_latency.items():
        vals = list(s["models"].values())
        s.update({"avg_ms": round(sum(vals) / len(vals)),
                  "min_ms": min(vals), "max_ms": max(vals)})

    judge_micro = sum(r.get("cost", {}).get("micro_usd", 0) for r in judge_rows)
    return {
        "run_id": manifest["run_id"],
        "state": manifest.get("state"),
        "tasks": sorted({c["task"] for c in cells.values()}),
        "completion": completion_counts(manifest),
        "scenario_status": {sid: s["status"]
                            for sid, s in scenario_completion(manifest).items()},
        "models": models,
        "scenario_latency": scenario_latency,
        "judge_cost_usd": round(judge_micro / 1e6, 4),
        "cells": list(cells.values()),
    }


def summarize_runs(runs_root: Path, run_ids: list | None = None) -> list[dict]:
    runs_root = Path(runs_root)
    dirs = ([runs_root / r for r in run_ids] if run_ids else
            sorted(d for d in runs_root.iterdir()
                   if (d / "manifest.json").exists()))
    return [summarize_run(d) for d in dirs]


def write_csv(summaries: list[dict], path: Path) -> Path:
    """One row per cell (scenario x model) across all summarized runs."""
    fields = ["run_id", "task", "scenario_id", "model_id", "state",
              "scenario_status", "score", "score_status", "latency_ms",
              "attempts", "cost_usd", "error"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for s in summaries:
            for c in s["cells"]:
                row = dict(c)
                row["scenario_status"] = s["scenario_status"].get(c["scenario_id"])
                w.writerow({k: row.get(k) for k in fields})
    return Path(path)


def build_navigation(run_dir: Path) -> dict:
    """Add browse-friendly views to a run folder without touching originals:

      outputs/by-model/<model_id>/<scenario_id>.png   (symlinks to the
                                canonical outputs/image/<scenario>/<model>)
      INDEX.csv                 one row per image: scenario, model, kind,
                                path, score, status — openable in Excel
    """
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    scores = {}
    for line in (run_dir / "scores.jsonl").read_text().splitlines() \
            if (run_dir / "scores.jsonl").exists() else []:
        r = json.loads(line)
        scores[(r["scenario_id"], r["model_id"])] = r

    rows, linked = [], 0
    out_root = run_dir / "outputs"
    for modality_dir in sorted(d for d in out_root.iterdir()
                               if d.is_dir() and d.name != "by-model"):
        for scen_dir in sorted(d for d in modality_dir.iterdir() if d.is_dir()):
            for f in sorted(scen_dir.iterdir()):
                if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".mp4"):
                    continue
                model_id = f.stem.split(".invalid")[0]
                kind = "invalid_attempt" if ".invalid" in f.name else "output"
                if kind == "output":
                    link_dir = out_root / "by-model" / model_id
                    link_dir.mkdir(parents=True, exist_ok=True)
                    link = link_dir / f"{scen_dir.name}{f.suffix}"
                    if not link.exists():
                        try:
                            link.symlink_to(Path("../..") / modality_dir.name
                                            / scen_dir.name / f.name)
                        except OSError:
                            import shutil
                            shutil.copy2(f, link)
                        linked += 1
                srow = scores.get((scen_dir.name, model_id), {})
                rows.append({"scenario_id": scen_dir.name, "model_id": model_id,
                             "kind": kind,
                             "path": str(f.relative_to(run_dir)),
                             "score": srow.get("score"),
                             "status": srow.get("status",
                                                manifest.get("cells", {}).get(
                                 f"{scen_dir.name}::{model_id}", {}).get("state"))})
    for scen_id, assets in (manifest.get("inputs") or {}).items():
        for a in assets:
            rows.append({"scenario_id": scen_id, "model_id": "",
                         "kind": f"original_input ({a['role']})",
                         "path": a["path"], "score": None, "status": "frozen"})

    index = run_dir / "INDEX.csv"
    with open(index, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["scenario_id", "model_id", "kind",
                                           "path", "score", "status"])
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["scenario_id"], x["model_id"])):
            w.writerow(r)
    return {"images_indexed": len(rows), "model_links": linked, "index": str(index)}


def print_summary(summaries: list[dict]) -> None:
    for s in summaries:
        comp = s["completion"]
        print(f"\n== {s['run_id']}  ({', '.join(s['tasks'])})  state={s['state']} ==")
        parts = [f"{k}={v}" for k, v in comp.items() if k != "total"]
        print(f"scenarios: {comp['total']}  |  " + "  ".join(parts))
        header = (f"{'model':<32}{'mean':>6}{'min':>6}{'max':>6}{'n':>4}"
                  f"{'avg-lat':>9}{'min':>7}{'max':>8}{'succ%':>7}{'att':>5}"
                  f"{'gen$':>9}")
        print(header)
        for mid, m in sorted(s["models"].items()):
            r, lat = m["rating"], m["latency"]
            print(f"{mid:<32}"
                  f"{(r['mean'] if r else '—'):>6}{(r['min'] if r else '—'):>6}"
                  f"{(r['max'] if r else '—'):>6}{(r['n'] if r else 0):>4}"
                  f"{((str(round(lat['avg_ms'] / 1000, 1)) + 's') if lat else '—'):>9}"
                  f"{((str(round(lat['min_ms'] / 1000, 1)) + 's') if lat else '—'):>7}"
                  f"{((str(round(lat['max_ms'] / 1000, 1)) + 's') if lat else '—'):>8}"
                  f"{(str(round(m['success_rate'] * 100)) + '%' if m['success_rate'] is not None else '—'):>7}"
                  f"{(m['mean_attempts_to_success'] or '—'):>5}"
                  f"{m['gen_cost_usd']:>9.4f}")
            for status, n in sorted(m["statuses"].items()):
                if status != "ok":
                    print(f"    {status}: {n}")
            for err, n in sorted(m["errors"].items(), key=lambda x: -x[1])[:3]:
                print(f"    error x{n}: {err}")
        print(f"judging cost: ${s['judge_cost_usd']:.4f} (kept separate)")
        slats = s.get("scenario_latency", {})
        if slats:
            if len(slats) <= 20:
                print(f"{'scenario':<14}{'avg-lat':>9}{'min':>8}{'max':>8}  per model")
                for sid, sl in sorted(slats.items()):
                    per = " ".join(f"{m.split('-')[0]}:{v / 1000:.0f}s"
                                   for m, v in sorted(sl["models"].items()))
                    print(f"{sid:<14}{sl['avg_ms'] / 1000:>8.1f}s{sl['min_ms'] / 1000:>7.1f}s"
                          f"{sl['max_ms'] / 1000:>7.1f}s  {per}")
            else:
                worst = sorted(slats.items(), key=lambda x: -x[1]["max_ms"])[:5]
                print("slowest scenarios: " + ", ".join(
                    f"{sid} ({sl['max_ms'] / 1000:.0f}s)" for sid, sl in worst)
                    + "  — full per-scenario latency in --csv / --json")
        bad = [sid for sid, st in s["scenario_status"].items()
               if st in ("incomplete", "in_progress")]
        if bad:
            print(f"NOT completed: {', '.join(bad)}")
