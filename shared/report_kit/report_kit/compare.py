"""Comparison helpers every lane's report uses — ordering, the metric table,
the head-to-head strip, the family / industry rollups, and scenario accounting.

These shape data for PRESENTATION only. Scoring and verdicts stay in each
lane's scoring code; the kit never decides who won a task.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

# ---------------------------------------------------------------- vendors


_VENDORS = (
    ("google", "Google"), ("vertex", "Google"), ("gemini", "Google"),
    ("openai", "OpenAI"), ("byteplus", "ByteDance"), ("bytedance", "ByteDance"),
    ("elevenlabs", "ElevenLabs"),
)


def vendor_of(provider: str) -> str:
    """Provider string ("google_vertex", "openai_images", …) -> vendor name."""
    p = (provider or "").lower()
    for needle, name in _VENDORS:
        if p.startswith(needle):
            return name
    return provider or "?"


def gemini_first(model_ids: Iterable[str], vendors: dict | None = None) -> list:
    """Presentation order, one rule for every table, tile, card and strip:
    the Google/Gemini arm first, the rest alphabetical."""
    vd = vendors or {}

    def rank(mid: str):
        google = vd.get(mid) == "Google" or "gemini" in mid.lower()
        return (0 if google else 1, mid)

    return sorted(model_ids, key=rank)


def tab_label(model_ids: list, names: dict) -> str:
    """Name a comparison, not a task: "Gemini 3 Pro vs GPT high". A trailing
    parenthesised tier is compressed so several tabs fit on one row."""
    def short(name: str) -> str:
        m = re.match(r"^(\S+).*\((.+)\)\s*$", name)
        return f"{m.group(1)} {m.group(2)}" if m else name
    return " vs ".join(short(names.get(m, m)) for m in model_ids)


def study_title(model_ids: list, names: dict) -> str:
    """"Gemini vs GPT": the first word of each arm's display name, in order."""
    words = []
    for mid in model_ids:
        w = names.get(mid, mid).split()[0]
        if w not in words:
            words.append(w)
    return " vs ".join(words)


# ---------------------------------------------------------- metric table

# Rows the client deliverable never carries: internal diagnostics only.
# Generation cost per output IS client-facing (study lead's call, 2026-09-04);
# judging cost is ours, not theirs.
INTERNAL_ROWS = {"judged", "below_5", "judge_cost", "success", "attempts"}


def wtl_rollup(task: dict) -> None:
    """Give each model in a task its W-T-L string from the task's pairs."""
    for mid, m in task["models"].items():
        w = l = ti = 0
        for p in task.get("pairs", []):
            if p["a"] == mid:
                w, l, ti = w + p["wins_a"], l + p["wins_b"], ti + p["ties"]
            elif p["b"] == mid:
                w, l, ti = w + p["wins_b"], l + p["wins_a"], ti + p["ties"]
        m["wtl"] = f"{w}-{ti}-{l}"


def _row(cols, models, key, label, better, unit, get, hi=False, internal_only=None,
         decimals=0):
    vals = []
    for mid in cols:
        try:
            vals.append(get(models[mid]))
        except (KeyError, TypeError, ZeroDivisionError):
            vals.append(None)
    r = {"key": key, "label": label, "unit": unit, "hi": hi, "better": better,
         "internal_only": key in INTERNAL_ROWS if internal_only is None else internal_only,
         "cells": [{"mid": mid, "v": v} for mid, v in zip(cols, vals)],
         "delta": None, "delta_class": "", "delta_pct": 0, "delta_rel": None,
         "decimals": decimals}
    if better and len(vals) == 2 and None not in vals:
        d = vals[0] - vals[1]
        hi_abs = max(abs(vals[0]), abs(vals[1])) or 1
        good = (d > 0) if better == "higher" else (d < 0)
        r["delta"] = d
        r["delta_class"] = "" if abs(d) < 1e-9 else ("up" if good else "down")
        r["delta_pct"] = min(100, round(abs(d) / hi_abs * 100, 1))
        # the client states every difference as a percentage: score rows in
        # percentage points, the rest relative to the rival
        if vals[1]:
            r["delta_rel"] = round(d / abs(vals[1]) * 100, 1)
    return r


def metric_rows(models: dict, order: list, unit: str,
                extra: list | None = None) -> list:
    """Metrics as ROWS, models as COLUMNS — the reader compares down a column.

    The common rows are fixed, in this order, with these labels, for every
    lane. A lane adds its own measures through `extra`: a list of dicts with
    key, label, better ("higher"|"lower"|None), unit, get (model -> value),
    and optionally hi, internal_only, decimals (ratio rows). Extras follow the
    common rows.

    Units: score | ms | usd_micro | ratio | int | num | text.
    """
    cols = [mid for mid in order if mid in models]
    R = lambda *a, **k: _row(cols, models, *a, **k)            # noqa: E731
    rows = [
        R("mean", "Rating", "higher", "score", lambda m: m["mean"], hi=True),
        R("worst", "Reliability (worst scenario rating)", "higher", "score",
          lambda m: m["worst"]),
        R("wtl", "W–T–L", None, "text", lambda m: m["wtl"]),
        R("judged", "Judged", None, "text",
          lambda m: f'{m["judged_n"]}/{m["eligible"]}'),
        R("below_5", "<5", "lower", "int", lambda m: m["below_5"]),
    ]
    # client-facing, but only when something failed: W-T-L counts scenarios
    # both arms delivered, so without this row a refusal silently disappears
    if any((models[mid].get("failed") or 0) for mid in cols):
        rows.append(R("failed", "Failed", None, "text",
                      lambda m: f'{m["failed"]} of {m["eligible"]}',
                      internal_only=False))
    rows += [
        R("gen_cost", f"Cost per {unit}", "lower", "usd_micro",
          lambda m: round(m["gen_cost_per_scenario_usd"] * 1e6)),
        R("judge_cost", "Judge cost/scen", "lower", "usd_micro",
          lambda m: round(m["judge_cost_per_scenario_usd"] * 1e6)),
        R("lat_min", "Latency min", "lower", "ms", lambda m: m["latency_min_ms"]),
        R("lat_p50", "Latency p50", "lower", "ms", lambda m: m["latency_p50_ms"], hi=True),
        R("lat_max", "Latency max", "lower", "ms", lambda m: m["latency_max_ms"]),
        R("success", "Success", "higher", "ratio", lambda m: m["success_rate"]),
        R("attempts", "Attempts", "lower", "num", lambda m: m["mean_attempts"]),
    ]
    for x in extra or []:
        rows.append(R(x["key"], x["label"], x.get("better"), x["unit"], x["get"],
                      hi=x.get("hi", False), internal_only=x.get("internal_only"),
                      decimals=x.get("decimals", 0)))
    return rows


# ------------------------------------------------------ head-to-head strip


def build_duel(tasks: dict, order: list, unit: str, extra: list | None = None) -> dict | None:
    """The head-to-head strip for the first task with exactly two scored
    models. Slot a is the first model in presentation order (the Gemini arm).
    `extra`: lane rows as dicts {key, label, get, fmt, better}."""
    for task, t in tasks.items():
        ms = [(mid, t["models"][mid]) for mid in order
              if mid in t["models"] and t["models"][mid].get("mean") is not None]
        if len(ms) != 2:
            continue
        (aid, a), (bid, b) = ms

        def metric(key, label, av, bv, fmt, better, client_label=None):
            av, bv = av or 0, bv or 0
            hi = max(av, bv) or 1
            win = None
            if abs(av - bv) > 1e-9:
                win = ("a" if av > bv else "b") if better == "higher" \
                    else ("a" if av < bv else "b")
            return {"key": key, "label": label,
                    "client_label": client_label or label,
                    "internal_only": key in INTERNAL_ROWS,
                    "a": fmt.format(av), "b": fmt.format(bv),
                    "a_pct": f"{av * 10:.1f}%", "b_pct": f"{bv * 10:.1f}%",
                    "aw": round(av / hi * 100, 1), "bw": round(bv / hi * 100, 1),
                    "win": win, "score": key in ("mean", "worst")}

        metrics = [
            metric("mean", "Quality — mean", a["mean"], b["mean"], "{:.2f}",
                   "higher", client_label="Quality — mean rating"),
            metric("worst", "Reliability (worst scenario rating)", a.get("worst"),
                   b.get("worst"), "{:.1f}", "higher"),
        ]
        if a.get("gen_cost_per_scenario_usd") is not None:
            metrics.append(metric("gen_cost", f"Cost per {unit}",
                                  a["gen_cost_per_scenario_usd"],
                                  b.get("gen_cost_per_scenario_usd"), "${:.3f}", "lower"))
        if a.get("latency_p50_ms") is not None:
            metrics.append(metric("lat_p50", "Latency p50",
                                  (a["latency_p50_ms"] or 0) / 1000,
                                  (b.get("latency_p50_ms") or 0) / 1000, "{:.1f}s", "lower"))
        for x in extra or []:
            metrics.append(metric(x["key"], x["label"], x["get"](a), x["get"](b),
                                  x["fmt"], x["better"]))
        return {"task": task, "a": aid, "b": bid, "metrics": metrics}
    return None


# ------------------------------------------------- scenarios and rollups


def _two_decimal_tie(diff: float) -> bool:
    return round(diff, 2) == 0


def scenario_result(cards: list, is_tie: Callable[[float], bool] = _two_decimal_tie):
    """(winner model id or None, margin or None) for one scenario's cards.
    margin is None when fewer than two models have a score: not compared."""
    scored = sorted((c for c in cards if c.get("score") is not None),
                    key=lambda c: -c["score"])
    if len(scored) < 2:
        return None, None
    diff = scored[0]["score"] - scored[1]["score"]
    margin = round(diff, 3)
    return (None if is_tie(diff) else scored[0]["model_id"]), margin


def finish_rollup(row: dict) -> None:
    """Close one family / industry row: each model's mean and win %, and which
    model leads the row. Win % is over the scenarios BOTH models completed —
    a rival's failure is not a loss for the model that delivered."""
    ms = row["models"]
    for m in ms.values():
        m["mean"] = round(sum(m["scores"]) / len(m["scores"]), 2) if m["scores"] else None
        m["n"] = len(m["scores"])
        m["win_pct"] = (round(100 * m["wins"] / row["compared"], 1)
                        if row.get("compared") else None)
    means = [m["mean"] for m in ms.values() if m["mean"] is not None]
    top_mean = max(means, default=None)
    top_wins = max((m["wins"] for m in ms.values()), default=0)
    lead_mean = [mid for mid, m in ms.items() if m["mean"] == top_mean]
    lead_wins = [mid for mid, m in ms.items() if m["wins"] == top_wins]
    # a shared top is not a lead: equal means tag nobody
    row["lead_mean"] = lead_mean[0] if len(ms) > 1 and len(lead_mean) == 1 else None
    row["lead_wins"] = (lead_wins[0] if len(ms) > 1 and len(lead_wins) == 1
                        and top_wins > 0 else None)


def rollup(evidence: list, key: str, label_of: Callable[[str], str] | None = None,
           complete_only: bool = False) -> dict:
    """Group scenarios by `key` ("family" | "industry") into rollup rows.
    `label_of` maps a card's model id to the column it counts under (used when
    a study groups tiers of one model family under one label)."""
    lab = label_of or (lambda mid: mid)
    rows: dict = {}
    for e in evidence:
        name = e.get(key)
        if not name:
            continue
        row = rows.setdefault(name, {"n": 0, "compared": 0, "models": {}})
        row["n"] += 1
        row["compared"] += e.get("margin") is not None
        for c in e["cards"]:
            if c.get("score") is None or (complete_only and e.get("margin") is None):
                continue
            m = row["models"].setdefault(lab(c["model_id"]), {"scores": [], "wins": 0})
            m["scores"].append(c["score"])
            if e.get("winner") is not None and lab(e["winner"]) == lab(c["model_id"]):
                m["wins"] += 1
    for row in rows.values():
        finish_rollup(row)
    return rows


def tally(evidence: list, order: list) -> dict:
    """Every scenario on the page, accounted for once: compared (won / tied)
    or not compared, with who had no result — disjoint groups that add up."""
    t = {"n": len(evidence), "compared": 0, "ties": 0,
         "wins": {mid: 0 for mid in order},
         "only_missing": {mid: 0 for mid in order},
         "all_missing": 0, "some_missing": 0, "all_failed": True}
    for e in evidence:
        if e.get("margin") is not None:
            t["compared"] += 1
            if e.get("winner"):
                t["wins"][e["winner"]] = t["wins"].get(e["winner"], 0) + 1
            else:
                t["ties"] += 1
            continue
        gone = [c for c in e["cards"] if c.get("score") is None]
        if not gone:
            continue
        t["all_failed"] &= all(c.get("state") == "failed" for c in gone)
        if len(gone) == len(e["cards"]):
            t["all_missing"] += 1
        elif len(gone) == 1:
            mid = gone[0]["model_id"]
            t["only_missing"][mid] = t["only_missing"].get(mid, 0) + 1
        else:
            t["some_missing"] += 1
    t["not_compared"] = t["n"] - t["compared"]
    return t
