"""CLI — run / judge / score / report / cost are separate commands on
purpose (plan §19): re-judging or re-reporting must never re-generate media,
and a report tweak must cost nothing.

    python -m runner.cli run    --modality video --scenarios scenarios/ --budget 5.00
    python -m runner.cli judge  --run <run-id>
    python -m runner.cli score  --run <run-id>          # re-score after a weight change
    python -m runner.cli report --run <run-id> --open
    python -m runner.cli cost   --run <run-id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_dir(args) -> Path:
    p = Path(args.run)
    if p.exists():
        return p
    candidate = PROJECT_ROOT / "runs" / args.run
    if candidate.exists():
        return candidate
    sys.exit(f"error: run not found: {args.run} (looked in {candidate})")


def cmd_run(args) -> int:
    from .generate import RunRejected, run_generation
    from .loaders import enabled_models, load_models, load_scenarios

    try:
        scenarios = load_scenarios(args.scenarios, modality=args.modality)
        models = enabled_models(load_models(args.models), args.modality)
        if len(models) < 1:
            raise RunRejected(f"no enabled {args.modality} models in {args.models}")
        print(f"[plan] {len(scenarios)} scenario(s) x {len(models)} model(s) "
              f"({', '.join(m.id for m in models)})")
        run_dir = run_generation(
            PROJECT_ROOT, scenarios, models, args.modality,
            budget_usd=args.budget, workers=args.workers, run_id=args.run)
    except RunRejected as e:
        print(f"\nREJECTED — {e}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as e:
        print(f"\nREJECTED — {e}", file=sys.stderr)
        return 2

    _print_cell_summary(run_dir)
    print(f"\nrun folder: {run_dir}")
    print(f"next: python -m runner.cli judge --run {run_dir.name}")
    return 0


def cmd_judge(args) -> int:
    from .generate import RunRejected
    from .judge import judge_run
    from .scoring import score_run
    run_dir = _run_dir(args)
    try:
        counts = judge_run(PROJECT_ROOT, run_dir, Path(args.models))
    except RunRejected as e:
        print(f"\nREJECTED — {e}", file=sys.stderr)
        return 2
    print(f"[judge] judged={counts['judged']} unjudged={counts['unjudged']} "
          f"already-done={counts['skipped_existing']}")
    sc = score_run(PROJECT_ROOT, run_dir)
    print(f"[score] scored={sc['scored']} invalid(earned 0)={sc['invalid']} "
          f"unjudged(excluded)={sc['unjudged']}")
    _print_cell_summary(run_dir)
    print(f"next: python -m runner.cli report --run {run_dir.name} --open")
    return 0


def cmd_score(args) -> int:
    from .scoring import score_run
    run_dir = _run_dir(args)
    sc = score_run(PROJECT_ROOT, run_dir)
    print(f"[score] scored={sc['scored']} invalid={sc['invalid']} "
          f"unjudged={sc['unjudged']} other={sc['other']}")
    return 0


def cmd_report(args) -> int:
    from .report import build_combined_report, build_report
    runs = args.run if isinstance(args.run, list) else [args.run]
    dirs = []
    for r in runs:
        args.run = r
        dirs.append(_run_dir(args))
    hide = tuple(args.hide_industry or [])
    if len(dirs) == 1:
        out = build_report(PROJECT_ROOT, dirs[0], open_browser=args.open,
                           hide_industries=hide)
    else:
        from datetime import datetime
        out_path = Path(args.out) if args.out else (
            PROJECT_ROOT / "runs" /
            f"combined-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.html")
        out = build_combined_report(PROJECT_ROOT, dirs, out_path,
                                    open_browser=args.open,
                                    hide_industries=hide,
                                    brief=args.brief)
    print(f"report: {out}")
    return 0


def cmd_cost(args) -> int:
    from .telemetry import RunFiles
    run_dir = _run_dir(args)
    files = RunFiles(run_dir)
    gen = files.read("telemetry")
    jud = files.read("judge")

    by_model: dict[str, dict] = {}
    for r in gen:
        c = r.get("cost")
        if not c:
            continue
        m = by_model.setdefault(r["model_id"], {"micro": 0, "estimated": False, "calls": 0})
        m["micro"] += c["micro_usd"]
        m["calls"] += 1
        if c.get("usage_source") == "estimated":
            m["estimated"] = True
    print(f"{'model':<28}{'calls':>6}{'gen USD':>12}")
    for mid, m in sorted(by_model.items()):
        est = "  (contains estimates)" if m["estimated"] else ""
        print(f"{mid:<28}{m['calls']:>6}{m['micro'] / 1e6:>12.4f}{est}")
    judge_micro = sum(r.get("cost", {}).get("micro_usd", 0) for r in jud)
    gen_micro = sum(m["micro"] for m in by_model.values())
    print(f"{'-' * 46}")
    print(f"{'generation total':<34}{gen_micro / 1e6:>12.4f}")
    print(f"{'judging total (kept separate)':<34}{judge_micro / 1e6:>12.4f}")
    return 0


def cmd_fixjsonl(args) -> int:
    from .telemetry import fix_jsonl
    result = fix_jsonl(_run_dir(args))
    for name in result["repaired"]:
        print(f"repaired: {name} (original kept as .reformatted.bak)")
    for name in result["ok"]:
        print(f"already canonical: {name}")
    return 0


def cmd_index(args) -> int:
    from .summary import build_navigation
    info = build_navigation(_run_dir(args))
    print(f"indexed {info['images_indexed']} images | "
          f"{info['model_links']} by-model links | {info['index']}")
    return 0


def cmd_summary(args) -> int:
    from .summary import print_summary, summarize_runs, write_csv
    summaries = summarize_runs(PROJECT_ROOT / "runs", args.run or None)
    if not summaries:
        print("no runs found")
        return 1
    print_summary(summaries)
    if args.csv:
        path = write_csv(summaries, Path(args.csv))
        print(f"\nper-cell CSV: {path}")
    if args.json:
        slim = [{k: v for k, v in s.items() if k != "cells"} for s in summaries]
        Path(args.json).write_text(json.dumps(slim, indent=1))
        print(f"aggregate JSON: {args.json}")
    return 0


def _print_cell_summary(run_dir: Path) -> None:
    manifest = json.loads((Path(run_dir) / "manifest.json").read_text())
    counts: dict[str, int] = {}
    for cell in manifest.get("cells", {}).values():
        counts[cell["state"]] = counts.get(cell["state"], 0) + 1
    parts = " · ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"[cells] {parts}   (run state: {manifest.get('state')})")


def main(argv=None) -> int:
    from .loaders import load_dotenv
    load_dotenv(PROJECT_ROOT)

    ap = argparse.ArgumentParser(prog="runner.cli",
                                 description="GenMedia model comparison runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="generate outputs + deterministic checks")
    p.add_argument("--modality", required=True, choices=["image", "voice", "video"])
    p.add_argument("--scenarios", default=str(PROJECT_ROOT / "scenarios"),
                   help="YAML dir/file or CSV sheet (id,task,prompt,expected,required_text)")
    p.add_argument("--models", default=str(PROJECT_ROOT / "configs" / "models.yaml"))
    p.add_argument("--budget", type=float, default=None,
                   help="hard USD cap; pre-flight refuses, mid-run aborts")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--run", default=None, help="existing run id to resume")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("judge", help="blind-judge a generated run, then score it")
    p.add_argument("--run", required=True)
    p.add_argument("--models", default=str(PROJECT_ROOT / "configs" / "models.yaml"))
    p.set_defaults(fn=cmd_judge)

    p = sub.add_parser("score", help="re-score from stored criterion scores (free)")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("report", help="build report.html for a run; pass --run "
                                      "twice for a combined tabbed report")
    p.add_argument("--run", required=True, action="append",
                   help="run id (repeat to combine runs into one dashboard)")
    p.add_argument("--out", default=None,
                   help="output path for a combined report (default runs/combined-<ts>.html)")
    p.add_argument("--hide-industry", action="append", default=[],
                   help="industry name to hide from the report (repeatable); its "
                        "scenarios are shown under their next 'also' industry")
    p.add_argument("--brief", action="store_true",
                   help="combined report as a one-page executive summary: no "
                        "tabs, no task/family tables, no per-scenario evidence")
    p.add_argument("--open", action="store_true")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("cost", help="cost rollup from telemetry (gen vs judge)")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_cost)

    p = sub.add_parser("index", help="add browse views to a run: outputs/by-model/ "
                                     "symlinks + INDEX.csv of every image")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("fixjsonl", help="restore one-record-per-line layout in a "
                                        "run's JSONL files after an editor "
                                        "pretty-printed them (backup kept)")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_fixjsonl)

    p = sub.add_parser("summary", help="telemetry summary across runs: ratings, "
                                       "latency, reliability, cost, completion")
    p.add_argument("--run", action="append", default=None,
                   help="run id (repeatable; default: every run in runs/)")
    p.add_argument("--csv", default=None, help="write per-cell rows to this CSV")
    p.add_argument("--json", default=None, help="write aggregate JSON here")
    p.set_defaults(fn=cmd_summary)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
