"""Weights -> scenario score -> model score -> paired verdict (plan §14).

    criterion_score  in 0..10        (judge, or derived from a measurement)
    scenario_score   = sum(weight_i * criterion_score_i)     weights sum to 1.0
    model_score      = mean over JUDGED scenarios (invalid = an earned 0;
                       unjudged is EXCLUDED, never 0)

Verdict, per task (either door is enough, coverage >= 80% required):
    A beats B  <=>  mean(A) - mean(B) >= 0.5
               OR   A wins >= 70% of the DECIDED scenarios (ties excluded,
                    tie = |delta| <= 0.5), sign test quoted when decided >= 10.

Re-scoring from stored criterion scores is free: no regeneration, no
re-judging. The rubric hash is stamped into every score row.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from .generate import Manifest
from .loaders import Scenario, effective_criteria, load_rubric
from .telemetry import RunFiles, utcnow

TIE_BAND = 0.5          # |delta| <= 0.5 on the same scenario = tie
MEAN_GAP_DOOR = 0.5
WIN_RATE_DOOR = 0.70
COVERAGE_FLOOR = 0.80
SIGN_TEST_MIN_N = 10


# --------------------------------------------------------------------------
# Measured criterion mappings — facts to 0-10, defined by code
# --------------------------------------------------------------------------

def text_accuracy_from_ocr(match: float) -> float:
    """Plan §12: match 1.0 -> 10; below 0.6 -> 0; linear in between."""
    if match >= 1.0:
        return 10.0
    if match < 0.6:
        return 0.0
    return round((match - 0.6) / 0.4 * 10.0, 4)


def wer_to_score(wer: float) -> float:
    """Plan §13: WER 0 -> 10; 0.10 -> 5; >= 0.20 -> 0; linear."""
    if wer <= 0:
        return 10.0
    if wer >= 0.20:
        return 0.0
    return round(10.0 * (1 - wer / 0.20), 4)


def preservation_score(measures: dict) -> float | None:
    """pHash: distance 0 -> 10, at the declared bound -> 5, at 2x -> 0.
    SSIM (outside declared region): 1.0 -> 10, at the declared min -> 5,
    linearly to 0 below it. A gate failure is already an invalid cell; this
    only refines ranking among passing outputs."""
    if "preservation_phash_distance" in measures and "preservation_phash_max" in measures:
        dist = float(measures["preservation_phash_distance"])
        bound = float(measures["preservation_phash_max"]) or 1.0
        return round(max(0.0, 10.0 - 5.0 * dist / bound), 4)
    if "preservation_ssim_outside" in measures and "preservation_ssim_min" in measures:
        ssim = float(measures["preservation_ssim_outside"])
        lo = float(measures["preservation_ssim_min"])
        if ssim >= lo:
            return round(5.0 + 5.0 * (ssim - lo) / max(1e-9, 1.0 - lo), 4)
        return round(max(0.0, 5.0 * (ssim / lo) ** 4), 4)
    return None


def technical_compliance_score(measures: dict) -> float | None:
    """Video spec delivery, 0-10. The hard validity floor lives in the gates
    (checks.min_*); this criterion grades how much of the DECLARED spec a
    valid clip actually delivered.

      duration: inside [min_duration_s, max_duration_s] -> full; outside the
                band the score falls linearly, hitting 0 at 50% violation
      dims:     delivered/requested resolution ratio, capped at 1.0 — a 720p
                clip against a 1080p brief scores 1280/1920 = 0.667

    Overall = 10 x min(components). None (no measures) = unmeasured — the
    weight is redistributed by the existing mechanism and recorded."""
    parts = []
    dur = measures.get("duration_s")
    lo, hi = measures.get("min_duration_s"), measures.get("max_duration_s")
    if dur is not None and (lo is not None or hi is not None):
        violation = 0.0
        if lo is not None and dur < lo:
            violation = (lo - dur) / lo
        elif hi is not None and dur > hi:
            violation = (dur - hi) / hi
        parts.append(max(0.0, 1.0 - 2.0 * violation))
    w, h = measures.get("width"), measures.get("height")
    tw, th = measures.get("target_width"), measures.get("target_height")
    if w and h and tw and th:
        parts.append(min(1.0, w / tw, h / th))
    if not parts:
        return None
    return round(10.0 * min(parts), 4)


def measured_criterion_score(name: str, measures: dict) -> float | None:
    """None = the measurement is missing (unmeasured — weight redistributed)."""
    if name == "text_accuracy" and "ocr_match" in measures:
        return text_accuracy_from_ocr(float(measures["ocr_match"]))
    if name == "text_accuracy" and "wer" in measures:
        return wer_to_score(float(measures["wer"]))
    if name == "preservation":
        return preservation_score(measures)
    if name == "technical_compliance":
        return technical_compliance_score(measures)
    return None


# --------------------------------------------------------------------------
# scores.jsonl — one row per scenario x model, final numbers
# --------------------------------------------------------------------------

def score_run(project_root: Path, run_dir: Path) -> dict:
    project_root, run_dir = Path(project_root), Path(run_dir)
    manifest = Manifest(run_dir)
    files = RunFiles(run_dir)
    run_id = manifest.data["run_id"]
    modality = manifest.data["modality"]

    scenarios = {}
    for f in sorted((run_dir / "scenarios").glob("*.yaml")):
        data = yaml.safe_load(f.read_text())
        data.pop("source_path", None)
        s = Scenario(**data, source_path=str(f))
        scenarios[s.id] = s

    rubrics = {}
    for task, expected_hash in manifest.data.get("rubric_hashes", {}).items():
        rubrics[task] = load_rubric(project_root / "configs" / "rubrics", modality, task)

    checks_by_cell = {(r["scenario_id"], r["model_id"]): r for r in files.read("checks")}
    judged_by_cell = {}
    for r in files.read("judge"):
        if r.get("status") == "judged":
            judged_by_cell[(r["scenario_id"], r["model_id"])] = r

    existing = {(r["scenario_id"], r["model_id"]) for r in files.read("scores")}
    counts = {"scored": 0, "invalid": 0, "unjudged": 0, "other": 0}

    for key, cell in sorted(manifest.data["cells"].items()):
        sid, mid = cell["scenario_id"], cell["model_id"]
        if cell["state"] == "skipped" or (sid, mid) in existing:
            continue
        s = scenarios.get(sid)
        if s is None:
            continue
        rubric = rubrics[s.task]
        crits = effective_criteria(rubric, s)
        row = {"ts": utcnow(), "run_id": run_id, "scenario_id": sid, "model_id": mid,
               "task": s.task, "rubric_hash": rubric.rubric_hash,
               "weights": {c.name: round(c.weight, 6) for c in crits}}

        if cell["state"] == "invalid":
            # the one earned zero: the model produced something unusable
            row.update({"status": "invalid", "score": 0.0,
                        "reason": cell.get("reason", "")})
            counts["invalid"] += 1
        elif cell["state"] in ("judged", "scored"):
            jrow = judged_by_cell.get((sid, mid))
            if jrow is None:
                row.update({"status": "unjudged", "score": None,
                            "reason": "no valid judge record"})
                counts["unjudged"] += 1
            else:
                measures = checks_by_cell.get((sid, mid), {}).get("measures", {})
                crit_scores, unmeasured = {}, []
                for c in crits:
                    if c.judged_by == "measured":
                        v = measured_criterion_score(c.name, measures)
                        if v is None:
                            unmeasured.append(c.name)
                        else:
                            crit_scores[c.name] = {"score": v, "source": "measured"}
                    else:
                        j = jrow["criteria"].get(c.name)
                        crit_scores[c.name] = {"score": j["score"], "source": "judge",
                                               "reasoning": j["reasoning"]}
                weights = {c.name: c.weight for c in crits if c.name not in unmeasured}
                if unmeasured:  # redistribute, and say so (plan §18, ASR-failure rule)
                    total = sum(weights.values())
                    weights = {k: v / total for k, v in weights.items()}
                    row["unmeasured"] = unmeasured
                    row["weights"] = {k: round(v, 6) for k, v in weights.items()}
                score = sum(weights[n] * crit_scores[n]["score"] for n in weights)
                row.update({"status": "scored", "score": round(score, 4),
                            "criteria": crit_scores})
                counts["scored"] += 1
                manifest.set_cell_state(key, "scored")
        elif cell["state"] == "unjudged":
            row.update({"status": "unjudged", "score": None,
                        "reason": cell.get("reason", "")})
            counts["unjudged"] += 1
        else:  # failed / still planned — no number, ever
            row.update({"status": cell["state"], "score": None,
                        "reason": cell.get("reason", "")})
            counts["other"] += 1
        files.scores.append(row)

    if counts["scored"] or counts["invalid"]:
        manifest.set_run_state("scored")
    from .summary import refresh_scenario_status
    refresh_scenario_status(manifest)
    return counts


# --------------------------------------------------------------------------
# Aggregation — the numbers the report prints (pure; reads JSONL only)
# --------------------------------------------------------------------------

def sign_test_p(wins: int, decided: int) -> float:
    """One-sided binomial tail: P(X >= max(wins, decided-wins) | p=0.5)."""
    k = max(wins, decided - wins)
    return sum(math.comb(decided, i) for i in range(k, decided + 1)) / 2 ** decided


def _p50(values: list) -> float | None:
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    mid = n // 2
    return float(v[mid]) if n % 2 else (v[mid - 1] + v[mid]) / 2.0


def aggregate(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = Manifest(run_dir)
    files = RunFiles(run_dir)
    cells = manifest.data.get("cells", {})
    telemetry = files.read("telemetry")
    scores = files.read("scores")
    judge_rows = files.read("judge")

    # latest score row per cell wins (re-scoring appends; nothing is edited)
    score_by_cell: dict = {}
    for r in scores:
        score_by_cell[(r["scenario_id"], r["model_id"])] = r

    tasks: dict[str, dict] = {}
    for key, cell in cells.items():
        task = cell["task"]
        t = tasks.setdefault(task, {"models": {}, "scenarios": set(), "pairs": []})
        t["scenarios"].add(cell["scenario_id"])
        m = t["models"].setdefault(cell["model_id"], {
            "eligible": 0, "skipped": 0, "numeric": [], "scored": 0, "invalid": 0,
            "unjudged": 0, "failed": 0, "latencies": [], "attempts": [],
            "gen_micro": 0, "judge_micro": 0, "cost_estimated": False,
            "refused": 0, "by_scenario": {}})
        if cell["state"] == "skipped":
            m["skipped"] += 1
            continue
        m["eligible"] += 1
        srow = score_by_cell.get((cell["scenario_id"], cell["model_id"]))
        if srow and srow["status"] in ("scored", "invalid"):
            m["numeric"].append(srow["score"])
            m["by_scenario"][cell["scenario_id"]] = srow["score"]
            m[srow["status"]] += 1
        elif srow and srow["status"] == "unjudged" or cell["state"] == "unjudged":
            m["unjudged"] += 1
        if cell["state"] == "failed":
            m["failed"] += 1
            if "refused" in (cell.get("reason") or ""):
                m["refused"] += 1

    for row in telemetry:
        task = row.get("task")
        mid = row.get("model_id")
        if task not in tasks or mid not in tasks[task]["models"]:
            continue
        m = tasks[task]["models"][mid]
        if row.get("status") == "ok":
            m["latencies"].append(row.get("latency_ms"))
            m["attempts"].append(row.get("attempt", 1))
            cost = row.get("cost", {})
            m["gen_micro"] += cost.get("micro_usd", 0)
            if cost.get("usage_source") == "estimated":
                m["cost_estimated"] = True

    for row in judge_rows:
        task = row.get("task")
        mid = row.get("model_id")
        if task in tasks and mid in tasks[task]["models"] and row.get("cost"):
            tasks[task]["models"][mid]["judge_micro"] += row["cost"].get("micro_usd", 0)

    # per-model summary + pairwise verdicts
    for task, t in tasks.items():
        for mid, m in t["models"].items():
            nums = m["numeric"]
            m["mean"] = round(sum(nums) / len(nums), 2) if nums else None
            m["worst"] = round(min(nums), 2) if nums else None
            m["below_5"] = sum(1 for v in nums if v < 5)
            m["judged_n"] = len(nums)
            m["coverage"] = len(nums) / m["eligible"] if m["eligible"] else 0.0
            m["latency_p50_ms"] = _p50(m["latencies"])
            m["latency_max_ms"] = max(m["latencies"]) if m["latencies"] else None
            m["success_rate"] = ((m["eligible"] - m["failed"]) / m["eligible"]
                                 if m["eligible"] else None)
            m["mean_attempts"] = (round(sum(m["attempts"]) / len(m["attempts"]), 2)
                                  if m["attempts"] else None)
            m["gen_cost_per_scenario_usd"] = (m["gen_micro"] / 1e6 / m["eligible"]
                                              if m["eligible"] else 0.0)
            m["judge_cost_per_scenario_usd"] = (m["judge_micro"] / 1e6 / m["eligible"]
                                                if m["eligible"] else 0.0)

        mids = sorted(t["models"])
        for i, a in enumerate(mids):
            for b in mids[i + 1:]:
                t["pairs"].append(pairwise_verdict(task, a, b, t["models"]))
        t["scenarios"] = sorted(t["scenarios"])
    return {"tasks": tasks}


def pairwise_verdict(task: str, a: str, b: str, models: dict) -> dict:
    ma, mb = models[a], models[b]
    common = sorted(set(ma["by_scenario"]) & set(mb["by_scenario"]))
    wins_a = wins_b = ties = 0
    for sid in common:
        d = ma["by_scenario"][sid] - mb["by_scenario"][sid]
        if abs(d) <= TIE_BAND:
            ties += 1
        elif d > 0:
            wins_a += 1
        else:
            wins_b += 1
    decided = wins_a + wins_b
    mean_gap = (ma["mean"] - mb["mean"]) if (ma["mean"] is not None
                                             and mb["mean"] is not None) else None

    result = {"task": task, "a": a, "b": b, "n_common": len(common),
              "wins_a": wins_a, "wins_b": wins_b, "ties": ties,
              "decided": decided, "mean_a": ma["mean"], "mean_b": mb["mean"],
              "mean_gap": round(mean_gap, 3) if mean_gap is not None else None,
              "sign_test_p": (round(sign_test_p(max(wins_a, wins_b), decided), 5)
                              if decided >= SIGN_TEST_MIN_N else None),
              "winner": None, "door": None, "note": ""}

    if mean_gap is None or not common:
        result["note"] = "not comparable: missing scores"
        return result

    def door_for(cand: str, opp: str, wins: int) -> str | None:
        gap = models[cand]["mean"] - models[opp]["mean"]
        if gap >= MEAN_GAP_DOOR:
            return f"mean gap {gap:.2f} >= {MEAN_GAP_DOOR}"
        if decided > 0 and wins / decided >= WIN_RATE_DOOR:
            return (f"{wins}/{decided} decided scenarios "
                    f"({wins / decided:.0%} >= {WIN_RATE_DOOR:.0%})")
        return None

    door_a, door_b = door_for(a, b, wins_a), door_for(b, a, wins_b)
    if door_a and door_b:
        result["note"] = (f"the two lenses contradict ({a}: {door_a}; {b}: {door_b}) "
                          f"— no winner declared; inspect the per-scenario evidence")
    else:
        for cand, door in ((a, door_a), (b, door_b)):
            if not door:
                continue
            if models[cand]["coverage"] < COVERAGE_FLOOR:
                result["note"] = (f"{cand} clears a door ({door}) but coverage "
                                  f"{models[cand]['coverage']:.0%} < "
                                  f"{COVERAGE_FLOOR:.0%} — no winner declared")
                continue
            result["winner"], result["door"] = cand, door
            return result

    # declared tie — break only with facts, in this order (plan §11)
    facts = []
    if ma["invalid"] != mb["invalid"]:
        fewer = a if ma["invalid"] < mb["invalid"] else b
        facts.append(f"fewer check failures: {fewer}")
    if (ma["success_rate"] is not None and mb["success_rate"] is not None
            and ma["success_rate"] != mb["success_rate"]):
        better = a if ma["success_rate"] > mb["success_rate"] else b
        facts.append(f"higher success rate: {better}")
    ca = ma["gen_cost_per_scenario_usd"] + ma["judge_cost_per_scenario_usd"]
    cb = mb["gen_cost_per_scenario_usd"] + mb["judge_cost_per_scenario_usd"]
    if abs(ca - cb) > 1e-9:
        facts.append(f"cheaper: {a if ca < cb else b}")
    if ma["latency_p50_ms"] and mb["latency_p50_ms"] \
            and ma["latency_p50_ms"] != mb["latency_p50_ms"]:
        facts.append(f"faster p50: {a if ma['latency_p50_ms'] < mb['latency_p50_ms'] else b}")
    tie_note = ("tie on quality (mean gap "
                + (f"{abs(mean_gap):.2f}" if mean_gap is not None else "n/a")
                + f" < {MEAN_GAP_DOOR}, no {WIN_RATE_DOOR:.0%} win rate). "
                + ("Broken only by facts: " + "; ".join(facts) if facts
                   else "Nothing separates them on these scenarios — "
                        "they are equivalent here, which is itself a result."))
    result["note"] = (result["note"] + " | " + tie_note) if result["note"] else tie_note
    return result
