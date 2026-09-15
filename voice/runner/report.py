"""
Scoring rollup and the HTML report (plan v1.2 sections 14 and 16).

One static file per run, opened straight from the run folder with no server,
written twice from one context: report.html (internal) and report-client.html.
The page is the shared report kit's (shared/report_kit) - the same layout,
sections and styling as every other lane; runner/kit_context.py arranges this
run's numbers for it and runner/templates/lane_hooks.j2 draws the audio.

FOUR COLUMNS, NEVER ONE. Quality, cost, latency and reliability sit side by
side and a human decides. A model that is 20% better and 5x the price is a
business decision, not an arithmetic one.
"""

from __future__ import annotations

import difflib
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import calibration as calib
from .normalize import normalize
from .rubrics import Rubric
from .scoring import (TIE_BAND, ScoredCell, ModelSummary, paired_wtl, score_cell,
                      summarise, verdict)
from .telemetry import (RunPaths, artefact_url, incomplete_scenarios, read_manifest, read_stream,
                        write_manifest)


@dataclass
class CellStats:
    attempts: int = 0
    succeeded: bool = False
    latency_ms: int | None = None
    gen_cost: int = 0
    asr_cost: int = 0
    judge_cost: int = 0
    status: str = "not attempted"
    params_unsupported: tuple[str, ...] = ()
    voice_logical: str | None = None
    voice_provider_id: str | None = None
    duration_s: float | None = None
    cost_exact: bool = True


def collect_stats(paths: RunPaths) -> dict[str, CellStats]:
    """Fold telemetry.jsonl into one record per cell."""
    stats: dict[str, CellStats] = {}
    for row in read_stream(paths, "telemetry"):
        sid, mid = row.get("scenario_id"), row.get("model_id")
        if not sid or not mid:
            continue
        key = f"{sid}|{mid}"
        st = stats.setdefault(key, CellStats())
        if row.get("step") == "asr":
            st.asr_cost += int((row.get("cost") or {}).get("micro_usd", 0))
            continue
        # A generation attempt.
        if row.get("attempt", 0) >= 1:
            st.attempts += 1
        st.gen_cost += int((row.get("cost") or {}).get("micro_usd", 0))
        status = row.get("status")
        st.status = status or st.status
        if status in ("ok", "resumed"):
            st.succeeded = True
            if row.get("latency_ms") is not None:
                st.latency_ms = int(row["latency_ms"])
            out = row.get("output") or {}
            if out.get("duration_s"):
                st.duration_s = float(out["duration_s"])
            st.params_unsupported = tuple(row.get("params_unsupported") or ())
            v = row.get("voice") or {}
            st.voice_logical = v.get("logical") or st.voice_logical
            st.voice_provider_id = v.get("provider_voice_id") or st.voice_provider_id
            cost = row.get("cost") or {}
            if cost.get("usage_exact") is False:
                st.cost_exact = False
    for row in read_stream(paths, "judge"):
        key = f"{row.get('scenario_id')}|{row.get('model_id')}"
        if key in stats:
            stats[key].judge_cost += int((row.get("cost") or {}).get("micro_usd", 0))
    return stats


def build_scores(
    paths: RunPaths, scenarios: list, rubrics: dict[str, Rubric], project_root: Path
) -> tuple[list[ScoredCell], calib.CalibrationState, dict[str, Any]]:
    """
    Score every cell, then evaluate the calibration gate against the judge's
    own composites from this run.

    Order matters: calibration needs judge composites, and scoring needs the
    calibration verdict to mark cells trusted or not. So we score once with
    calibration assumed FALSE to obtain the composites, evaluate the gate,
    then score again for real. Both passes are pure arithmetic over stored
    records - no spend, no regeneration.
    """
    by_id = {s.id: s for s in scenarios}
    checks = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "checks")}
    judges = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "judge")}

    class _Check:
        def __init__(self, rec: dict[str, Any]) -> None:
            self.measurements = rec.get("measurements", {})
            self.failed_gates = rec.get("failed_gates", [])
            self.passed = bool(rec.get("passed"))
            self.raw = rec

    class _Judge:
        def __init__(self, rec: dict[str, Any]) -> None:
            self.status = rec.get("status", "unjudged")
            self.error = rec.get("error")
            scores = rec.get("scores") or {}
            reasoning = rec.get("reasoning") or {}
            self.criteria = [
                type("JC", (), {"name": k, "score": float(v), "reasoning": reasoning.get(k, "")})()
                for k, v in scores.items()
            ]
            self.raw = rec

    # A scenario not every model answered is a GAP, not a result. Scoring the
    # arm that survived would credit it for a comparison that never happened,
    # so those cells are marked `incomplete` with no score - excluded from the
    # mean by summarise(), and from paired win/tie/loss by score is None.
    partial = incomplete_scenarios(paths)

    def _pass(calibrated: bool) -> list[ScoredCell]:
        out: list[ScoredCell] = []
        for key, crec in checks.items():
            sid, mid = key.split("|", 1)
            scenario = by_id.get(sid)
            if scenario is None:
                continue
            rubric = rubrics[scenario.task]
            if sid in partial:
                out.append(
                    ScoredCell(
                        scenario_id=sid, model_id=mid, task=scenario.task,
                        status="incomplete", rubric_hash=rubric.rubric_hash, score=None,
                        note="not every model answered this scenario - one arm alone is "
                             "not comparable, so it is excluded rather than scored",
                    )
                )
                continue
            jrec = judges.get(key)
            out.append(
                score_cell(
                    scenario,
                    rubric,
                    mid,
                    _Check(crec),
                    _Judge(jrec) if jrec else None,
                    calibrated,
                )
            )
        return out

    provisional = _pass(False)
    composites = {f"{c.scenario_id}|{c.model_id}": c.score for c in provisional if c.score is not None}

    any_rubric = next(iter(rubrics.values()))
    judge_model = next((r.get("judge_model") for r in judges.values() if r.get("judge_model")), "unknown")
    state = calib.evaluate(project_root, any_rubric.rubric_hash, judge_model, composites)

    cells = _pass(state.passed)

    # scores.jsonl is rewritten from scratch every time the report runs. It is
    # DERIVED entirely from stored criterion scores and stored measurements, so
    # re-scoring after a weight edit costs nothing, regenerates no audio and
    # re-judges nothing. It is the one file in the run folder that is not
    # append-only, precisely because it is not evidence - it is arithmetic.
    path = paths.stream("scores")
    if path.exists():
        path.unlink()
    from .telemetry import Telemetry

    tel = Telemetry(paths, paths.run_id)
    for cell in sorted(cells, key=lambda c: (c.scenario_id, c.model_id)):
        tel.write("scores", cell.as_record)

    return cells, state, {"judge_model": judge_model}


def word_diff(script: str, transcript: str) -> list[dict[str, str]]:
    """Word-level diff of the NORMALIZED pair - what the gate actually compared."""
    a, b = normalize(script).split(), normalize(transcript).split()
    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            out.append({"kind": "same", "text": " ".join(a[i1:i2])})
        elif tag == "delete":
            out.append({"kind": "missing", "text": " ".join(a[i1:i2])})
        elif tag == "insert":
            out.append({"kind": "added", "text": " ".join(b[j1:j2])})
        else:
            out.append({"kind": "missing", "text": " ".join(a[i1:i2])})
            out.append({"kind": "added", "text": " ".join(b[j1:j2])})
    return out


def render(
    paths: RunPaths,
    scenarios: list,
    rubrics: dict[str, Rubric],
    registry,
    project_root: Path,
) -> Path:
    manifest = read_manifest(paths)
    cells, state, meta = build_scores(paths, scenarios, rubrics, project_root)
    stats = collect_stats(paths)
    by_id = {s.id: s for s in scenarios}
    checks = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "checks")}
    judges = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "judge")}

    model_ids = sorted({c.model_id for c in cells})
    tasks = sorted({c.task for c in cells})
    spec_by_id = {m.id: m for m in registry.models}

    tables = []
    for task in tasks:
        rows = []
        for mid in model_ids:
            summary: ModelSummary = summarise(cells, mid, task)
            if summary.attempted == 0:
                continue
            keys = [f"{c.scenario_id}|{mid}" for c in cells if c.model_id == mid and c.task == task]
            lat = [stats[k].latency_ms for k in keys if k in stats and stats[k].latency_ms]
            attempts = [stats[k].attempts for k in keys if k in stats and stats[k].succeeded]
            ok = sum(1 for k in keys if k in stats and stats[k].succeeded)
            gen = sum(stats[k].gen_cost for k in keys if k in stats)
            asr = sum(stats[k].asr_cost for k in keys if k in stats)
            jud = sum(stats[k].judge_cost for k in keys if k in stats)
            n = max(1, len(keys))
            rows.append(
                {
                    "model": mid,
                    "mean": summary.mean,
                    "worst": summary.worst,
                    "below_five": summary.below_five,
                    "judged": summary.judged,
                    "attempted": summary.attempted,
                    "unjudged": summary.unjudged,
                    "invalid": summary.invalid,
                    "coverage": summary.coverage,
                    "cost_total": gen + asr + jud,
                    "cost_gen": gen,
                    "cost_asr": asr,
                    "cost_judge": jud,
                    "cost_per_scenario": (gen + asr + jud) / n,
                    "cost_exact": all(stats[k].cost_exact for k in keys if k in stats),
                    "p50": statistics.median(lat) if lat else None,
                    "min": min(lat) if lat else None,
                    "max": max(lat) if lat else None,
                    "success": ok / n,
                    "attempts_mean": statistics.mean(attempts) if attempts else None,
                    "wtl": None,
                }
            )
        if len(rows) >= 2:
            for r in rows:
                others = [o for o in rows if o["model"] != r["model"]]
                if others:
                    best_other = max(others, key=lambda o: o["mean"] if o["mean"] is not None else -1)
                    p = paired_wtl(cells, r["model"], best_other["model"], task)
                    r["wtl"] = f"{p.wins}-{p.ties}-{p.losses}"
        rows.sort(key=lambda r: (r["mean"] is None, -(r["mean"] or 0)))
        summaries = [summarise(cells, m, task) for m in model_ids]
        tables.append(
            {
                "task": task,
                "rows": rows,
                "verdict": verdict(summaries, cells, task, state.passed),
                "rubric": rubrics[task],
            }
        )

    evidence = []
    for scenario in sorted(scenarios, key=lambda s: s.id):
        entries = []
        for mid in model_ids:
            key = f"{scenario.id}|{mid}"
            cell = next((c for c in cells if c.scenario_id == scenario.id and c.model_id == mid), None)
            if cell is None and key not in stats:
                continue
            crec = checks.get(key, {})
            jrec = judges.get(key, {})
            st = stats.get(key, CellStats())
            transcript = crec.get("transcript_raw")
            spec = spec_by_id.get(mid)
            ext = "wav"
            # The path is what `exists()` needs; the URL is what the <audio>
            # tag needs, and for a variant scenario they differ - see
            # telemetry.artefact_url. Keeping one string for both is what
            # broke the dashboard's players.
            audio_rel = f"outputs/{scenario.modality}/{scenario.id}/{mid}.{ext}"
            entries.append(
                {
                    "model": mid,
                    "cell": cell,
                    "audio": (artefact_url(audio_rel)
                              if (paths.dir / audio_rel).exists() else None),
                    "transcript": transcript,
                    "diff": word_diff(str(scenario.checks.get("wer_reference") or scenario.text), transcript)
                    if transcript
                    else None,
                    "wer": (crec.get("wer") or {}).get("wer"),
                    "gates": crec.get("gates", []),
                    "passed": crec.get("passed"),
                    "measurements": crec.get("measurements", {}),
                    "judge_reasoning": jrec.get("reasoning", {}),
                    "judge_status": jrec.get("status"),
                    "judge_error": jrec.get("error"),
                    "blind_label": jrec.get("blind_label"),
                    "stats": st,
                    "voice": f"{st.voice_logical} -> {st.voice_provider_id}"
                    if st.voice_provider_id
                    else None,
                    "supports": list(spec.supports) if spec else [],
                }
            )
        if entries:
            evidence.append(
                {
                    "scenario": scenario,
                    "entries": entries,
                    "reference_differs": "wer_reference" in (scenario.checks or {}),
                }
            )

    voice_map_rows = []
    for mid in model_ids:
        spec = spec_by_id.get(mid)
        if not spec:
            continue
        for logical, provider_voice in spec.voice_map.items():
            voice_map_rows.append({"model": mid, "logical": logical, "provider_voice": provider_voice})

    unsupported_rows = sorted(
        {(k.split("|")[1], p) for k, s in stats.items() for p in s.params_unsupported}
    )

    totals = {
        "gen": sum(s.gen_cost for s in stats.values()),
        "asr": sum(s.asr_cost for s in stats.values()),
        "judge": sum(s.judge_cost for s in stats.values()),
    }
    totals["all"] = totals["gen"] + totals["asr"] + totals["judge"]

    from . import _report_kit as kit
    from .kit_context import LANE, run_context

    ctx = run_context(
        manifest=manifest, cells=cells, tables=tables, evidence_rows=evidence,
        calibration=state, totals=totals, rubrics=rubrics, judge_model=meta["judge_model"],
        voice_map_rows=voice_map_rows, unsupported_rows=unsupported_rows,
        skipped=manifest.get("skipped", []), tie_band=TIE_BAND, paired_wtl=paired_wtl,
        stats=stats, model_ids=model_ids,
    )
    kit.render_run(LANE, ctx, paths.dir)

    manifest["calibration"] = state.as_record
    manifest["cost_totals_micro_usd"] = totals
    write_manifest(paths, manifest)
    return paths.report
