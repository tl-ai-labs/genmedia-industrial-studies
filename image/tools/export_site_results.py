"""Export a run's per-scenario results for the study site's scenario-bank Win Map.

Writes one JSON file keyed by scenario id, so the site can show, on every
scenario-bank tile (industry, customer, use case, ...), how often the Google
model won, tied or lost across the scenarios under it — the Win Map Ravi
asked for on 2026-10-12. Only scenarios in the run appear; the site shows
nothing for the rest.

    python tools/export_site_results.py --run <id> --out <site>/dashboard/public/data/studies/genmedia-model-comparison/scenario-bank/results/image.json

The win rule is the client report's: scores rounded to two decimals, higher
wins, equal is a tie; a scenario where either cell did not score is "none".
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Display names, as the client report shows them (models alone, never makers).
NAMES = {
    "gemini-nano-banana-2-1-vertex": "Nano Banana 2.1",
    "gpt-image-2-5-sunburst-high": "GPT Image 2.5 Sunburst (high)",
}


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    run_dir = ROOT / "runs" / args.run
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    models = [m["id"] for m in manifest["models"]]
    google = [m for m in models if "gemini" in m][0]
    rival = [m for m in models if m != google][0]

    # the latest score row per cell
    rows: dict[tuple[str, str], dict] = {}
    for r in read_jsonl(run_dir / "scores.jsonl"):
        rows[(r["scenario_id"], r["model_id"])] = r

    judge = next((m for m in manifest.get("models", []) if m.get("role") == "judge"), None)
    judge_rows = read_jsonl(run_dir / "judge.jsonl")
    judge_model = judge_rows[-1]["judge"]["provider_model"] if judge_rows else (judge or {}).get("provider_model")

    scenarios: dict[str, dict] = {}
    tally = {"google": 0, "rival": 0, "tie": 0, "none": 0}
    for sid in sorted({s for s, _ in rows}):
        a, b = rows.get((sid, google)), rows.get((sid, rival))

        def cell(r: dict | None) -> dict | None:
            if not r or r.get("status") != "scored":
                return None
            out = {"score": round(float(r["score"]), 2)}
            if r.get("pass") is not None:
                out["pass"] = bool(r["pass"])
            return out

        ca, cb = cell(a), cell(b)
        if ca and cb:
            winner = "google" if ca["score"] > cb["score"] else "rival" if cb["score"] > ca["score"] else "tie"
        else:
            winner = "none"
        tally[winner] += 1
        scenarios[sid] = {"winner": winner, "google": ca, "rival": cb}

    out = {
        "schema": "genmedia-bank-results/1",
        "modality": manifest["modality"],
        "run": {
            "id": manifest["run_id"],
            "date": manifest["created"][:10],
            "judge": judge_model,
            "google": {"id": google, "name": NAMES.get(google, google)},
            "rival": {"id": rival, "name": NAMES.get(rival, rival)},
            "scenarios": len(scenarios),
            "tally": tally,
        },
        "scenarios": scenarios,
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{p}: {len(scenarios)} scenarios, tally {tally}")


if __name__ == "__main__":
    main()
