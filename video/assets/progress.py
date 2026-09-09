"""Progress of a run, from its JSONL + manifest only (safe to run any time,
even mid-run): cells by state per model, spend so far, per-clip latency,
failures, and an ETA from the measured pace.

    python assets/progress.py            # newest run
    python assets/progress.py <run-id>
"""
from __future__ import annotations

import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

VIDEO = Path(__file__).resolve().parent.parent
RUNS = VIDEO / "runs"


def main(run_id: str | None) -> int:
    run_dir = RUNS / run_id if run_id else max((d for d in RUNS.iterdir() if d.is_dir()),
                                               key=lambda d: d.name)
    m = json.loads((run_dir / "manifest.json").read_text())
    tel = ([json.loads(l) for l in (run_dir / "telemetry.jsonl").read_text().splitlines() if l.strip()]
           if (run_dir / "telemetry.jsonl").exists() else [])
    now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{run_dir.name}] state={m['state']}  cells={len(m['cells'])}  "
          f"budget=${m.get('budget_usd')}  now={now}")
    by = collections.defaultdict(collections.Counter)
    for c in m["cells"].values():
        by[c["model_id"]][c["state"]] += 1
    total_micro = 0
    for mid, states in by.items():
        ok = [r for r in tel if r["model_id"] == mid and r["status"] == "ok"]
        fails = [r for r in tel if r["model_id"] == mid and r["status"] != "ok"]
        micro = sum(r["cost"]["micro_usd"] for r in ok)
        total_micro += micro
        lat = [r["latency_ms"] / 1000 for r in ok]
        done = sum(v for k, v in states.items() if k not in ("planned", "prepared"))
        n = sum(states.values())
        print(f"  {mid:<20} {done:>2}/{n}  {dict(states)}")
        print(f"  {'':<20} spent ${micro / 1e6:.2f}  clips ok={len(ok)} failed-attempts={len(fails)}"
              + (f"  latency avg={sum(lat) / len(lat):.0f}s max={max(lat):.0f}s" if lat else ""))
        for r in fails[-3:]:
            print(f"  {'':<20} ! {r['scenario_id']} {r['status']}: {(r.get('error') or '')[:90]}")
    print(f"  total spent ${total_micro / 1e6:.2f} of ${m.get('budget_usd')}")
    # latest completed cells
    recent = sorted((r for r in tel if r["status"] == "ok"), key=lambda r: r["ts"])[-5:]
    if recent:
        print("  latest:", " · ".join(f"{r['scenario_id']}×{r['model_id'].split('-')[0]} {r['ts'][11:19]}Z"
                                       for r in recent))
    ev = m.get("events", [])
    if ev:
        print("  events:", " → ".join(f"{e['state']}@{e['ts'][11:19]}" for e in ev[-4:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
