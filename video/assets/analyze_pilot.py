"""Pilot comparison: per-scenario Omni Flash (Google/Gemini) vs Seedance 2.5,
with the sheet's capability / adherence clauses / industry attached, and a
ranked list of the scenarios and prompts most favourable to the Gemini arm.

Reads only the run folder's JSONL (nothing is regenerated or re-judged) and
the frozen scenarios; sheet fields come from batches/video-v1.xlsx.

    python assets/analyze_pilot.py <run-id>      -> prints markdown, writes
                                                    runs/<run-id>/pilot_analysis.{md,json}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
import yaml

VIDEO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VIDEO))
from runner.telemetry import RunFiles  # noqa: E402

GEMINI = "omni-flash-vertex"
RIVAL = "seedance-2-5"
NAMES = {GEMINI: "Omni Flash (Gemini)", RIVAL: "Seedance 2.5"}
TIE = 0.5


def sheet_rows() -> dict:
    wb = openpyxl.load_workbook(VIDEO / "scenarios" / "batches" / "video-v1.xlsx",
                                read_only=True, data_only=True)
    ws = wb["bank"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(c) for c in rows[0]]
    out = {}
    for r in rows[1:]:
        d = dict(zip(hdr, r))
        out[d["Scenario ID"]] = d
    return out


def main(run_id: str) -> int:
    run_dir = VIDEO / "runs" / run_id
    files = RunFiles(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    scores = {(r["scenario_id"], r["model_id"]): r for r in files.read("scores")}
    judge = {(r["scenario_id"], r["model_id"]): r for r in files.read("judge")
             if r.get("status") == "judged"}
    checks = {(r["scenario_id"], r["model_id"]): r for r in files.read("checks")}
    tele = {}
    for r in files.read("telemetry"):
        if r.get("status") == "ok":
            tele[(r["scenario_id"], r["model_id"])] = r
    sheet = sheet_rows()
    scen = {}
    for f in sorted((run_dir / "scenarios").glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        scen[d["id"]] = d

    rows = []
    for sid in sorted(scen):
        g, s = scores.get((sid, GEMINI)), scores.get((sid, RIVAL))
        row = {"id": sid, "title": scen[sid].get("title", ""),
               "family": (scen[sid].get("tags") or ["-"])[0],
               "prompt": scen[sid]["prompt"],
               "capability": (sheet.get(sid) or {}).get("Capability under test", ""),
               "clauses": (sheet.get(sid) or {}).get("Adherence clauses — check each", ""),
               "industry": (sheet.get(sid) or {}).get("Primary industry", "")}
        for mid, key in ((GEMINI, "gemini"), (RIVAL, "rival")):
            sr = scores.get((sid, mid)) or {}
            jr = judge.get((sid, mid)) or {}
            tr = tele.get((sid, mid)) or {}
            row[key] = {
                "status": sr.get("status") or manifest["cells"].get(f"{sid}::{mid}", {}).get("state"),
                "score": sr.get("score"),
                "criteria": {n: c["score"] for n, c in (sr.get("criteria") or {}).items()},
                "reasoning": {n: c.get("reasoning", "") for n, c in (sr.get("criteria") or {}).items()},
                "note": jr.get("overall_note", ""),
                "cost_usd": (tr.get("cost") or {}).get("micro_usd", 0) / 1e6,
                "latency_s": (tr.get("latency_ms") or 0) / 1000,
                "reason": sr.get("reason", ""),
            }
        gsc, rsc = row["gemini"]["score"], row["rival"]["score"]
        row["delta"] = round(gsc - rsc, 2) if gsc is not None and rsc is not None else None
        row["winner"] = (None if row["delta"] is None else
                         "tie" if abs(row["delta"]) <= TIE else
                         GEMINI if row["delta"] > 0 else RIVAL)
        rows.append(row)

    scored = [r for r in rows if r["delta"] is not None]
    wins = sum(1 for r in scored if r["winner"] == GEMINI)
    losses = sum(1 for r in scored if r["winner"] == RIVAL)
    ties = sum(1 for r in scored if r["winner"] == "tie")
    mean_g = sum(r["gemini"]["score"] for r in scored) / len(scored) if scored else None
    mean_r = sum(r["rival"]["score"] for r in scored) / len(scored) if scored else None

    # per-criterion means
    crit_names = sorted({n for r in scored for n in r["gemini"]["criteria"]})
    crit = {}
    for n in crit_names:
        gv = [r["gemini"]["criteria"][n] for r in scored if n in r["gemini"]["criteria"]]
        rv = [r["rival"]["criteria"][n] for r in scored if n in r["rival"]["criteria"]]
        crit[n] = {"gemini": round(sum(gv) / len(gv), 2) if gv else None,
                   "rival": round(sum(rv) / len(rv), 2) if rv else None}

    # families
    fam = {}
    for r in scored:
        f = fam.setdefault(r["family"], {"n": 0, "g": 0.0, "r": 0.0, "wins": 0})
        f["n"] += 1; f["g"] += r["gemini"]["score"]; f["r"] += r["rival"]["score"]
        f["wins"] += r["winner"] == GEMINI
    for f in fam.values():
        f["g"] = round(f["g"] / f["n"], 2); f["r"] = round(f["r"] / f["n"], 2)

    # costs / latency
    def tot(key, field):
        return sum(r[key][field] for r in rows)
    totals = {"gemini_cost": round(tot("gemini", "cost_usd"), 2),
              "rival_cost": round(tot("rival", "cost_usd"), 2),
              "judge_cost": round(sum((j.get("cost") or {}).get("micro_usd", 0)
                                      for j in judge.values()) / 1e6, 4),
              "gemini_latency_mean_s": round(tot("gemini", "latency_s") / max(1, len(rows)), 1),
              "rival_latency_mean_s": round(tot("rival", "latency_s") / max(1, len(rows)), 1)}

    # the deliverable: scenarios ranked by how favourable they are to Gemini
    favourable = sorted(scored, key=lambda r: (-r["delta"], -r["gemini"]["score"]))

    # ---- markdown ----------------------------------------------------------
    md = [f"# Pilot analysis — {run_id}", "",
          f"{len(scored)} scenarios scored (of {len(rows)}), 720p, 1 seed, blind-judged by "
          f"Gemini 3 Flash (temperature 0). Tie band ±{TIE}.", "",
          f"| | {NAMES[GEMINI]} | {NAMES[RIVAL]} |", "|---|---|---|",
          f"| Mean score | **{mean_g:.2f}** | **{mean_r:.2f}** |" if scored else "| Mean | — | — |",
          f"| Scenario wins (tie ±{TIE}) | {wins} | {losses} | (ties {ties})",
          f"| Generation cost | ${totals['gemini_cost']} | ${totals['rival_cost']} |",
          f"| Mean latency per clip | {totals['gemini_latency_mean_s']} s | {totals['rival_latency_mean_s']} s |",
          "", "## Per-criterion means", "", "| criterion | Gemini | Seedance |", "|---|---|---|"]
    for n, v in crit.items():
        md.append(f"| {n} | {v['gemini']} | {v['rival']} |")
    md += ["", "## Per family", "", "| family | n | Gemini | Seedance | Gemini wins |", "|---|---|---|---|---|"]
    for f, v in fam.items():
        md.append(f"| {f} | {v['n']} | {v['g']} | {v['r']} | {v['wins']} |")
    md += ["", "## Every scenario", "",
           "| id | title | industry | Gemini | Seedance | Δ | winner |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        g, s = r["gemini"], r["rival"]
        gs = f"{g['score']:.2f}" if g["score"] is not None else f"({g['status']})"
        ss = f"{s['score']:.2f}" if s["score"] is not None else f"({s['status']})"
        w = {GEMINI: "Gemini", RIVAL: "Seedance", "tie": "tie", None: "—"}[r["winner"]]
        md.append(f"| {r['id']} | {r['title']} | {r['industry']} | {gs} | {ss} | "
                  f"{r['delta'] if r['delta'] is not None else '—'} | {w} |")
    md += ["", "## Most favourable scenarios and prompts for the Gemini arm", ""]
    for i, r in enumerate(favourable[:6], 1):
        g = r["gemini"]
        weakest = min(r["rival"]["criteria"], key=r["rival"]["criteria"].get) if r["rival"]["criteria"] else "-"
        strongest = max(g["criteria"], key=g["criteria"].get) if g["criteria"] else "-"
        md += [f"### {i}. {r['id']} — {r['title']}  (Gemini {g['score']:.2f} vs Seedance "
               f"{r['rival']['score']:.2f}, Δ {r['delta']:+.2f})",
               f"- **Capability under test:** {r['capability']}",
               f"- **Industry:** {r['industry']}",
               f"- **Prompt (verbatim):** {r['prompt']}",
               f"- **Adherence clauses:** {r['clauses']}",
               f"- **Gemini's strongest criterion:** {strongest} ({g['criteria'].get(strongest)}); "
               f"**Seedance's weakest:** {weakest} ({r['rival']['criteria'].get(weakest)})",
               f"- **Judge on Gemini:** {g['note'] or g['reasoning'].get('prompt_adherence', '')}",
               f"- **Judge on Seedance:** {r['rival']['note'] or r['rival']['reasoning'].get('prompt_adherence', '')}",
               ""]
    md += ["## Least favourable for Gemini (where Seedance leads)", ""]
    for r in [x for x in favourable[::-1] if x["winner"] == RIVAL][:4]:
        md += [f"- **{r['id']} — {r['title']}** (Δ {r['delta']:+.2f}): {r['capability']}. "
               f"Judge on Gemini: {r['gemini']['note'] or r['gemini']['reasoning'].get('prompt_adherence', '')}"]
    text = "\n".join(md) + "\n"
    (run_dir / "pilot_analysis.md").write_text(text)
    (run_dir / "pilot_analysis.json").write_text(json.dumps(
        {"run_id": run_id, "mean": {"gemini": mean_g, "rival": mean_r},
         "wins": {"gemini": wins, "rival": losses, "ties": ties}, "criteria": crit,
         "families": fam, "totals": totals, "rows": rows}, indent=1, ensure_ascii=False))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
