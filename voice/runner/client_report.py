"""
The client-facing report: one self-contained HTML file, for an outside reader.

WHAT MAKES IT DIFFERENT FROM THE INTERNAL BOARD. Not the numbers - those are
imported wholesale from `runner.dashboard` and nothing here recomputes a
verdict. What differs is the audience. The internal board is a working surface
that leads with what is uncertain and carries our accounting: judge cost, total
spend, attempt counts, unjudged tallies. None of that means anything to a
reader outside the project, and some of it (what we paid to run the study) is
simply not theirs.

WHY IT IMPORTS RATHER THAN REIMPLEMENTS. On 2026-09-04 this project collapsed
two winner rules into one constant because the per-run report decided at 0.5
while the board decided on measured noise, and the two could disagree about the
same pair of runs with nothing on either page saying so. A third surface with
its own arithmetic would reopen that hole three times as wide - and this is the
one surface where a reader cannot check our working. So: `load_runs`,
`rollup_models`, `_duel`, `_scenario_blocks` and `WIN_GAP` come from
`runner.dashboard`, and this file only arranges them.

WHAT IT STILL SAYS OUT LOUD. Everything that qualifies a number. The judge is a
Google model and it is uncalibrated; the headline is currently against the
Google arm; four of five decided scenarios sit inside their own noise. A client
report that dropped those would not be a simpler report, it would be a
different and false one - and the reader most able to catch it is exactly the
reader this file is written for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .audio import (clip_bytes, clip_data_uri, fmt_size, guard_size,
                    safe_name)
from .dashboard import (WIN_GAP, _bar_widths, _duel, _overall, _scenario_blocks,
                        load_runs, rollup_models)

# The arm this report is written to be read alongside. Matched on the vendor
# prefix rather than a pinned id so a Gemini version bump does not silently
# move the column order and flip every sign on the page.
GEMINI_PREFIX = "gemini"

# Who makes each arm. Named on the head-to-head strip because a reader from
# one of these companies is looking for their own name first, and because
# "gemini-3-1-flash-tts vs elevenlabs-multilingual-v2" says nothing about who
# is being compared to whom.
VENDORS = {
    "gemini": "Google", "elevenlabs": "ElevenLabs", "openai": "OpenAI",
    "gpt": "OpenAI", "tts": "OpenAI", "azure": "Microsoft", "playht": "PlayHT",
}


def vendor_of(model_id: str) -> str:
    """The company behind a model id, or an empty string rather than a guess."""
    low = model_id.lower()
    for prefix, name in VENDORS.items():
        if low.startswith(prefix):
            return name
    return ""


def is_gemini(model_id: str) -> bool:
    return model_id.lower().startswith(GEMINI_PREFIX)


def order_models(model_ids: list[str]) -> list[str]:
    """
    Gemini first, competitors after. PRESENTATION ONLY.

    This never touches a subtraction. `_scenario_blocks` computes every gap as
    `duel.a - duel.b`, and `_duel` orders by gate rate, which currently puts
    ElevenLabs in slot a. Reordering the columns without reorienting the number
    would print a gap whose sign disagrees with the columns beside it; flipping
    the subtraction here instead of in `oriented_gap` would make this page
    disagree with the internal board about the same scenario. One helper does
    the orientation, once - see `oriented_gap`.
    """
    return sorted(model_ids, key=lambda m: (not is_gemini(m), m))


def oriented_gap(gap: float | None, duel: dict | None) -> float | None:
    """
    A gap expressed as GEMINI MINUS COMPETITOR, whatever order the duel used.

    THE ONLY PLACE the sign is allowed to move. Positive always means Gemini
    is ahead on this page; the internal board keeps the duel's own order. Both
    describe the same measurement, and `test_client_report.py` asserts the two
    never disagree about who leads.
    """
    if gap is None or not duel:
        return None
    return gap if is_gemini(duel["a"]) else -gap


def pct(score: float | None, out_of: float = 10.0) -> float | None:
    """
    A rubric score as a percentage.

    Requested by the study owner. Worth stating plainly on the page and here:
    9.5/10 rendered as 95% is the SAME rubric score, not an accuracy rate and
    not a probability of anything. The page carries that sentence once.
    """
    return None if score is None else score / out_of * 100.0


def short_verdict(s: dict[str, Any], gem_label: str, oth_label: str,
                  band_pp: float) -> str:
    """
    One line per scenario, in percentage points.

    The board's own `detail` is written for someone auditing the method and
    runs to three clauses. This is the same facts for someone reading a
    comparison. WHAT IT MAY NOT DROP is the qualifier: where a decided gap is
    smaller than the model's own run-to-run spread, the line says so, because
    that is the difference between a result and a coin-flip and it is the
    first thing a trim would quietly lose.
    """
    g = s["gap_pct"]
    fl = s["floor_pct"]
    if s["winner"]:
        who = gem_label if s["winner_is_gemini"] else oth_label
        if s["inside_noise"]:
            return (f"{who} ahead by {abs(g):.1f}pp — but that is inside its own "
                    f"±{fl:.1f}pp run-to-run spread, so a re-run can move it.")
        tail = f" (spread ±{fl:.1f}pp)" if fl is not None else " (run once, no spread yet)"
        return f"{who} ahead by {abs(g):.1f}pp{tail}."
    if s["verdict"] == "Split":
        lead = gem_label if (g or 0) > 0 else oth_label
        return f"{lead} scores {abs(g):.1f}pp higher but cleared fewer gates — not a win."
    if s["verdict"] == "Tie":
        return f"Tie — {abs(g):.1f}pp apart, inside the {band_pp:.1f}pp band."
    if s["verdict"] == "No score":
        return (f"No score — every clip failed its gates across "
                f"{s['passes']} pass{'' if s['passes'] == 1 else 'es'}.")
    if s["verdict"] == "One arm only":
        return "Only one model produced a scored clip here."
    if s["verdict"] == "Not comparable":
        return "Not comparable — one model has a single scored pass."
    return f"{s['verdict']}."


def _titles(runs) -> dict[str, str]:
    """
    Human titles, read from each run's OWN frozen scenario copy.

    Same rule the script reader follows: the page must name what was actually
    run, not what the working tree says today.
    """
    import yaml

    out: dict[str, str] = {}
    for r in runs:
        d = Path(r.runs_root) / r.run_id / "scenarios"
        for f in sorted(d.glob("*.yaml")) if d.exists() else []:
            try:
                doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001 - a bad file is not a report failure
                continue
            if doc.get("id"):
                out[str(doc["id"])] = str(doc.get("title") or "")
    return out


def _clip_row(c: dict[str, Any], runs_root: Path, quality: float | None,
              audio_dir: Path | None, sid: str, mid: str) -> dict[str, Any]:
    """
    One clip, with everything a reader needs to judge it beside the audio.

    `audio_dir` decides HOW the clip reaches the page. Inlined, the page is
    99% base64 and the browser must parse 22 MB before it paints a pixel -
    which is where this stopped being openable. Written beside the page
    instead, the markup is 167 KB and each clip loads only when played.
    """
    from urllib.parse import unquote

    path = Path(runs_root) / unquote(c["audio_rel"]) if c.get("audio_rel") else None
    audio = None
    if path and path.exists():
        if audio_dir is None:
            audio = clip_data_uri(path, quality)
        else:
            name = safe_name(sid, mid, c.get("variant") or "", c.get("run_label") or "") + ".mp3"
            (audio_dir / name).write_bytes(clip_bytes(path, quality))
            audio = f"audio/{name}"
    return {
        "variant": c.get("variant") or "",
        "run_label": c.get("run_label") or "",
        "score": c.get("score"),
        "score_pct": pct(c.get("score")),
        "status": c.get("status"),
        "wer": c.get("wer"),
        "wer_pct": None if c.get("wer") is None else c["wer"] * 100.0,
        "duration_s": c.get("duration_s"),
        "latency_ms": c.get("latency_ms"),
        "ttfa_ms": c.get("ttfa_ms"),
        "audio": audio,
    }


def build(runs_root: Path, modality: str = "voice", quality: float | None = None,
          audio_dir: Path | None = None) -> dict[str, Any]:
    """Everything the template needs. No verdicts are decided here."""
    runs = load_runs(runs_root, modality)
    if not runs:
        raise SystemExit(f"no {modality} runs under {runs_root}")
    models = rollup_models(runs)
    duel = _duel(models)
    blocks = _scenario_blocks(runs, duel, {m.model_id: m.accent for m in models})
    by_id = {m.model_id: m for m in models}
    titles = _titles(runs)

    order = order_models([m.model_id for m in models])
    gem_id = next((m for m in order if is_gemini(m)), None)
    oth_id = next((m for m in order if not is_gemini(m)), None)

    scenarios = []
    for b in blocks:
        cols = []
        for mid in order:
            col = next((c for c in b["side"] if c["model_id"] == mid), None)
            if col is None:
                continue
            cols.append({
                "model": mid,
                "is_gemini": is_gemini(mid),
                "mean": col["mean"],
                "mean_pct": pct(col["mean"]),
                "n_scored": col["n_scored"],
                "n_cells": col["n_cells"],
                "failed": col["failed"],
                "worst_wer_pct": (None if col["worst_wer"] is None
                                  else col["worst_wer"] * 100.0),
                "is_winner": col["is_winner"],
                "clips": [_clip_row(c, runs_root, quality, audio_dir, b["id"], mid)
                          for c in col["clips"]],
            })
        g = oriented_gap(b["gap"], duel)
        # Per-run scores, ordered Gemini first like everything else.
        rows = sorted(b["rows"], key=lambda r: (not is_gemini(r["model_id"]), r["model_id"]))
        scenarios.append({
            "id": b["id"], "title": titles.get(b["id"], ""),
            "industry": b["industry"], "passes": b["n_passes"],
            "verdict": b["verdict"], "detail": b["detail"], "winner": b["winner"],
            "winner_is_gemini": bool(b["winner"] and is_gemini(b["winner"])),
            "gap": g, "gap_pct": pct(g), "floor": b["floor"],
            "floor_pct": pct(b["floor"]),
            "inside_noise": (g is not None and b["floor"] is not None
                             and abs(g) <= b["floor"]),
            "scripts": b["scripts"], "cols": cols,
            "labels": b["labels"], "rows": rows,
            "group": b["group"], "ttfa": b["ttfa"], "throughput": b["throughput"],
            "stale": b["stale"],
            # Sort keys. None stays None so the page can push it last rather
            # than treating "never scored" as zero.
            "sort_gem": pct(next((c["mean"] for c in cols if c["is_gemini"]), None)),
            "sort_oth": pct(next((c["mean"] for c in cols if not c["is_gemini"]), None)),
            "sort_gap": pct(g),
        })

    # The transposed table: metrics down, models across, difference last.
    def _row(label, key, fmt, higher_better, note=""):
        gv = getattr(by_id[gem_id], key) if gem_id else None
        ov = getattr(by_id[oth_id], key) if oth_id else None
        return {"label": label, "fmt": fmt, "note": note,
                "gem": gv, "oth": ov,
                "delta": (None if gv is None or ov is None else gv - ov),
                "higher_better": higher_better}

    metrics = [
        _row("Quality", "mean_score", "pct", True,
             "rubric score, expressed as a percentage"),
        _row("Gates passed", "gate_pass_rate", "rate", True,
             "how often the clip was usable at all"),
        _row("Run-to-run spread", "repeat_spread", "pct", False,
             "same script twice — smaller is more consistent"),
        _row("Worst word error rate", "worst_wer", "rate", False,
             "the worst single clip, not the average"),
        _row("Cost per clip", "mean_cost", "usd", False, ""),
        _row("Cost per audio minute", "cost_per_audio_minute", "usd", False, ""),
        _row("Latency, median", "p50_latency", "ms", False, "whole call"),
        _row("Latency, 95th percentile", "p95_latency", "ms", False,
             "the tail a real-time claim lives on"),
    ]

    gem_label = "Gemini" if gem_id else "—"
    oth_label = (oth_id or "").split("-")[0].title() or "the other model"
    band_pp = WIN_GAP * 10
    for s in scenarios:
        s["short"] = short_verdict(s, gem_label, oth_label, band_pp)

    # HEAD TO HEAD, from the SAME `metrics` rows the table below renders.
    # Built from one list rather than two so the strip and the table can never
    # disagree about who is ahead on a row - which is exactly the failure the
    # overall verdict had this morning, a headline contradicting the table
    # underneath it.
    strip = []
    for m in metrics:
        gw, ow, win = _bar_widths(m["gem"], m["oth"], not m["higher_better"])
        # QUALITY RESPECTS THE DECISION BAND. Everything else on this page
        # refuses to name a winner inside 0.05, and a star on a 0.01 gap would
        # quietly contradict that - the strip is the most glanceable thing
        # here and the easiest place to over-claim.
        if (m["label"] == "Quality" and m["delta"] is not None
                and abs(m["delta"]) <= WIN_GAP):
            win = None
        strip.append({"label": m["label"], "fmt": m["fmt"],
                      "gem": m["gem"], "oth": m["oth"],
                      "gem_w": gw, "oth_w": ow,
                      "gem_win": win == "a", "oth_win": win == "b"})

    n_win = sum(1 for s in scenarios if s["winner"])
    overall = _overall(models)
    return {
        "win_gap": WIN_GAP,
        # The headline is imported, never restated. It currently runs AGAINST
        # the Google arm; a client report that quietly dropped it would be a
        # different report, not a simpler one.
        "overall_verdict": overall["verdict"], "overall_detail": overall["detail"],
        "gemini": gem_id, "other": oth_id,
        "scenarios": scenarios, "metrics": metrics,
        "industries": sorted({s["industry"] for s in scenarios}),
        "n_scenarios": len(scenarios),
        "n_clips": sum(len(c["clips"]) for s in scenarios for c in s["cols"]),
        "n_runs": len(runs),
        "gemini_wins": sum(1 for s in scenarios if s["winner_is_gemini"]),
        "other_wins": n_win - sum(1 for s in scenarios if s["winner_is_gemini"]),
        "n_win": n_win,
        "inside_noise": sum(1 for s in scenarios if s["winner"] and s["inside_noise"]),
        "strip": strip,
        "gem_vendor": vendor_of(gem_id or ""), "oth_vendor": vendor_of(oth_id or ""),
        "band_pp": band_pp, "gem_label": gem_label, "oth_label": oth_label,
        "judge": runs[-1].judge_model, "asr": runs[-1].asr_model,
        "judge_is_google": is_gemini(runs[-1].judge_model or ""),
        "uncalibrated": any(not r.calibration_passed for r in runs),
    }


def render_client_report(runs_root: Path, modality: str = "voice",
                         quality: float | None = None, inline: bool = False,
                         out_dir: Path | None = None) -> Path:
    """
    Write the shareable report.

    DEFAULT IS A FOLDER, not one file, and that is a correction rather than a
    preference. Inlining every clip produced a 22 MB page that is 99.2% base64
    - 167 KB of markup behind 22 MB the browser has to parse and hold before
    it paints anything - and it would not open. As a folder the page is the
    167 KB and each clip is fetched only when someone presses play.

    `inline=True` still produces the single self-contained file, which is the
    right shape when the report has to travel as one attachment and the reader
    is willing to wait for it.

    `out_dir` writes the folder somewhere other than beside the runs - which
    is how `voice/dashboard/` is produced. That folder is COMMITTED, unlike
    `runs/`, so the report travels with the repo and can be deployed from it.
    Only the report goes there: the run evidence it was derived from stays
    gitignored, the same seam `apps/dashboard/public/data` uses in the study
    console.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    if inline:
        if out_dir is not None:
            raise SystemExit("--out writes a folder; it cannot be combined with --inline")
        out_dir, out = Path(runs_root), Path(runs_root) / "client-report.html"
        audio_dir = None
    else:
        out_dir = Path(out_dir) if out_dir else Path(runs_root) / "client-report"
        out = out_dir / "index.html"
        audio_dir = out_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        # Clear stale clips so a renamed scenario leaves nothing behind. Only
        # *.mp3, and only in audio/ - README.md and vercel.json live beside
        # the page, are hand-written, and are not ours to remove.
        for stale in audio_dir.glob("*.mp3"):
            stale.unlink()

    ctx = build(runs_root, modality, quality, audio_dir)
    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parent / "templates")),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["pct1"] = lambda v: "—" if v is None else f"{v:.1f}%"
    env.filters["pct0"] = lambda v: "—" if v is None else f"{v:.0f}%"
    env.filters["usd4"] = lambda v: "—" if v is None else f"${v / 1e6:,.4f}"
    env.filters["secs"] = lambda v: "—" if not v else f"{float(v) / 1000:.1f}s"
    env.filters["ms"] = lambda v: "—" if not v else f"{int(v)} ms"

    # LSTRIP: Jinja's macro definitions at the top of the template each leave a
    # newline behind, so the document opened with blank lines before its
    # doctype - which is enough to drop a browser into quirks mode.
    html = env.get_template("client.html.j2").render(**ctx).lstrip()
    data = html.encode("utf-8")
    guard_size(len(data), out)
    out.write_bytes(data)

    if audio_dir is None:
        print(f"client report: {out} ({fmt_size(len(data))}, "
              f"{ctx['n_clips']} clips inlined)")
    else:
        audio_bytes = sum(f.stat().st_size for f in audio_dir.glob("*.mp3"))
        print(f"client report: {out} (page {fmt_size(len(data))}, "
              f"{ctx['n_clips']} clips in audio/ = {fmt_size(audio_bytes)}, "
              f"{fmt_size(len(data) + audio_bytes)} total)")
        print(f"  share the whole {out_dir.name}/ folder - the page reads audio/ beside it")
    return out
