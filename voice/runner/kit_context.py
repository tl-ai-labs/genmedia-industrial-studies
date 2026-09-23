"""
The voice lane's reports in the shared kit's shape (shared/report_kit).

WHAT THIS FILE IS. Every lane - image, video, voice - presents its results
through one report kit, so the page chrome, section order, tables, styling,
number formats and file names are decided once for all three. This module is
the voice half of that seam: it takes the numbers `runner.dashboard` already
computed and arranges them into the kit's report context. It decides no
verdict and computes no quality number of its own - the same rule the client
report has always followed, now for every voice surface.

Voice-only content (audio players, the median take, transcripts, set verdicts,
time to first audio, human review, where each model was served from) is
rendered by runner/templates/lane_hooks.j2, at the fixed places the kit gives
every lane.

TWO CONTEXTS, ONE SHAPE.
  study_context()  every run under runs/, merged into one comparison - the
                   internal board (runs/index.html) and the client report
                   (runs/client-report/) are its two audiences.
  run_context()    one run - its report.html and report-client.html.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Callable

from . import _report_kit as kit
from .dashboard import (INDUSTRY, WIN_GAP, _duel, _industry, _overall, _parent_id,
                        _scenario_blocks, _streaming_panel, load_runs_reviewed,
                        rollup_models, served_rows)
from .client_report import (_titles, is_gemini, pct, short_verdict,
                            vendor_of)
from .review import review_context

LANE = kit.LaneProfile(key="voice", unit="clip", media="audio",
                       templates=Path(__file__).resolve().parent / "templates")

def render(ctx: dict[str, Any], client: bool) -> str:
    """One audience of a voice report, through the kit's run page. Returned,
    not written, so the caller can refuse an undeliverable size first
    (kit.write_page with a guard)."""
    return kit.render_page(LANE, "kit/run.html.j2", ctx, client=client)


def _win_rule(band: float, study: bool) -> dict[str, str]:
    """Voice decides a scenario on a decision band, not on any margin — stated
    in each audience's units where the kit states the scenario rule."""
    extra = (" and it cleared no fewer gates; better on quality but worse on delivery is a split"
             if study else "")
    return {
        "client": f"A scenario is won only when the quality gap clears the {band * 10:.1f} pp decision "
                  f"band{extra}. A closer gap is recorded as a tie",
        "internal": f"A scenario is won only when the quality gap clears the {band} decision band on the "
                    f"10-point scale{extra}. A closer gap is recorded as a tie",
    }


# Voice files a scenario under an industry by its id prefix (dashboard.INDUSTRY).
_INDUSTRY_SOURCE = ("Industries come from each scenario id's prefix "
                    "(vr-ecom, vr-drama, vr-game, vr-ads), one industry per scenario.")


# --------------------------------------------------------------------------
# The study: every run, one comparison
# --------------------------------------------------------------------------

def study_context(runs_root: Path, modality: str = "voice",
                  review_path: Path | None = None,
                  client_audio: Callable[[dict, str, str], str | None] | None = None,
                  titles: dict[str, str] | None = None) -> dict[str, Any]:
    """
    `client_audio(clip, scenario_id, model_id)` returns the src the CLIENT
    page plays a clip from (an mp3 beside the page, or a data URI). Without it
    the client copy has no players - the internal board never needs one.
    """
    runs, review, applied = load_runs_reviewed(runs_root, modality, review_path)
    if not runs:
        raise SystemExit(f"no {modality} runs under {runs_root}")
    if titles is None:
        titles = _titles(runs)                      # the run's own frozen titles
    models = rollup_models(runs)
    by_id = {m.model_id: m for m in models}
    duel_v = _duel(models)
    blocks = _scenario_blocks(runs, duel_v, {m.model_id: m.accent for m in models}, review)
    all_cells = [c for r in runs for c in r.cells]
    served = served_rows(runs)
    human = review_context(review, applied)
    streaming = _streaming_panel(all_cells, models)
    overall = _overall(models)
    uncalibrated = any(not r.calibration_passed for r in runs)

    vendors = {m.model_id: vendor_of(m.model_id) for m in models}
    order = kit.gemini_first([m.model_id for m in models], vendors)
    served["models"].sort(key=lambda r: order.index(r["model_id"])
                          if r["model_id"] in order else len(order))
    task = statistics.mode([c.task for c in all_cells if c.task and c.task != "—"] or ["text_to_speech"])

    # ---- the model table, in the kit's per-model fields ---------------------
    ttfa_p50 = {r["model_id"]: r["p50"] for r in (streaming.get("per_model") or [])}
    mdicts: dict[str, dict[str, Any]] = {}
    for mid in order:
        m = by_id[mid]
        cells = [c for c in all_cells if c.model_id == mid]
        judge = [c.judge_micro for c in cells if c.judge_micro]
        mdicts[mid] = {
            "mean": m.mean_score,
            "worst": min(m.scores) if m.scores else None,
            "judged_n": m.scored_n, "eligible": m.evaluated_n or m.n,
            "below_5": sum(1 for s in m.scores if s < 5),
            "gen_cost_per_scenario_usd": (m.mean_cost / 1e6) if m.costs else None,
            "judge_cost_per_scenario_usd": (statistics.mean(judge) / 1e6) if judge else None,
            "latency_min_ms": min(m.latencies) if m.latencies else None,
            "latency_p50_ms": m.p50_latency, "latency_max_ms": max(m.latencies) if m.latencies else None,
            "success_rate": m.success_rate, "mean_attempts": m.mean_attempts,
            # voice measures
            "gate_pass_rate": m.gate_pass_rate, "repeat_spread": m.repeat_spread,
            "worst_wer": m.worst_wer, "cost_per_audio_minute": m.cost_per_audio_minute,
            "latency_p95_ms": m.p95_latency, "ttfa_p50_ms": ttfa_p50.get(mid),
            "invalid": m.invalid, "unjudged": m.unjudged,
        }

    # ---- scenarios -----------------------------------------------------------
    a_id = order[0] if order else None
    b_id = order[1] if len(order) > 1 else None
    evidence = []
    for b in blocks:
        side = {c["model_id"]: c for c in b["side"]}
        cards = []
        for mid in order:
            col = side.get(mid)
            if col is None:
                continue
            clips = []
            for cl in col["clips"]:
                clips.append(dict(cl, audio_internal=cl.get("audio_rel"),
                                  audio_client=(client_audio(cl, b["id"], mid)
                                                if client_audio else None)))
            names = sorted({g.get("gate") for cl in col["clips"] for g in (cl.get("gates") or [])})
            lead = next((cl for cl in clips if cl.get("lead")), clips[0] if clips else {})
            cards.append({
                "model_id": mid, "score": col["mean"],
                "state": "scored" if col["mean"] is not None else "gated",
                "status": "scored" if col["mean"] is not None else "gated",
                "gates": [{"gate": g, "passed": g not in col["failed"]} for g in names],
                "latency_ms": lead.get("latency_ms"),
                "gen_cost_micro": None,
                "clips": clips, "n_scored": col["n_scored"], "n_cells": col["n_cells"],
                "worst_wer": col["worst_wer"], "n_corrected": col["n_corrected"],
                "is_winner": col["is_winner"],
                # hooks see the card, not the page context
                "study": True, "uncalibrated": uncalibrated,
            })
        scores = {c["model_id"]: c["score"] for c in cards}
        g, o = scores.get(a_id), scores.get(b_id)
        scripts = b["scripts"]
        if len(scripts) > 1:
            prompt = f"{len(scripts)} variants\n" + "\n".join(
                f"{s['label']}{' (' + s['title'] + ')' if s['title'] else ''}: {s['text']}"
                for s in scripts)
        else:
            prompt = scripts[0]["text"] if scripts else "No frozen script recorded for this scenario."
        # Oriented to the page's column order, never re-decided: the gap and
        # the winner are the board's own (dashboard._scenario_blocks).
        gap = None if b["gap"] is None or not duel_v else (
            b["gap"] if duel_v["a"] == a_id else -b["gap"])
        evidence.append({
            "id": b["id"], "title": (titles or {}).get(b["id"], ""),
            "prompt": prompt, "expected": "", "task": task,
            "family": "", "industry": b["industry"], "industry_also": [],
            "winner": b["winner"],
            # compared, but crowned nobody under the gates rule: its own result
            "result_key": "split" if b["verdict"] == "Split" else None,
            "result_label": "split" if b["verdict"] == "Split" else None,
            "margin": None if b["gap"] is None else round(abs(b["gap"]), 3),
            "g_score": g, "c_score": o,
            "gap": None if g is None or o is None else round(g - o, 3),
            "sources": [], "cards": cards,
            "voice": {
                "verdict": b["verdict"], "detail": b["detail"], "n_passes": b["n_passes"],
                "labels": b["labels"], "rows": sorted(b["rows"], key=lambda r: order.index(r["model_id"])
                                                      if r["model_id"] in order else 99),
                "gaps": b["gaps"], "gap": gap, "floor": b["floor"], "stale": b["stale"],
                "group": b["group"], "ttfa": b["ttfa"], "throughput": b["throughput"],
                "human": b["human"], "n_corrected": b["n_corrected"],
                "short": _short_verdict(b, gap, order),
                "gap_label": f"{duel_v['a']} − {duel_v['b']}" if duel_v else "",
            },
        })

    # ---- one pair, the study's own overall verdict ---------------------------
    compared = [e for e in evidence if e["margin"] is not None]
    wins_a = sum(1 for e in compared if e["winner"] == a_id)
    wins_b = sum(1 for e in compared if e["winner"] == b_id)
    pairs = []
    if a_id and b_id:
        pairs.append({
            "a": a_id, "b": b_id, "winner": overall["winner"],
            "door": overall["detail"], "note": f"{overall['verdict']}. {overall['detail']}",
            "mean_a": mdicts[a_id]["mean"], "mean_b": mdicts[b_id]["mean"],
            "wins_a": wins_a, "wins_b": wins_b, "ties": len(compared) - wins_a - wins_b,
            "n_common": len(compared), "decided": wins_a + wins_b, "sign_test_p": None,
        })
    tasks = {task: {"models": mdicts, "pairs": pairs}}
    kit.wtl_rollup(tasks[task])
    tasks[task]["metric_rows"] = kit.metric_rows(mdicts, order, LANE.unit, extra=VOICE_ROWS)
    duel = kit.build_duel(tasks, order, LANE.unit, extra=_duel_extra(mdicts, order))
    _respect_the_band(duel, mdicts)

    industries = kit.rollup(evidence, "industry")
    n_clips = sum(len(c["clips"]) for e in evidence for c in e["cards"])
    judge_model = runs[-1].judge_model
    n_excluded = len(all_cells) - n_clips
    return {
        "manifest": {
            # The top bar reads the run id; for a study it names the study.
            "run_id": f"{len(evidence)} scenarios · {n_clips} clips · {len(runs)} runs",
            "created": runs[-1].started_at, "state": "reported",
            "git_sha": runs[-1].git_sha if runs[-1].git_sha != "—" else None,
        },
        "names": {}, "vendors": vendors, "model_order": order, "family_models": order,
        "agg": {"tasks": tasks}, "duel": duel,
        "tally": kit.tally(evidence, order),
        "families": {}, "industries": industries, "hidden_industries": [],
        "evidence": evidence,
        "totals": {"gen_micro": sum(c.cost_micro for c in all_cells),
                   "judge_micro": sum(c.judge_micro for c in all_cells)},
        "completion": {"completed": len(compared), "total": len(evidence)},
        "estimates": [],
        "judge_meta": {"provider_model": judge_model, "temperature": 0},
        "vendor_lines": _vendor_lines(order, served),
        "win_rule_note": _win_rule(WIN_GAP, study=True),
        "result_kinds": {"split": "Splits"},
        "runs_note": f"{len(runs)} runs merged, repeated scenarios averaged",
        "generation_note": "Every clip was generated by the runs listed in the footnotes",
        "industry_source_note": _INDUSTRY_SOURCE,
        "voice": {
            "mode": "study", "overall": overall, "served": served, "human": human,
            "streaming": streaming, "uncalibrated": uncalibrated,
            "judge": judge_model, "asr": runs[-1].asr_model,
            "judge_is_google": judge_model.lower().startswith("gemini"),
            "win_gap": WIN_GAP, "band_pp": WIN_GAP * 10,
            "asr_micro": sum(c.asr_micro for c in all_cells),
            "n_clips": n_clips, "n_runs": len(runs), "n_excluded": n_excluded,
            "stale_ids": sorted({e["id"] for e in evidence if e["voice"]["stale"]}),
            "run_rows": _run_rows(runs),
            "footnotes": _study_footnotes(runs, models, served, human, n_excluded,
                                          [e for e in evidence if e["voice"]["stale"]],
                                          uncalibrated),
            "per_scenario_human": _human_rows(evidence),
            "mos_signal": any(not c.audio_q_is_mos for c in all_cells if c.audio_q is not None),
        },
    }


# Voice measures beside the common rows. Labels are the kit's style; every
# row the old client report carried stays client-facing.
VOICE_ROWS = [
    {"key": "gates", "label": "Gates passed", "better": "higher", "unit": "ratio",
     "get": lambda m: m["gate_pass_rate"]},
    {"key": "spread", "label": "Run-to-run spread", "better": "lower", "unit": "score",
     "get": lambda m: m["repeat_spread"]},
    {"key": "worst_wer", "label": "Worst word error rate", "better": "lower", "unit": "ratio",
     "get": lambda m: m["worst_wer"], "decimals": 1},
    {"key": "cost_min", "label": "Cost per audio minute", "better": "lower", "unit": "usd_micro",
     "get": lambda m: m["cost_per_audio_minute"]},
    {"key": "lat_p95", "label": "Latency p95", "better": "lower", "unit": "ms",
     "get": lambda m: m["latency_p95_ms"]},
    {"key": "ttfa_p50", "label": "Time to first audio p50", "better": "lower", "unit": "ms",
     "get": lambda m: m["ttfa_p50_ms"]},
]


def _duel_extra(mdicts: dict, order: list) -> list[dict]:
    """Voice rows on the strip - only where both sides have a value, because
    the strip draws a missing value as zero and 'no repeats yet' is not a
    perfect spread."""
    if len(order) < 2:
        return []
    a, b = mdicts[order[0]], mdicts[order[1]]
    out = [{"key": "gates", "label": "Gates passed", "get": lambda m: m["gate_pass_rate"],
            "fmt": "{:.0%}", "better": "higher"}]
    if a.get("repeat_spread") is not None and b.get("repeat_spread") is not None:
        out.append({"key": "spread", "label": "Run-to-run spread",
                    "get": lambda m: m["repeat_spread"], "fmt": "±{:.3f}", "better": "lower"})
    if a.get("worst_wer") is not None and b.get("worst_wer") is not None:
        out.append({"key": "worst_wer", "label": "Worst word error rate",
                    "get": lambda m: m["worst_wer"], "fmt": "{:.1%}", "better": "lower"})
    return out


def _respect_the_band(duel: dict | None, mdicts: dict) -> None:
    """Quality inside the decision band names no better side on the strip -
    everywhere else on the page refuses a winner there, and the strip is the
    easiest place to over-claim."""
    if not duel:
        return
    av, bv = mdicts[duel["a"]]["mean"], mdicts[duel["b"]]["mean"]
    for m in duel["metrics"]:
        if m["key"] == "mean" and av is not None and bv is not None and abs(av - bv) <= WIN_GAP:
            m["win"] = None


def _short_verdict(b: dict, gap: float | None, order: list) -> str:
    """The client's one line per scenario - client_report.short_verdict, fed
    the gap oriented to this page's columns."""
    inside = gap is not None and b["floor"] is not None and abs(gap) <= b["floor"]
    oth = next((m for m in order if not is_gemini(m)), "")
    return short_verdict(
        {"gap_pct": pct(gap), "floor_pct": pct(b["floor"]), "winner": b["winner"],
         "winner_is_gemini": bool(b["winner"] and is_gemini(b["winner"])),
         "inside_noise": inside, "verdict": b["verdict"], "passes": b["n_passes"]},
        "Gemini", oth.split("-")[0].title() or "the other model", WIN_GAP * 10)


def _vendor_lines(order: list, served: dict) -> list[str]:
    rows = {r["model_id"]: r for r in served.get("models", [])}
    return [f"{mid} ({vendor_of(mid) or 'vendor not recorded'}; "
            f"{rows[mid]['served_from'] if mid in rows else 'served from not recorded'})"
            for mid in order]


def _run_rows(runs) -> list[dict[str, Any]]:
    return [{
        "label": r.label, "started": (r.started_at or "")[:16].replace("T", " "),
        "scenario": ", ".join(sorted({_parent_id(c.scenario_id) for c in r.cells})) or "—",
        "cells": len(r.cells),
        "passed": sum(1 for c in r.cells if c.status == "scored"),
        "cost": sum(c.total_micro for c in r.cells),
        "judge": r.judge_model, "predictor": r.mos_predictor,
        "served": "; ".join(f"{m}: {v['served_from']}" for m, v in sorted(r.served.items())) or "—",
    } for r in reversed(runs)]


def _human_rows(evidence: list) -> list[dict[str, Any]]:
    """Automated verdict beside the human preference, per reviewed scenario -
    agreement is something the reader sees, never something computed."""
    out = []
    for e in evidence:
        obs = e["voice"]["human"]
        if not obs:
            continue
        out.append({
            "id": e["id"], "title": e["title"],
            "automated": f"{e['winner']} wins" if e["winner"] else e["voice"]["verdict"],
            "preferences": [o["preference"] for o in obs if o.get("preference")],
            "n_notes": len(obs), "n_corrected": e["voice"]["n_corrected"],
        })
    return out


def _study_footnotes(runs, models, served, human, n_excluded, stale, uncalibrated) -> list[str]:
    """The internal board's footnotes, as trusted HTML strings. Unchanged in
    substance from the pre-kit board; they carry every qualifier."""
    from html import escape as _e

    notes = [
        "<b>Quality is meaned over scored cells only</b>, and always carries its denominator. "
        "A gated cell is counted in the gate rate, never averaged into quality - a clean read "
        "that is too long for an ad slot is the wrong length, not bad audio.",
        "<b>A repeat is the same script and the same gates.</b> Runs of an edited scenario are "
        "excluded from every spread on this page.",
        "<b>The objective audio number is a signal metric, not a MOS.</b> It measures SNR, "
        "spectral flatness, clipping and bandwidth - it must not be quoted as a mean opinion score.",
        "<b>Cost is what the run believed it paid</b>, at the rates frozen in its own manifest; "
        "cost per clip includes transcription and judging.",
    ]
    if n_excluded:
        ids = ", ".join(f"<code>{_e(e['id'])}</code>" for e in stale)
        notes.append(
            f"<b>{n_excluded} clips are not on this page.</b> They were produced by earlier runs "
            f"of {ids} against a <em>different version</em> of the scenario - the script or the "
            f"gates changed afterwards - so they are excluded from every card and every spread. "
            f"They remain in their own run folders, unaltered.")
    arms = {m.model_id.split("-")[0] for m in models}
    judge_vendor = (runs[-1].judge_model or "").split("-")[0]
    notes.append(
        f"<b>Judged by <code>{_e(runs[-1].judge_model)}</code>, listening to the audio</b>, blinded "
        f"A/B/C per scenario. "
        + ("It shares a vendor with one arm under test - blinding hides the label, not the "
           "acoustic fingerprint, so judge-derived criteria carry that exposure."
           if judge_vendor in arms else "It shares a vendor with neither arm."))
    notes.append(
        f"<b>Transcribed by <code>{_e(runs[-1].asr_model)}</code></b>, run locally. The transcript "
        f"is the basis of every WER number and every phrase gate.")
    if uncalibrated:
        notes.insert(0, "<b>The judge is uncalibrated.</b> The 2-humans x 5-clips gate has never "
                        "been run, so <code>naturalness</code> and <code>clarity</code> carry no "
                        "evidence of agreement with a human ear.")
    if human["present"]:
        notes.append(
            f"<b>Human review is kept apart from the automated results.</b> "
            f"{human['n_corrections']} automated result{'s' if human['n_corrections'] != 1 else ''} "
            f"the reviewers found wrong {'are' if human['n_corrections'] != 1 else 'is'} corrected, "
            f"each marked <em>corrected</em> with what the instrument originally said. "
            f"<code>--no-review</code> renders the instrument's answer untouched.")
    return notes


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------

def run_context(*, manifest: dict, cells, tables: list, evidence_rows: list, calibration,
                totals: dict, rubrics: dict, judge_model: str, voice_map_rows: list,
                unsupported_rows: list, skipped: list, tie_band: float,
                paired_wtl, stats: dict, model_ids: list) -> dict[str, Any]:
    """The per-run report in the kit's shape. Everything here was computed by
    runner.report.render(); this only arranges it."""
    vendors = {mid: vendor_of(mid) for mid in model_ids}
    order = kit.gemini_first(model_ids, vendors)
    tasks: dict[str, Any] = {}
    for t in tables:
        rows = {r["model"]: r for r in t["rows"]}
        mdicts = {}
        for mid in order:
            r = rows.get(mid)
            if r is None:
                continue
            n = max(1, r["attempted"])
            mdicts[mid] = {
                "mean": r["mean"], "worst": r["worst"], "judged_n": r["judged"],
                "eligible": r["attempted"], "below_5": r["below_five"],
                "gen_cost_per_scenario_usd": r["cost_per_scenario"] / 1e6,
                "judge_cost_per_scenario_usd": r["cost_judge"] / n / 1e6,
                "latency_min_ms": r["min"], "latency_p50_ms": r["p50"], "latency_max_ms": r["max"],
                "success_rate": r["success"], "mean_attempts": r["attempts_mean"],
                "cost_exact": r["cost_exact"], "unjudged": r["unjudged"],
            }
        pairs = []
        mo = [m for m in order if m in mdicts]
        if len(mo) >= 2:
            p = paired_wtl(cells, mo[0], mo[1], t["task"])
            v = t["verdict"]
            pairs.append({"a": mo[0], "b": mo[1], "winner": v.winner, "door": v.reason,
                          "note": v.reason, "mean_a": mdicts[mo[0]]["mean"],
                          "mean_b": mdicts[mo[1]]["mean"], "wins_a": p.wins, "ties": p.ties,
                          "wins_b": p.losses, "n_common": p.compared, "decided": p.decided,
                          "sign_test_p": v.p_value})
        tasks[t["task"]] = {"models": mdicts, "pairs": pairs, "rubric": t["rubric"]}
        kit.wtl_rollup(tasks[t["task"]])
        tasks[t["task"]]["metric_rows"] = kit.metric_rows(mdicts, order, LANE.unit)

    def is_tie(diff: float) -> bool:
        return abs(diff) <= tie_band                        # scoring.paired_wtl's rule

    evidence = []
    for e in evidence_rows:
        sc = e["scenario"]
        cards = []
        for x in sorted(e["entries"], key=lambda x: order.index(x["model"]) if x["model"] in order else 99):
            cell = x["cell"]
            st = x["stats"]
            score = cell.score if cell is not None and cell.status in ("scored", "invalid") else None
            criteria = {}
            if cell is not None:
                for name, val in (cell.criterion_scores or {}).items():
                    reasoning = (x["judge_reasoning"] or {}).get(name, "")
                    criteria[name] = {"score": float(val), "reasoning": reasoning,
                                      "source": "judge" if reasoning else "measured"}
            clip = {"variant": "", "run_label": "", "lead": False, "score": score,
                    "status": cell.status if cell else st.status,
                    "wer": x["wer"], "duration_s": st.duration_s, "latency_ms": st.latency_ms,
                    "ttfa_ms": None, "transcript": x["transcript"], "gates": x["gates"],
                    "corrections": [], "review_note": "",
                    "audio_internal": x["audio"], "audio_client": x["audio"]}
            cards.append({
                "model_id": x["model"], "score": score,
                "state": cell.status if cell else ("failed" if st.status not in ("ok", "resumed") else "unjudged"),
                "status": cell.status if cell else st.status,
                "gates": x["gates"], "latency_ms": st.latency_ms,
                "gen_cost_micro": st.gen_cost or None, "cost_estimated": not st.cost_exact,
                "blind_label": x["blind_label"],
                "criteria": criteria or None,
                "weights": (cell.effective_weights if cell is not None else None),
                "unmeasured": (cell.unmeasured if cell is not None else None),
                "reason": x["judge_error"] or (cell.note if cell is not None and cell.status == "incomplete" else ""),
                "clips": [clip], "n_scored": 1 if score is not None else 0, "n_cells": 1,
                "worst_wer": x["wer"], "n_corrected": 0,
                "diff": x["diff"], "measurements": x["measurements"], "voice_pin": x["voice"],
                "study": False, "uncalibrated": not calibration.passed,
            })
        winner, margin = kit.scenario_result(cards, is_tie)
        scores = {c["model_id"]: c["score"] for c in cards}
        g = scores.get(order[0]) if order else None
        o = scores.get(order[1]) if len(order) > 1 else None
        evidence.append({
            "id": sc.id, "title": sc.title, "prompt": sc.text, "expected": sc.expected,
            "task": sc.task, "family": sc.tags[0] if sc.tags else "",
            "industry": _industry(sc.variant_of or sc.id), "industry_also": [],
            "winner": winner, "margin": margin, "g_score": g, "c_score": o,
            "gap": None if g is None or o is None else round(g - o, 3),
            "sources": [], "cards": cards,
            "voice": {"negative_control": e["reference_differs"]},
        })
    families = kit.rollup(evidence, "family")
    return {
        "manifest": dict(manifest, created=manifest.get("started_at", "")), "names": {}, "vendors": vendors, "model_order": order,
        "family_models": order, "agg": {"tasks": tasks},
        "duel": kit.build_duel(tasks, order, LANE.unit),
        "tally": kit.tally(evidence, order), "families": families,
        "industries": kit.rollup(evidence, "industry"), "hidden_industries": [],
        "evidence": evidence,
        "totals": {"gen_micro": totals["gen"], "judge_micro": totals["judge"]},
        "completion": {"completed": sum(1 for e in evidence if e["margin"] is not None),
                       "total": len(evidence)},
        "estimates": sorted({mid for t in tasks.values() for mid, m in t["models"].items()
                             if not m["cost_exact"]}),
        "judge_meta": {"provider_model": judge_model, "temperature": 0},
        "vendor_lines": [f"{mid} ({vendor_of(mid) or 'vendor not recorded'})" for mid in order],
        "params_unsupported": [f"{m}: {p}" for m, p in unsupported_rows],
        "win_rule_note": _win_rule(tie_band, study=False),
        "industry_source_note": _INDUSTRY_SOURCE,
        "voice": {
            "mode": "run", "calibration": calibration, "rubrics": rubrics,
            "totals": totals, "voice_map_rows": voice_map_rows, "skipped": skipped,
            "mos_fallback_reason": manifest.get("mos_fallback_reason"),
            "uncalibrated": not calibration.passed, "win_gap": tie_band, "band_pp": tie_band * 10,
            "judge": judge_model, "served": {"any": False}, "human": {"present": False},
            "streaming": {"any": False},
        },
    }


__all__ = ["LANE", "INDUSTRY", "render", "run_context", "study_context"]
