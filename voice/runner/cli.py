"""
The CLI: run - judge - report - cost - calibrate (plan v1.2 section 19).

Four separate commands on purpose. Re-judging must never re-generate media,
and a report tweak must cost nothing. A run is never "done" as a boolean;
generated, judged and scored are separate states because they are separate
commands.

VOICE RUNS AS ITS OWN PROCESS. `--modality voice` filters the scenarios, the
models and the output directory, so it can run at the same time as an image
process and the two never touch each other's files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import calibration as calib
from . import matrix as matrix_mod
from .asr import Asr
from .cost import fmt_usd
from .dashboard import render_dashboard
from .generate import Budget, preflight_estimate, run_generation
from .judge import blind_labels_for, build_judge, judge_cell
from .models import load_registry, preflight
from .mos import build_predictor
from .report import render
from .rubrics import load_rubric
from .scenarios import load_scenarios, repeat_scenarios
from .summary import write_summary
from .telemetry import (
    RunPaths,
    Telemetry,
    latest_run,
    new_run_id,
    read_manifest,
    read_stream,
    write_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(*paths: Path) -> list[str]:
    """
    Minimal .env reader - no dependency, no export, no overwrite of a value
    already in the environment. Returns the names it set, never the values.
    """
    loaded: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _rubrics_for(configs: Path, modality: str, tasks: list[str]) -> dict:
    return {t: load_rubric(configs, modality, t) for t in tasks}


def _resolve_run(runs_root: Path, run_id: str | None, modality: str | None) -> RunPaths:
    rid = run_id or latest_run(runs_root, modality)
    if not rid:
        raise SystemExit(f"no runs found under {runs_root} - run `genmedia run` first")
    paths = RunPaths(runs_root, rid)
    if not paths.dir.exists():
        raise SystemExit(f"no such run: {paths.dir}")
    return paths


def _run_per_scenario(args: argparse.Namespace) -> int:
    """Re-enter cmd_run once per scenario, each pass minting its own run."""
    scenarios = repeat_scenarios(
        load_scenarios(Path(args.scenarios), args.modality),
        int(getattr(args, "repeat", 1) or 1),
    )
    if not scenarios:
        raise SystemExit(f"no {args.modality} scenarios under {args.scenarios}")

    minted: list[str] = []
    worst = 0

    # --run names ONE existing run, so it cannot serve a fan-out over several
    # scenarios. It used to be dropped SILENTLY here, which is worse than
    # refusing it: on 2026-09-03 two runs were resumed with --run, the flag
    # was discarded, fresh runs were minted, and 741 characters of a metered
    # ElevenLabs quota were spent re-generating clips that were already on
    # disk. A flag that costs money must never be ignored without saying so.
    if getattr(args, "run", None) and len(scenarios) > 1:
        raise SystemExit(
            f"--run {args.run} names one existing run, but --scenarios matched "
            f"{len(scenarios)} scenarios and this mode mints one run each. "
            f"Point --scenarios at a single file to resume that run, or drop "
            f"--run to start fresh ones."
        )

    print(f"\nPER-SCENARIO MODE  {len(scenarios)} scenario(s) -> {len(scenarios)} run(s)")
    print("  one run per scenario; use --bundle for a single folder covering all of them")

    # Confirm ONCE for the whole fan-out. Prompting per scenario would ask five
    # times for one decision, and a prompt asked five times is a prompt people
    # learn to hit blindly. The per-scenario passes then run unattended, each
    # still bound by its own --budget.
    if not args.yes:
        registry = load_registry(Path(args.configs))
        models = registry.for_modality(args.modality)
        mx = matrix_mod.build(scenarios, models)
        est, _ = preflight_estimate(mx, registry.asr.price if registry.asr else None)
        print(f"\n  {len(mx.cells)} cells across {len(scenarios)} runs · "
              f"estimated {fmt_usd(est)} in total")
        try:
            if input("  proceed with all of them? [y/N] ").strip().lower() != "y":
                print("  aborted before any call.")
                return 1
        except EOFError:
            print("  no input available — aborted before any call.")
            return 1

    for i, s in enumerate(scenarios, 1):
        print("\n" + "-" * 62)
        print(f"  SCENARIO {i}/{len(scenarios)}  {s.id}")
        print("-" * 62)
        sub = argparse.Namespace(**vars(args))
        sub._single = True
        sub.yes = True          # already confirmed once, above
        # Point this pass at exactly one scenario file.
        sub.scenarios = s.source_path.split(":")[0]
        sub.label = f"{args.label}-{s.id}" if args.label else s.id
        # Exactly one scenario: --run is unambiguous and resumes that run,
        # reusing the clips already paid for. Guarded above for the fan-out.
        sub.run = getattr(args, "run", None) if len(scenarios) == 1 else None
        rc = cmd_run(sub)
        worst = max(worst, rc)
        if getattr(sub, "minted_run_id", None):
            minted.append(sub.minted_run_id)
        if rc == 2:
            print("  stopping: preflight or budget refused the run")
            break

    args.minted_run_ids = minted
    args.minted_run_id = minted[-1] if minted else None
    print("\n" + "=" * 62)
    print(f"  {len(minted)} run(s) created, one per scenario:")
    for r in minted:
        print(f"    {r}")
    print("=" * 62)
    return worst


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """
    ONE SCENARIO, ONE RUN (default).

    A run is the evidence for one question: "how do these models handle THIS
    script". Bundling five scenarios into one folder made a run answer five
    questions at once, which meant re-running one scenario meant touching the
    others' evidence, and a run's manifest described a set rather than a case.

    Per-scenario runs also make the atomicity rule and the folder agree: the
    scenario is already the unit of completion, so it is now the unit of
    filing too. Aggregation across scenarios has not been lost - it moved to
    the dashboard, which was always the surface that spanned runs.

    `--bundle` restores the old behaviour when you genuinely want one folder
    for a whole sweep.
    """
    if not getattr(args, "bundle", False) and not getattr(args, "_single", False):
        return _run_per_scenario(args)
    configs = Path(args.configs)
    runs_root = Path(args.runs)
    registry = load_registry(configs)

    scenarios = repeat_scenarios(
        load_scenarios(Path(args.scenarios), args.modality),
        int(getattr(args, "repeat", 1) or 1),
    )
    if not scenarios:
        raise SystemExit(f"no {args.modality} scenarios under {args.scenarios}")

    models = registry.for_modality(args.modality)
    if args.models:
        wanted = {m.strip() for m in args.models.split(",")}
        unknown = wanted - {m.id for m in registry.for_modality(args.modality, enabled_only=False)}
        if unknown:
            raise SystemExit(f"unknown model id(s): {sorted(unknown)}")
        models = [m for m in registry.for_modality(args.modality, enabled_only=False) if m.id in wanted]
    if not models:
        raise SystemExit(f"no enabled {args.modality} models in {registry.config_path}")

    needs = [registry.asr] if registry.asr else []
    pf = preflight(registry, args.modality, needs)
    print(f"\nPREFLIGHT - credentials, before any spend")
    print(pf.render())
    if pf.missing_credentials:
        print(
            "\nHard stop: an enabled model or service has no credential. Set the variable(s) "
            "above, or set `enabled: false` on the model in configs/models.yaml.\n"
        )
        return 2

    mx = matrix_mod.build(scenarios, models)
    if not mx.cells:
        raise SystemExit("the matrix is empty - no model supports any loaded scenario's task")

    predictor, mos_fallback = build_predictor(registry.mos, PROJECT_ROOT)
    if mos_fallback:
        print(f"\nNOTE  {mos_fallback}")

    est, notes = preflight_estimate(mx, registry.asr.price if registry.asr else None)
    print(f"\nMATRIX  {len(mx.cells)} cells ({len(scenarios)} scenarios x {len(models)} models)")
    for s in mx.skipped:
        print(f"  n/a      {s.scenario_id:9} {s.model_id:26} {s.reason}")
    print(f"\nESTIMATE {fmt_usd(est)} for the whole run - {notes[0]}")
    if args.budget is not None and est > args.budget * 1_000_000:
        print(
            f"\nRefusing to start: the estimate exceeds --budget ${args.budget:.2f}. "
            f"Raise the budget or cut scenarios."
        )
        return 2
    if not args.yes:
        reply = input("proceed? [y/N] ").strip().lower()
        if reply != "y":
            print("aborted before any call.")
            return 1

    run_id = args.run or new_run_id(f"{args.modality}-{args.label}" if args.label else args.modality)
    paths = RunPaths(runs_root, run_id).ensure()
    tel = Telemetry(paths, run_id)

    # Freeze the scenario set into the run folder. A scenario edited tomorrow
    # belongs to the next run, and this copy is what proves what THIS run ran.
    for s in scenarios:
        src = Path(s.source_path.split(":")[0])
        if src.exists():
            (paths.scenarios_dir / src.name).write_bytes(src.read_bytes())

    tasks = sorted({s.task for s in scenarios})
    rubrics = _rubrics_for(configs, args.modality, tasks)
    scenario_set_hash = hashlib.sha256(
        "".join(s.scenario_hash for s in scenarios).encode()
    ).hexdigest()

    manifest = {
        "run_id": run_id,
        "modality": args.modality,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_sha": git_sha(),
        "plan_version": "1.2",
        "scenario_count": len(scenarios),
        "scenario_set_hash": scenario_set_hash,
        "scenarios": [
            {
                "id": s.id,
                "task": s.task,
                "hash": s.scenario_hash,
                "source": s.source_path,
                "source_format": s.source_format,
            }
            for s in scenarios
        ],
        "models": [
            {
                "id": m.id,
                "adapter": m.adapter,
                "provider": m.provider,
                "provider_model": m.provider_model,
                "supports": list(m.supports),
                # The pinned voice per provider, recorded because it is a
                # DECLARED difference between arms, not a hidden one.
                "voice_map": m.voice_map,
                "limits": {"max_concurrency": m.limits.max_concurrency, "rpm": m.limits.rpm},
                "price": m.price.as_record,
            }
            for m in models
        ],
        "rubrics": {t: {"hash": r.rubric_hash, "sources": list(r.source_files)} for t, r in rubrics.items()},
        "asr": {"provider_model": registry.asr.provider_model, "price": registry.asr.price.as_record}
        if registry.asr
        else None,
        "judge": {
            "provider_model": registry.judges[args.modality].provider_model,
            "temperature": registry.judges[args.modality].temperature,
        }
        if args.modality in registry.judges
        else None,
        "mos": {"predictor": registry.mos.predictor, "configured": registry.mos.model_path},
        "mos_fallback_reason": mos_fallback,
        "budget_usd": args.budget,
        "preflight_estimate_micro_usd": est,
        "skipped": [
            {"scenario_id": s.scenario_id, "model_id": s.model_id, "reason": s.reason} for s in mx.skipped
        ],
    }
    write_manifest(paths, manifest)

    print(f"\nRUN     {run_id}")
    budget = Budget(args.budget)
    asr = Asr(registry.asr) if registry.asr else None
    outcomes = run_generation(
        mx, paths, tel, asr, predictor, budget, workers=args.workers, timeout_s=args.timeout
    )

    ok = sum(1 for o in outcomes if o.status == "ok")
    gated = sum(1 for o in outcomes if o.check_report is not None and o.check_report.passed)
    print(
        f"\nDONE    {ok}/{len(outcomes)} generated, {gated} passed all gates, "
        f"spent {fmt_usd(budget.spent_micro)}"
    )
    print(f"        next: python -m runner.cli judge --run {run_id}")
    args.minted_run_id = run_id
    return 0


# --------------------------------------------------------------------------
# judge
# --------------------------------------------------------------------------

def cmd_judge(args: argparse.Namespace) -> int:
    configs = Path(args.configs)
    registry = load_registry(configs)
    paths = _resolve_run(Path(args.runs), args.run, args.modality)
    manifest = read_manifest(paths)
    modality = manifest["modality"]

    spec = registry.judges.get(modality)
    if spec is None:
        raise SystemExit(f"no judge configured for modality '{modality}' in {registry.config_path}")
    if not spec.has_credential:
        print(f"Hard stop: the judge needs ${spec.auth_env}, which is not set.")
        return 2

    scenarios = load_scenarios(Path(args.scenarios), modality)
    by_id = {s.id: s for s in scenarios}
    rubrics = _rubrics_for(configs, modality, sorted({s.task for s in scenarios}))
    tel = Telemetry(paths, paths.run_id)

    already = {f"{r['scenario_id']}|{r['model_id']}" for r in read_stream(paths, "judge")}
    checks = read_stream(paths, "checks")
    backend = build_judge(spec)
    # In the chained `all` flow, args.budget is the GENERATION cap. Judging
    # gets its own, so a generous generation budget cannot silently become
    # a generous judging budget too.
    budget = Budget(getattr(args, "judge_budget", None) or args.budget)

    # Blind labels are assigned per scenario over the models that actually
    # produced a clip for it, seeded by the scenario id.
    per_scenario: dict[str, list[str]] = {}
    for rec in checks:
        per_scenario.setdefault(rec["scenario_id"], []).append(rec["model_id"])
    labels = {sid: blind_labels_for(sid, sorted(mids)) for sid, mids in per_scenario.items()}

    judged = skipped = failed = 0
    print(f"\nJUDGE   {paths.run_id} with {spec.provider_model} (temperature {spec.temperature})")
    for rec in sorted(checks, key=lambda r: (r["scenario_id"], r["model_id"])):
        sid, mid = rec["scenario_id"], rec["model_id"]
        key = f"{sid}|{mid}"
        scenario = by_id.get(sid)
        if scenario is None:
            continue
        if key in already:
            print(f"  resume   {sid:9} {mid:26} already judged")
            continue
        if not rec.get("passed"):
            # Gate failure -> the judge is never called and no judge cost is
            # incurred. The cell scores 0 as an earned invalid, not unjudged.
            print(f"  gated    {sid:9} {mid:26} failed {rec.get('failed_gates')} - judge not called")
            skipped += 1
            continue

        audio = paths.dir / f"outputs/{modality}/{sid}/{mid}.wav"
        if not audio.exists():
            print(f"  missing  {sid:9} {mid:26} no audio on disk")
            skipped += 1
            continue

        # Only measurements go in as facts - never the other models' scores.
        m = rec.get("measurements", {})
        facts = {}
        if "normalized_wer" in m:
            facts["word error rate vs the script (normalized, measured by ASR)"] = (
                f"{m['normalized_wer']:.1%}"
            )
        if "duration_s" in m:
            facts["clip duration"] = f"{m['duration_s']:.1f}s"
        if "audio_quality_1_5" in m:
            kind = "perceptual MOS" if m.get("audio_quality_is_mos") else "objective signal metric, not a MOS"
            facts[f"objective audio quality ({kind}, 1-5)"] = f"{m['audio_quality_1_5']:.2f}"
        if "rms_dbfs" in m:
            facts["loudness"] = f"{m['rms_dbfs']:.1f} dBFS"

        try:
            budget.guard(f"judge {key}")
        except Exception as exc:  # noqa: BLE001
            print(f"  BUDGET   {exc}")
            break

        record = judge_cell(
            backend, spec, scenario, rubrics[scenario.task], mid, labels[sid][mid], audio, facts
        )
        tel.write("judge", record.as_record)
        if record.cost:
            budget.add(record.cost.micro_usd)
        if record.status == "judged":
            judged += 1
            scores = ", ".join(f"{c.name}={c.score:g}" for c in record.criteria)
            print(f"  judged   {sid:9} {mid:26} as '{record.blind_label}'  {scores}")
        else:
            failed += 1
            print(f"  UNJUDGED {sid:9} {mid:26} {record.error}")

    print(f"\nDONE    {judged} judged, {skipped} gated/skipped, {failed} unjudged, spent {fmt_usd(budget.spent_micro)}")
    print(f"        next: python -m runner.cli report --run {paths.run_id}")
    return 0


# --------------------------------------------------------------------------
# report / cost / calibrate
# --------------------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> int:
    configs = Path(args.configs)
    registry = load_registry(configs)
    paths = _resolve_run(Path(args.runs), args.run, args.modality)
    manifest = read_manifest(paths)
    modality = manifest["modality"]
    scenarios = load_scenarios(Path(args.scenarios), modality)
    rubrics = _rubrics_for(configs, modality, sorted({s.task for s in scenarios}))
    out = render(paths, scenarios, rubrics, registry, PROJECT_ROOT)
    # The summary is derived from the same streams the report just read, so
    # writing it here means the common path never leaves a run without one.
    print(f"summary: {write_summary(paths)}")
    print(f"report: {out}")
    if args.open:
        subprocess.run(["open", str(out)], check=False)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """The cross-run view. Pure arithmetic over stored records - no spend."""
    out = render_dashboard(Path(args.runs), args.modality)
    print(f"dashboard: {out}")
    if args.open:
        subprocess.run(["open", str(out)], check=False)
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    """
    run -> judge -> report -> dashboard, in one command.

    The four stay separate underneath, because re-judging must never
    regenerate audio and re-reporting must cost nothing. This only removes the
    two ways the manual sequence goes wrong: forgetting a step, and pointing a
    later step at the wrong run because it defaulted to "the newest one".
    The run id minted by `run` is threaded through explicitly.

    STOPS ON FAILURE. If generation fails there is nothing to judge, and
    judging a half-generated run would produce a comparison with a silent hole
    in it.
    """
    steps: list[tuple[str, int]] = []

    print("\n" + "=" * 62 + "\n  STEP 1/4  generate + deterministic checks\n" + "=" * 62)
    rc = cmd_run(args)
    steps.append(("run", rc))
    if rc != 0:
        print(f"\nSTOPPED at `run` (exit {rc}). Nothing was judged.")
        return rc

    # In per-scenario mode `run` mints SEVERAL runs, and every one of them
    # needs judging and reporting - not just the last.
    ids = getattr(args, "minted_run_ids", None) or [getattr(args, "minted_run_id", None)]
    ids = [i for i in ids if i]

    print("\n" + "=" * 62 + f"\n  STEP 2/4  blind judging ({len(ids)} run(s))\n" + "=" * 62)
    for rid in ids:
        args.run = rid
        rc = cmd_judge(args)
        if rc != 0:
            print(f"\nSTOPPED at `judge` on {rid} (exit {rc}). Audio and checks are on disk;")
            print(f"resume with: python -m runner.cli judge --run {rid}")
            steps.append(("judge", rc))
            return rc
    steps.append(("judge", 0))

    print("\n" + "=" * 62 + f"\n  STEP 3/4  score + report ({len(ids)} run(s))\n" + "=" * 62)
    for rid in ids:
        args.run = rid
        rc = cmd_report(args)
        if rc != 0:
            steps.append(("report", rc))
            return rc
    steps.append(("report", 0))
    rc = 0

    print("\n" + "=" * 62 + "\n  STEP 4/4  cross-run dashboard\n" + "=" * 62)
    rc = cmd_dashboard(args)
    steps.append(("dashboard", rc))

    print("\n" + "=" * 62)
    print("  PIPELINE COMPLETE  " + " · ".join(f"{n} ok" for n, c in steps if c == 0))
    for rid in ids:
        print(f"  run    {rid}")
        print(f"         {Path(args.runs) / rid / 'report.html'}")
    print(f"  board  {Path(args.runs) / 'index.html'}")
    print("=" * 62)
    return rc


def cmd_summarise(args: argparse.Namespace) -> int:
    """Fold a run's four streams into summary.json. Derived, no spend."""
    paths = _resolve_run(Path(args.runs), args.run, args.modality)
    out = write_summary(paths)
    print(f"summary: {out}")
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    paths = _resolve_run(Path(args.runs), args.run, args.modality)
    gen = asr = judge = 0
    est_cells = 0
    for row in read_stream(paths, "telemetry"):
        c = row.get("cost") or {}
        micro = int(c.get("micro_usd", 0))
        if row.get("step") == "asr":
            asr += micro
        else:
            gen += micro
        if c.get("usage_exact") is False:
            est_cells += 1
    for row in read_stream(paths, "judge"):
        judge += int((row.get("cost") or {}).get("micro_usd", 0))
    print(f"\nCOST    {paths.run_id}")
    print(f"  generation  {fmt_usd(gen)}")
    print(f"  asr         {fmt_usd(asr)}")
    print(f"  judge       {fmt_usd(judge)}")
    print(f"  TOTAL       {fmt_usd(gen + asr + judge)}")
    if est_cells:
        print(f"  note: {est_cells} generation row(s) priced from declared estimation assumptions (badged 'est')")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    configs = Path(args.configs)
    paths = _resolve_run(Path(args.runs), args.run, args.modality)
    manifest = read_manifest(paths)
    modality = manifest["modality"]
    scenarios = load_scenarios(Path(args.scenarios), modality)
    rubrics = _rubrics_for(configs, modality, sorted({s.task for s in scenarios}))
    from .report import build_scores

    cells, state, meta = build_scores(paths, scenarios, rubrics, PROJECT_ROOT)
    if args.init:
        clips = calib.pick_calibration_clips(cells)
        if not clips:
            raise SystemExit("no scored cells in this run to calibrate against")
        any_rubric = next(iter(rubrics.values()))
        path = calib.write_template(PROJECT_ROOT, paths.run_id, any_rubric.rubric_hash, meta["judge_model"], clips)
        print(f"wrote {path}")
        print("Two people score these five clips 0-10, then re-run `report`:")
        for c in clips:
            sid, mid = c.split("|")
            print(f"  runs/{paths.run_id}/outputs/{modality}/{sid}/{mid}.wav")
        return 0
    print(json.dumps(state.as_record, indent=2))
    return 0 if state.passed else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env")

    p = argparse.ArgumentParser(prog="genmedia", description="GenMedia model comparison (plan v1.2)")
    p.add_argument("--configs", default=str(PROJECT_ROOT / "configs"))
    p.add_argument("--scenarios", default=str(PROJECT_ROOT / "scenarios"))
    p.add_argument("--runs", default=str(PROJECT_ROOT / "runs"))
    p.add_argument("--modality", default="voice", choices=["voice", "image"])
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="generate + deterministic checks + measurements")
    r.add_argument("--budget", type=float, default=5.0, help="hard cap in USD")
    r.add_argument("--models", help="comma-separated model ids (default: every enabled one)")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--timeout", type=float, default=180.0)
    r.add_argument("--run", help="reuse an existing run id (resume)")
    r.add_argument("--label", help="suffix for the generated run id")
    r.add_argument("--repeat", type=int, default=1,
                   help="issue each scenario N times in ONE run - a batch, for the "
                        "throughput scenarios. Not the same as running twice on "
                        "different days, which measures run-to-run noise.")
    r.add_argument("--yes", action="store_true", help="skip the pre-flight confirmation")
    r.add_argument("--bundle", action="store_true",
                   help="put every scenario in ONE run folder (default: one run per scenario)")
    r.set_defaults(fn=cmd_run)

    j = sub.add_parser("judge", help="blind judging of the clips that passed their gates")
    j.add_argument("--run")
    j.add_argument("--budget", type=float, default=2.0)
    j.set_defaults(fn=cmd_judge)

    rp = sub.add_parser("report", help="score + render report.html")
    rp.add_argument("--run")
    rp.add_argument("--open", action="store_true")
    rp.set_defaults(fn=cmd_report)

    d = sub.add_parser("dashboard", help="cross-run GenMedia runs dashboard")
    d.add_argument("--open", action="store_true")
    d.set_defaults(fn=cmd_dashboard)

    al = sub.add_parser("all", help="run + judge + report + dashboard, in one command")
    al.add_argument("--budget", type=float, default=5.0, help="hard cap for GENERATION, in USD")
    al.add_argument("--judge-budget", type=float, default=2.0, dest="judge_budget",
                    help="hard cap for JUDGING, in USD")
    al.add_argument("--models", help="comma-separated model ids (default: every enabled one)")
    al.add_argument("--workers", type=int, default=4)
    al.add_argument("--timeout", type=float, default=180.0)
    al.add_argument("--run", help="reuse an existing run id (resume)")
    al.add_argument("--label", help="suffix for the generated run id")
    al.add_argument("--repeat", type=int, default=1,
                    help="issue each scenario N times in ONE run (batch throughput)")
    al.add_argument("--yes", action="store_true", help="skip the pre-flight confirmation")
    al.add_argument("--open", action="store_true", help="open the report and dashboard when done")
    al.add_argument("--bundle", action="store_true",
                    help="put every scenario in ONE run folder (default: one run per scenario)")
    al.set_defaults(fn=cmd_all)

    sm = sub.add_parser("summarise", help="write summary.json for a run (derived, no spend)")
    sm.add_argument("--run")
    sm.set_defaults(fn=cmd_summarise)

    c = sub.add_parser("cost", help="cost breakdown for a run")
    c.add_argument("--run")
    c.set_defaults(fn=cmd_cost)

    cal = sub.add_parser("calibrate", help="the 2-humans x 5-clips gate")
    cal.add_argument("--run")
    cal.add_argument("--init", action="store_true", help="write the template for reviewers to fill in")
    cal.set_defaults(fn=cmd_calibrate)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
