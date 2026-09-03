"""
Per-run summary - one small JSON per run, ready for a dashboard to read.

WHY THIS EXISTS.
A run folder is append-only evidence: telemetry.jsonl, checks.jsonl,
judge.jsonl, scores.jsonl. That is the right shape for proving a number, and
the wrong shape for displaying one - anything wanting to show a run has to
fold four streams itself and will fold them slightly differently each time.
`summary.json` is that fold, done once, by the code that already knows the
rules (unjudged is excluded, invalid is an earned zero, absent stays absent).

IT IS DERIVED, NOT EVIDENCE. Delete it and `genmedia summarise` rebuilds it
from the streams. Nothing reads it to make a decision; it exists to be
displayed. That is why it is the one file in a run folder that is rewritten
rather than appended.

WHAT IT DELIBERATELY DOES NOT DO. No blended score. No winner. Absent
measurements are null, never zero. A scenario answered by only some of the
models is reported as incomplete and its models are NOT credited with a
result, because a half-answered scenario is not comparable.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from .telemetry import RunPaths, read_manifest, read_stream


def _stats(values: list[float]) -> dict[str, Any] | None:
    """avg / min / max / n, or None when there is nothing to average."""
    if not values:
        return None
    return {
        "n": len(values),
        "avg": round(statistics.mean(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "spread": round(max(values) - min(values), 4),
    }


def _throughput(gen_rows: list[dict], check_rows: list[dict], cost_micro: int) -> dict[str, Any]:
    """
    Rates, with the sample size attached to every one of them.

    `n_clips` is not decoration. The workbook asks for 500 support turns and
    200 placeholder lines; a rate estimated from thirty is the same rate with
    wider error bars, and is honest ONLY while the denominator travels with
    it. Quoting "cost per finished minute" without saying over how many clips
    invites it to be read as a measured total.
    """
    seconds = [float(c["measurements"]["duration_s"]) for c in check_rows
               if c.get("measurements", {}).get("duration_s")]
    latencies = [float(g["latency_ms"]) / 1000.0 for g in gen_rows if g.get("latency_ms")]
    audio_s = sum(seconds)
    gen_s = sum(latencies)
    if not seconds:
        return {"n_clips": 0, "measured": False,
                "note": "no clip produced a duration - nothing to divide"}
    return {
        "n_clips": len(seconds),
        "measured": True,
        "audio_seconds": round(audio_s, 2),
        "audio_minutes": round(audio_s / 60.0, 4),
        # The headline a buyer compares across vendors.
        "cost_micro_usd_per_audio_minute": round(cost_micro / (audio_s / 60.0)) if audio_s else None,
        # Generation wall-clock is the SUM of per-call latency, not elapsed
        # time: the runner works several provider lanes concurrently, so
        # elapsed would measure our concurrency rather than the provider's
        # speed. Named so nobody reads it as "how long the batch took".
        "generation_seconds_serial": round(gen_s, 2),
        "realtime_factor": round(audio_s / gen_s, 3) if gen_s else None,
        "clips_per_generation_minute": round(len(seconds) / (gen_s / 60.0), 2) if gen_s else None,
        "audio_seconds_per_clip": _stats(seconds),
    }


def build_summary(paths: RunPaths) -> dict[str, Any]:
    manifest = read_manifest(paths)
    modality = manifest.get("modality", "voice")

    checks = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "checks")}
    judges = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "judge")}
    scores = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "scores")}

    gen: dict[str, dict[str, Any]] = {}
    attempts: dict[str, int] = {}
    asr_cost: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []

    for row in read_stream(paths, "telemetry"):
        if row.get("step") == "scenario" and row.get("status") == "incomplete":
            incomplete.append(
                {k: row.get(k) for k in ("scenario_id", "models_done", "models_expected", "missing")}
            )
            continue
        sid, mid = row.get("scenario_id"), row.get("model_id")
        if not sid or not mid:
            continue
        key = f"{sid}|{mid}"
        if row.get("step") == "asr":
            asr_cost[key] = asr_cost.get(key, 0) + int((row.get("cost") or {}).get("micro_usd", 0))
            continue
        if row.get("attempt", 0) >= 1:
            attempts[key] = attempts.get(key, 0) + 1
        if row.get("status") in ("ok", "resumed"):
            gen[key] = row
        elif row.get("status"):
            failures.append(
                {"scenario_id": sid, "model_id": mid, "status": row["status"],
                 "attempt": row.get("attempt"), "error": (row.get("error") or "")[:300]}
            )

    scenario_ids = sorted({k.split("|")[0] for k in checks} | {f["scenario_id"] for f in failures})
    model_ids = [m["id"] for m in manifest.get("models", [])]
    incomplete_ids = {i["scenario_id"] for i in incomplete}

    models: list[dict[str, Any]] = []
    for mid in model_ids:
        spec = next((m for m in manifest["models"] if m["id"] == mid), {})
        keys = [f"{sid}|{mid}" for sid in scenario_ids]
        mine_scores = [scores[k] for k in keys if k in scores]
        mine_checks = [checks[k] for k in keys if k in checks]
        mine_gen = [gen[k] for k in keys if k in gen]

        rated = [s["score"] for s in mine_scores if s.get("score") is not None]
        below5 = sum(1 for s in rated if s < 5.0)

        # Per-criterion averages, so a dashboard can show WHERE a model wins
        # rather than only that it did.
        per_criterion: dict[str, list[float]] = {}
        for s in mine_scores:
            for k, v in (s.get("criterion_scores") or {}).items():
                per_criterion.setdefault(k, []).append(float(v))

        gen_cost = sum(int((g.get("cost") or {}).get("micro_usd", 0)) for g in mine_gen)
        judge_cost = sum(int((judges[k].get("cost") or {}).get("micro_usd", 0))
                         for k in keys if k in judges and judges[k].get("cost"))
        a_cost = sum(asr_cost.get(k, 0) for k in keys)
        n_cells = len([k for k in keys if k in checks or k in scores]) or 1

        gate_fail_counts: dict[str, int] = {}
        for c in mine_checks:
            for g in c.get("failed_gates") or []:
                gate_fail_counts[g] = gate_fail_counts.get(g, 0) + 1

        models.append({
            "model_id": mid,
            "provider": spec.get("provider"),
            "provider_model": spec.get("provider_model"),
            "adapter": spec.get("adapter"),
            "voice_map": spec.get("voice_map"),
            "price": spec.get("price"),

            "rating": {
                "mean": round(statistics.mean(rated), 4) if rated else None,
                "min": round(min(rated), 4) if rated else None,
                "max": round(max(rated), 4) if rated else None,
                "spread": round(max(rated) - min(rated), 4) if len(rated) > 1 else 0.0,
                "scored": len(rated),
                "attempted": len(keys),
                "unjudged": sum(1 for s in mine_scores if s.get("status") == "unjudged"),
                "invalid": sum(1 for s in mine_scores if s.get("status") == "invalid"),
                "below_5": below5,
                # Calibration is a property of the RUN, repeated here so a
                # widget rendering one model cannot show the score without it.
                "calibration_trusted": all(
                    s.get("calibration_trusted", True) for s in mine_scores
                ),
                "per_criterion_avg": {
                    k: round(statistics.mean(v), 4) for k, v in sorted(per_criterion.items())
                },
            },

            "latency_ms": _stats([float(g["latency_ms"]) for g in mine_gen if g.get("latency_ms")]),
            "audio_duration_s": _stats(
                [float(c["measurements"]["duration_s"]) for c in mine_checks
                 if c.get("measurements", {}).get("duration_s")]
            ),
            "normalized_wer": _stats(
                [float(c["measurements"]["normalized_wer"]) for c in mine_checks
                 if c.get("measurements", {}).get("normalized_wer") is not None]
            ),
            "audio_quality_1_5": _stats(
                [float(c["measurements"]["audio_quality_1_5"]) for c in mine_checks
                 if c.get("measurements", {}).get("audio_quality_1_5") is not None]
            ),

            "cost_micro_usd": {
                "generation": gen_cost, "asr": a_cost, "judge": judge_cost,
                "total": gen_cost + a_cost + judge_cost,
                "per_scenario": round((gen_cost + a_cost + judge_cost) / n_cells),
                # An estimated cost must never be read as a measured one.
                "all_exact": all(
                    (g.get("cost") or {}).get("usage_exact") is not False for g in mine_gen
                ),
            },

            # THROUGHPUT - the question a buyer asks at volume, and the one
            # per-scenario cost cannot answer. A model that is cheaper per
            # CALL can be dearer per finished minute if it speaks faster, and
            # a studio ordering 200 placeholder lines is buying wall-clock,
            # not calls. Every input here was already recorded; nothing new is
            # measured, it is only divided.
            "throughput": _throughput(mine_gen, mine_checks, gen_cost + a_cost),

            "reliability": {
                "cells_ok": len(mine_gen),
                "cells_attempted": len(keys),
                "success_after_retries": round(len(mine_gen) / len(keys), 4) if keys else None,
                "attempts_to_success": _stats(
                    [float(attempts[k]) for k in keys if k in gen and k in attempts]
                ),
                "gate_failures": gate_fail_counts,
            },
        })

    return {
        "run_id": manifest["run_id"],
        "modality": modality,
        "started_at": manifest.get("started_at"),
        "git_sha": manifest.get("git_sha"),
        "scenario_set_hash": manifest.get("scenario_set_hash"),
        "rubrics": manifest.get("rubrics"),
        "judge": manifest.get("judge"),
        "asr": (manifest.get("asr") or {}).get("provider_model"),
        "mos_predictor": (manifest.get("mos") or {}).get("predictor"),
        "mos_fallback_reason": manifest.get("mos_fallback_reason"),
        "calibration": manifest.get("calibration"),

        "counts": {
            "scenarios": len(scenario_ids),
            "models": len(model_ids),
            "cells_expected": len(scenario_ids) * max(1, len(model_ids)),
            "cells_generated": len(gen),
            "cells_scored": sum(1 for s in scores.values() if s.get("score") is not None),
            "scenarios_complete": len(scenario_ids) - len(incomplete_ids),
            "scenarios_incomplete": len(incomplete_ids),
        },

        # A scenario answered by only some models is not a result. Named here
        # so a dashboard can exclude it rather than average over a gap.
        "incomplete_scenarios": incomplete,
        "failures": failures,
        "models": models,
        "cost_micro_usd_total": sum(m["cost_micro_usd"]["total"] for m in models),
    }


def write_summary(paths: RunPaths) -> Path:
    out = paths.dir / "summary.json"
    out.write_text(json.dumps(build_summary(paths), indent=2) + "\n", encoding="utf-8")
    return out
