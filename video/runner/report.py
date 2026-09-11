"""Aggregate one run into a single static HTML file (plan §16).

Images inlined as data URIs so the file opens from the run folder with no
server and can be mailed to anyone. Quality, cost, latency and reliability
are four separate columns — never one blended number.
"""
from __future__ import annotations

import base64
import io
import json
import re as _re
import shutil
import webbrowser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .generate import Manifest, _find_existing_output
from .scoring import MEAN_GAP_DOOR, TIE_BAND, aggregate
from .telemetry import RunFiles

THUMB_MAX_PX = 800


def _thumb_data_uri(path: Path, max_px: int = THUMB_MAX_PX, quality: int = 82) -> str | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _mini_data_uri(path: Path) -> str | None:
    """Tiny thumb for collapsed summary rows and the carousel."""
    return _thumb_data_uri(path, max_px=140, quality=70)


# Whole-report budget for inlining video clips as data URIs. Under it the
# report stays one mailable file (a smoke run is a few MB of mp4). Over it
# (a full 1080p bank run is hundreds of MB) clips are referenced by path
# relative to the run folder instead — report.html then plays them only when
# opened from inside the run folder, and the report says so visibly.
VIDEO_INLINE_TOTAL_MAX = 60 * 1024 * 1024


def _video_src(path: Path, run_dir: Path, inline: bool) -> str:
    if inline:
        return ("data:video/mp4;base64,"
                + base64.b64encode(path.read_bytes()).decode())
    return str(path.relative_to(run_dir))


# A shareable client deliverable has to carry its clips inside the file.
# Raw bank clips are ~14 Mbit/s, far past the inline budget, so --self-contained
# writes compact previews beside the run and inlines those instead. The
# originals are never touched or replaced, and the report says on its face
# that the embedded clips are re-encoded.
PREVIEW_CRF = 28
PREVIEW_DIRNAME = "previews"


def _build_previews(paths: list, run_dir: Path, crf: int = PREVIEW_CRF) -> dict:
    """src mp4 -> compact preview mp4 (same resolution, same duration, audio
    kept). Returns {} if ffmpeg is missing or any clip fails, so the caller
    falls back to path references rather than shipping a half-empty page."""
    import shutil as _shutil
    import subprocess
    if not _shutil.which("ffmpeg"):
        return {}
    out_dir = run_dir / PREVIEW_DIRNAME
    out_dir.mkdir(exist_ok=True)
    made = {}
    for src in paths:
        dst = out_dir / f"{src.parent.name}--{src.stem}-crf{crf}.mp4"
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src),
                   "-c:v", "libx264", "-crf", str(crf),
                   "-preset", "veryfast", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", "-c:a", "aac", "-b:a", "96k",
                   str(dst)]
            if subprocess.run(cmd, capture_output=True).returncode != 0:
                return {}
        made[src] = dst
    return made


def _env(names: dict | None = None, client: bool = False) -> Environment:
    """One template set, two audiences. `client=True` switches quality into
    percentages and lets macros drop internal-only rows via the CLIENT global —
    the audience is a property of the render, not of the data, so it lives here
    rather than being threaded through every macro call site."""
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["usd"] = lambda micro: f"${micro / 1e6:,.4f}"
    env.filters["s"] = lambda ms: f"{ms / 1000:.1f}s" if ms is not None else "—"
    nm = names or {}
    env.filters["disp"] = lambda mid: nm.get(mid, mid)
    # "text_to_video" -> "Text to video": the task key is a data
    # identifier, not a heading a reader should have to decode
    env.filters["tasktitle"] = lambda t: (
        str(t).replace("_", " ").capitalize() if t else "")

    def _q(v, short: bool = False) -> str:
        """A quality score: one 0-10 number in the audience's units. Client
        means keep a decimal — whole numbers would hide gaps smaller than the
        tie band and leave the delta column disagreeing with the values."""
        if v is None:
            return "—"
        if client:
            return f"{v * 10:.0f}%" if short else f"{v * 10:.1f}%"
        return f"{v:.1f}" if short else f"{v:.2f}"

    def _qd(v) -> str:
        """A quality GAP: points internally, percentage points for the client."""
        if v is None:
            return "—"
        return f"{v * 10:+.1f} pp" if client else f"{v:+.2f}"

    env.filters["q"] = _q
    env.filters["qd"] = _qd
    env.globals["CLIENT"] = client
    env.globals["TIE_BAND"] = TIE_BAND                 # 0.5 points, 0-10 scale
    env.globals["TIE_BAND_PP"] = int(TIE_BAND * 10)    # the same band as 5%
    env.globals["TIE_BAND_FRAC"] = f"{TIE_BAND / 10:g}"  # ... and as 0.05
    return env


def _client_prose(text: str) -> str:
    """Verdict prose for the client report. The stored verdict is untouched —
    this is a presentation copy that restates the tie band in the percentage
    points the rest of that report uses. Cost tie-breakers are kept: the client
    report shows a cost row, so hiding "cheaper: X" would be inconsistent, and
    that fact does not always favour the Gemini arm."""
    if not text:
        return text
    text = _re.sub(r"mean gap (\d+(?:\.\d+)?)",
                   lambda m: f"mean gap {float(m.group(1)) * 10:.1f} pp", text)
    band = f"{int(MEAN_GAP_DOOR * 10)} pp"
    return (text.replace(f"< {MEAN_GAP_DOOR}", f"< {band}")
                .replace(f">= {MEAN_GAP_DOOR}", f">= {band}"))


# --------------------------------------------------------------------------
# Why a scenario has no result — in words a reader outside the team can use.
#
# A raw provider code is not an answer. "OutputAudioSensitiveContentDetected"
# tells a buyer nothing; "it generated the clip, then blocked its own
# soundtrack" tells them what they would hit with their own footage. These
# refusals are the main product difference between the two arms, so they are
# reported, not hidden — and OUR OWN defects are labelled as ours rather than
# charged to a model that did nothing wrong.
# --------------------------------------------------------------------------

_FAILURE_KINDS = (
    ("OutputAudioSensitiveContentDetected", {
        "outcome": "Blocked after generating",
        "stage": "audio filter, ~4-5 min in",
        "client": ("Generated the full clip, then blocked its own soundtrack: "
                   "with audio enabled it invented a backing track and its "
                   "copyright filter judged that audio too close to protected "
                   "material. The video was made and then withheld. Nothing we "
                   "supplied was copyrighted."),
        "ours": False}),
    ("InputVideoSensitiveContentDetected", {
        "outcome": "Declined before starting",
        "stage": "privacy filter, within seconds",
        "client": ("Refused to accept the source footage: its privacy filter "
                   "detected an identifiable face and will not edit that clip. "
                   "Declined in seconds, before any generation. Turning audio "
                   "off does not change it — we tested that directly."),
        "ours": False}),
    ("recitation", {
        "outcome": "Declined to return output",
        "stage": "recitation filter, 3 attempts",
        "client": ("Generated the clip but declined to return it: its "
                   "recitation filter judged the result too close a reproduction "
                   "of the source footage. Tried three times, declined each time."),
        "ours": False}),
    ("exceeds maximum duration", {
        "outcome": "Source too long",
        "stage": "length limit, on submission",
        "client": ("Rejected the source for length: this model edits clips of "
                   "at most 10 seconds. The source has since been re-cut under "
                   "that limit."),
        "ours": False}),
    ("resource not found", {
        "outcome": "Not completed — our error",
        "stage": "our defect — no charge",
        "client": ("Not completed for a reason on our side, not the model's: "
                   "the source file was published at a location this run pointed "
                   "past. No charge, and no reflection on the model."),
        "ours": True}),
    ("could not reach the asset url", {
        "outcome": "Not completed — our error",
        "stage": "our defect — no charge",
        "client": ("Not completed for a reason on our side, not the model's: a "
                   "transient network failure while publishing the source. No "
                   "charge, and no reflection on the model."),
        "ours": True}),
)


def _explain_failure(error: str) -> dict:
    e = str(error or "")
    for needle, info in _FAILURE_KINDS:
        if needle.lower() in e.lower():
            return dict(info, internal=e[:300])
    return {"outcome": "Did not complete", "stage": "unknown",
            "client": "Did not return a usable clip.", "ours": False,
            "internal": e[:300]}


def _gemini_first(model_ids, vendors: dict | None = None) -> list:
    """Presentation order: the Google/Gemini arm first, the rest alphabetical.
    Ids stay the keys everywhere data is stored — this is column order, nothing
    more. With no Google arm the result is plain alphabetical, so ordering
    never becomes a hidden dependency."""
    vd = vendors or {}

    def rank(mid: str):
        google = vd.get(mid) == "Google" or "gemini" in mid.lower()
        return (0 if google else 1, mid)

    return sorted(model_ids, key=rank)


# Rows the client deliverable does not carry: internal diagnostics only.
# Generation cost per clip IS client-facing (study lead's call, 2026-09-04);
# judging cost is ours, not theirs, so it stays internal.
_INTERNAL_ROWS = {"judged", "below_5", "judge_cost", "success", "attempts"}


def _metric_rows(models: dict, order: list) -> list:
    """Metrics as ROWS, models as COLUMNS — the reader compares down a column
    instead of across a row. `delta` is Gemini minus competitor in the metric's
    own units and `better` says which sign is good, so latency and cost read
    the right way round. Two-model lanes only; otherwise there is no delta."""
    cols = [mid for mid in order if mid in models]

    def row(key, label, better, unit, get, hi=False):
        vals = []
        for mid in cols:
            try:
                vals.append(get(models[mid]))
            except (KeyError, TypeError):
                vals.append(None)
        r = {"key": key, "label": label, "unit": unit, "hi": hi, "better": better,
             "internal_only": key in _INTERNAL_ROWS,
             "cells": [{"mid": mid, "v": v} for mid, v in zip(cols, vals)],
             "delta": None, "delta_class": "", "delta_pct": 0,
             "delta_rel": None}
        if better and len(vals) == 2 and None not in vals:
            d = vals[0] - vals[1]
            hi_abs = max(abs(vals[0]), abs(vals[1])) or 1
            good = (d > 0) if better == "higher" else (d < 0)
            r["delta"] = d
            r["delta_class"] = "" if abs(d) < 1e-9 else ("up" if good else "down")
            r["delta_pct"] = min(100, round(abs(d) / hi_abs * 100, 1))
            # the client report states every difference as a percentage:
            # score rows in percentage points, the rest relative to the rival
            if vals[1]:
                r["delta_rel"] = round(d / abs(vals[1]) * 100, 1)
        return r

    return [
        row("mean", "Rating", "higher", "score", lambda m: m["mean"], hi=True),
        row("worst", "Reliability (worst scenario rating)", "higher", "score", lambda m: m["worst"]),
        row("wtl", "W–T–L", None, "text", lambda m: m["wtl"]),
        row("judged", "Judged", None, "text",
            lambda m: f'{m["judged_n"]}/{m["eligible"]}'),
        row("below_5", "<5", "lower", "int", lambda m: m["below_5"]),
        row("gen_cost", "Cost per clip", "lower", "usd_micro",
            lambda m: round(m["gen_cost_per_scenario_usd"] * 1e6)),
        row("judge_cost", "Judge cost/scen", "lower", "usd_micro",
            lambda m: round(m["judge_cost_per_scenario_usd"] * 1e6)),
        row("lat_p50", "Latency p50", "lower", "ms",
            lambda m: m["latency_p50_ms"], hi=True),
        row("lat_max", "Latency max", "lower", "ms",
            lambda m: m["latency_max_ms"]),
        row("success", "Success", "higher", "ratio",
            lambda m: m["success_rate"]),
        row("attempts", "Attempts", "lower", "num",
            lambda m: m["mean_attempts"]),
    ]


def merge_runs(run_dirs: list, out_dir: Path,
               task_groups: dict | None = None) -> Path:
    """Fold several runs into one run folder so they report as a single study.

    A pair has to live in one run on one source, so re-running a scenario
    means a new run — and the edits ended up spread over four of them. That
    is a bookkeeping artefact, not a boundary anyone reading the report cares
    about, and `build_combined_report` deliberately will not merge across
    tabs ("each lane keeps its own models, costs and verdict").

    Merging is safe here precisely because the run files are append-only and
    aggregate() already takes the LATEST row per cell. Concatenating the JSONL
    in chronological run order therefore gives exactly the intended answer:
    the most recent attempt at each scenario x model wins, and earlier
    attempts stay in the record as history. Outputs are copied in the same
    order so the file at a given relative path belongs to the row that won.

    The merged folder is derived, never authoritative. The original runs stay
    immutable and are listed in the merged manifest as `merged_from`.
    """
    out_dir = Path(out_dir)
    (out_dir / "scenarios").mkdir(parents=True, exist_ok=True)
    runs = [Path(d) for d in run_dirs]

    merged: dict = {}
    for rd in runs:                                   # chronological order
        mf = json.loads((rd / "manifest.json").read_text())
        if not merged:
            merged = {k: v for k, v in mf.items() if k != "cells"}
            merged["cells"] = {}
            merged["models"] = list(mf.get("models", []))
        merged["cells"].update(mf.get("cells", {}))
        merged.setdefault("rubric_hashes", {}).update(mf.get("rubric_hashes", {}))
        merged.setdefault("effective_weights", {}).update(mf.get("effective_weights", {}))
        merged.setdefault("inputs", {}).update(mf.get("inputs", {}))
        seen = {m["id"] for m in merged["models"]}
        for m in mf.get("models", []):
            if m["id"] not in seen:
                merged["models"].append(m); seen.add(m["id"])
        for name in ("telemetry", "checks", "judge", "scores"):
            src = rd / f"{name}.jsonl"
            if src.exists():
                with (out_dir / f"{name}.jsonl").open("a") as fh:
                    fh.write(src.read_text())
        for sub in ("outputs", "inputs"):
            if (rd / sub).exists():
                shutil.copytree(rd / sub, out_dir / sub, dirs_exist_ok=True)
        for f in (rd / "scenarios").glob("*.yaml"):
            shutil.copy2(f, out_dir / "scenarios" / f.name)

    merged["run_id"] = out_dir.name
    merged["state"] = "scored"
    merged["merged_from"] = [r.name for r in runs]

    # Report-side task grouping. A task is a real property of a scenario — it
    # selects the rubric, the checks and the required inputs — so the SCENARIO
    # is never edited and the original runs are untouched. But a lane holding
    # one scenario gets the same visual weight in a report as a lane holding
    # seven, which misleads by layout rather than by number. VID-AD-09 is ad
    # row 9 of the sheet and belongs with the ads; it is text_to_video only
    # because testing "does the text survive a camera push" cannot start from
    # a supplied still. Regrouping is recorded here so it is never silent.
    if task_groups:
        moved = {}
        for cell in merged["cells"].values():
            new = task_groups.get(cell["task"])
            if new:
                moved.setdefault(cell["scenario_id"], (cell["task"], new))
                cell["task"] = new
        merged["task_grouping"] = {
            "map": dict(task_groups),
            "scenarios": {sid: {"actual": was, "reported_under": now}
                          for sid, (was, now) in sorted(moved.items())}}
    (out_dir / "manifest.json").write_text(json.dumps(merged, indent=2, default=str))
    return out_dir


def build_report(project_root: Path, run_dir: Path, open_browser: bool = False,
                 hide_industries: tuple = (), self_contained: bool = False,
                 complete_only: bool = False,
                 preview_crf: int = PREVIEW_CRF) -> Path:
    """Writes BOTH deliverables from ONE context, every time:

        report.html         internal — summary tiles, cost, every diagnostic
        report-client.html  the client deliverable — percentages, no cost,
                            no internal metrics

    One context means the two files can never disagree on a number, and there
    is no flag to forget. Returns the internal path (the caller's contract)."""
    run_dir = Path(run_dir)
    ctx = _build_context(project_root, run_dir, hide_industries=hide_industries,
                         self_contained=self_contained,
                         complete_only=complete_only,
                         preview_crf=preview_crf)
    out = run_dir / "report.html"
    out.write_text(_env(ctx["names"]).get_template("report.html.j2").render(**ctx))
    client = run_dir / "report-client.html"
    client.write_text(_env(ctx["names"], client=True)
                      .get_template("report-client.html.j2").render(**ctx))
    Manifest(run_dir).set_run_state("reported")
    if open_browser:
        webbrowser.open(out.as_uri())
    return out


def build_combined_report(project_root: Path, run_dirs: list, out_path: Path,
                          open_browser: bool = False,
                          hide_industries: tuple = (),
                          brief: bool = False) -> Path:
    """One dashboard across several runs — each run becomes a tab (its task
    lane), with a combined overview on top. Cross-tab numbers are NOT merged:
    each lane keeps its own models, costs and verdict.

    brief=True: executive summary — lanes stacked on one page (no tabs), only
    duel strip + verdict + industries per lane; no per-scenario evidence."""
    ctxs = [_build_context(project_root, Path(d), hide_industries=hide_industries)
            for d in run_dirs]
    names: dict = {}
    vendors: dict = {}
    for c in ctxs:
        names.update(c["names"])
        vendors.update(c.get("vendors") or {})
    overview = {
        "n_scenarios": sum(len(c["evidence"]) for c in ctxs),
        "gen_micro": sum(c["totals"]["gen_micro"] for c in ctxs),
        "judge_micro": sum(c["totals"]["judge_micro"] for c in ctxs),
        "rows": [],
    }
    for c in ctxs:
        for task, t in c["agg"]["tasks"].items():
            for p in t["pairs"]:
                overview["rows"].append({
                    "run_id": c["manifest"]["run_id"], "task": task,
                    "n": len(c["evidence"]),
                    "completed": c["completion"].get("completed", 0),
                    "verdict": (names.get(p["winner"], p["winner"]) + " wins")
                               if p["winner"] else "tie",
                    "detail": p.get("door") or "decided on cost / latency facts",
                    "models": [names.get(m, m) for m
                               in _gemini_first(t["models"], vendors)]})
    # brief mode: one mixed scenario list + one industry table across lanes.
    # Model arms that differ only by tier are grouped under one label (the
    # parenthetical is dropped) and the grouping is stated on the page.
    merged = None
    if brief:
        def base_label(mid: str) -> str:
            return names.get(mid, mid).split(" (")[0]

        merged_evidence = []
        for c in ctxs:
            for e in c["evidence"]:
                e = dict(e)
                if e["winner"]:
                    e["winner"] = base_label(e["winner"])
                merged_evidence.append(e)
        merged_evidence.sort(key=lambda e: e["id"])

        industries: dict = {}
        families: dict = {}
        groups: dict = {}
        label_vendor: dict = {}
        for e in merged_evidence:
            families.setdefault(e["family"], {"n": 0, "models": {}})["n"] += 1
            ind = industries.setdefault(e["industry"], {"n": 0, "models": {}}) \
                if e["industry"] else None
            if ind:
                ind["n"] += 1
            for card in e["cards"]:
                lbl = base_label(card["model_id"])
                groups.setdefault(lbl, set()).add(
                    names.get(card["model_id"], card["model_id"]))
                label_vendor.setdefault(lbl, vendors.get(card["model_id"]))
                if ind and card["score"] is not None:
                    m = ind["models"].setdefault(lbl, {"scores": [], "wins": 0})
                    m["scores"].append(card["score"])
                    if e["winner"] == lbl:
                        m["wins"] += 1
        for ind in industries.values():
            for m in ind["models"].values():
                m["mean"] = round(sum(m["scores"]) / len(m["scores"]), 2)
                m["n"] = len(m["scores"])

        mixed = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
        # brief mode groups arms by DISPLAY label, so order by the label's
        # vendor rather than by model id — Gemini stays on the left here too
        merged_order = sorted(
            groups, key=lambda l: (0 if label_vendor.get(l) == "Google" else 1, l))
        merged = {
            "evidence": merged_evidence, "industries": industries,
            "families": families,
            "family_models": merged_order, "model_order": merged_order,
            "hidden_industries": ctxs[0]["hidden_industries"],
            "merged_note": ("Grouped columns: " + "; ".join(
                f"{k} covers {' and '.join(v)}" for k, v in mixed.items()) +
                " — per-lane tiers are on each scenario card.") if mixed else "",
        }

    html = _env(names).get_template("combined.html.j2").render(
        runs=ctxs, overview=overview, brief=brief, merged=merged)
    out_path = Path(out_path)
    out_path.write_text(html)
    if open_browser:
        webbrowser.open(out_path.resolve().as_uri())
    return out_path


def _build_context(project_root: Path, run_dir: Path,
                   hide_industries: tuple = (),
                   self_contained: bool = False,
                   complete_only: bool = False,
                   preview_crf: int = PREVIEW_CRF) -> dict:
    run_dir = Path(run_dir)
    manifest = Manifest(run_dir)
    files = RunFiles(run_dir)
    agg = aggregate(run_dir)

    if complete_only:
        # Report quality over the scenarios EVERY arm actually completed.
        # Otherwise the means compare different scenario sets: on 2026-09-10
        # Seedance refused two ads, so Omni's mean covered 10 scenarios and
        # Seedance's 8, printed side by side as though they measured the same
        # thing — and dropping those two moves Omni from ahead to behind.
        # The excluded scenarios are not hidden: they stay in the reliability
        # figures (failed / refused / unjudged) and in the evidence list,
        # because a refusal is a product fact, not missing data.
        for t in agg.get("tasks", {}).values():
            for m in t["models"].values():
                m["mean"] = m.get("mean_complete")
                m["worst"] = m.get("worst_complete")
                m["judged_n"] = m.get("complete_n", 0)
                m["below_5"] = sum(1 for v in m.get("numeric_complete", []) if v < 5)
    telemetry = files.read("telemetry")
    judge_rows = files.read("judge")
    scores = files.read("scores")
    checks = files.read("checks")

    score_by_cell = {(r["scenario_id"], r["model_id"]): r for r in scores}
    checks_by_cell = {(r["scenario_id"], r["model_id"]): r for r in checks}
    judge_by_cell = {(r["scenario_id"], r["model_id"]): r for r in judge_rows
                     if r.get("status") == "judged"}

    # vendor attribution — say plainly which arm is the Google/Gemini side and
    # which is the rival, in the tiles, the tables, the duel strip and the
    # footnotes. Computed early because presentation ORDER depends on it.
    def _vendor(provider: str) -> str:
        p = (provider or "").lower()
        if p.startswith("google"):
            return "Google"
        if p.startswith("openai"):
            return "OpenAI"
        if p.startswith(("byteplus", "bytedance")):
            return "ByteDance"
        return provider or "?"

    vendors = {m["id"]: _vendor(m.get("provider", ""))
               for m in manifest.data.get("models", [])}
    # one presentation order, used by every table, tile, card and duel slot.
    # Any model that scored but is missing from the manifest still gets a
    # column — a column must never vanish because of an ordering list.
    _all_ids = {m["id"] for m in manifest.data.get("models", [])}
    _all_ids |= {mid for t in agg["tasks"].values() for mid in t["models"]}
    model_order = _gemini_first(_all_ids, vendors)
    _rank = {mid: i for i, mid in enumerate(model_order)}

    # per-model W-T-L rollup within each task, plus the transposed metric rows
    for task, t in agg["tasks"].items():
        for mid, m in t["models"].items():
            w = l = ti = 0
            for p in t["pairs"]:
                if p["a"] == mid:
                    w, l, ti = w + p["wins_a"], l + p["wins_b"], ti + p["ties"]
                elif p["b"] == mid:
                    w, l, ti = w + p["wins_b"], l + p["wins_a"], ti + p["ties"]
            m["wtl"] = f"{w}-{ti}-{l}"
        # rides through ctx.agg, so every existing call site gets it for free
        t["metric_rows"] = _metric_rows(t["models"], model_order)

    # evidence blocks
    import yaml as _yaml
    scenarios_meta = {}
    for f in sorted((run_dir / "scenarios").glob("*.yaml")):
        d = _yaml.safe_load(f.read_text())
        scenarios_meta[d["id"]] = d

    ok_rows = {(r["scenario_id"], r["model_id"]): r for r in telemetry
               if r.get("status") == "ok"}

    # industry overlay from the sheet's mapping (configs/industry_map.yaml);
    # runs stay immutable — this is a report-side join keyed by scenario id
    industry_map: dict = {}
    imap_path = Path(project_root) / "configs" / "industry_map.yaml"
    if imap_path.exists():
        industry_map = (_yaml.safe_load(imap_path.read_text()) or {}).get("scenarios", {})

    # friendly model names from configs/models.yaml (display:); ids remain the
    # keys everywhere data is stored — this is presentation only
    names: dict = {}
    mpath = Path(project_root) / "configs" / "models.yaml"
    if mpath.exists():
        cfg = _yaml.safe_load(mpath.read_text()) or {}
        for lane in cfg.values():           # top-level lanes: image:, voice:, …
            if not isinstance(lane, list):
                continue
            for m in lane:
                if isinstance(m, dict) and m.get("display"):
                    names[m["id"]] = m["display"]
    # verdict prose ("cheaper: <id>") is stored with raw ids; display names
    # are substituted here, presentation-side only
    for t in agg["tasks"].values():
        for p in t["pairs"]:
            for key in ("door", "note"):
                if p.get(key):
                    for k, v in names.items():
                        p[key] = p[key].replace(k, v)
            # the same verdict, told without cost, for the client report
            p["door_client"] = _client_prose(p.get("door"))
            p["note_client"] = _client_prose(p.get("note"))

    # video outputs render as playable <video> elements (PIL cannot thumbnail
    # an mp4); inline them only while the whole report stays mailable
    video_paths = []
    for cell in manifest.data["cells"].values():
        p = _find_existing_output(
            run_dir / "outputs" / cell["modality"] / cell["scenario_id"],
            cell["model_id"])
        if p is not None and p.suffix.lower() == ".mp4":
            video_paths.append(p)
    # An edit's SOURCE is evidence too: "everything else unchanged" is not
    # checkable without seeing what it started from. Source clips therefore
    # share the inline budget and the preview pass with the outputs.
    for rows in manifest.data.get("inputs", {}).values():
        for row in rows:
            sp = run_dir / row["path"]
            if sp.suffix.lower() == ".mp4" and sp.exists():
                video_paths.append(sp)
    total_video_bytes = sum(p.stat().st_size for p in video_paths)
    videos_inline = 0 < total_video_bytes <= VIDEO_INLINE_TOTAL_MAX
    videos_transcoded = False
    preview_of: dict = {}
    if self_contained and video_paths and not videos_inline:
        preview_of = _build_previews(video_paths, run_dir, preview_crf)
        if preview_of:
            videos_inline = True
            videos_transcoded = True

    evidence = []
    for sid in sorted(scenarios_meta):
        smeta = scenarios_meta[sid]
        cards = []
        for key, cell in sorted(manifest.data["cells"].items()):
            if cell["scenario_id"] != sid:
                continue
            mid = cell["model_id"]
            out_dir = run_dir / "outputs" / cell["modality"] / sid
            path = _find_existing_output(out_dir, mid)
            srow = score_by_cell.get((sid, mid))
            jrow = judge_by_cell.get((sid, mid))
            crow = checks_by_cell.get((sid, mid))
            trow = ok_rows.get((sid, mid))
            cards.append({
                "gen_cost_micro": (trow or {}).get("cost", {}).get("micro_usd"),
                "cost_estimated": (trow or {}).get("cost", {}).get("usage_source") == "estimated",
                "latency_ms": (trow or {}).get("latency_ms"),
                "model_id": mid, "state": cell["state"],
                "reason": cell.get("reason", ""),
                # the same plain-English explanation the journeys table uses:
                # a raw provider payload in an evidence card tells a reader
                # nothing and looks like a crash
                "why": (_explain_failure(cell.get("reason", ""))
                        if cell["state"] in ("failed", "invalid", "unjudged")
                        else None),
                "thumb": _thumb_data_uri(path) if path else None,
                "mini": _mini_data_uri(path) if path else None,
                "video": (_video_src(preview_of.get(path, path), run_dir,
                                     videos_inline)
                          if path and path.suffix.lower() == ".mp4" else None),
                "artifact": str(path.relative_to(run_dir)) if path else None,
                "score": (srow or {}).get("score"),
                "status": (srow or {}).get("status", cell["state"]),
                "criteria": (srow or {}).get("criteria"),
                "weights": (srow or {}).get("weights"),
                "unmeasured": (srow or {}).get("unmeasured"),
                "overall_note": (jrow or {}).get("overall_note"),
                "blind_label": (jrow or {}).get("blind_label"),
                "gates": (crow or {}).get("gates"),
                "measures": (crow or {}).get("measures"),
            })
        # frozen input assets (edit tasks): the proof of what every model received
        sources = []
        for row in manifest.data.get("inputs", {}).get(sid, []):
            spath = run_dir / row["path"]
            is_clip = spath.suffix.lower() == ".mp4"
            sources.append({
                "role": row["role"], "sha256": row["sha256"],
                "path": row["path"],
                # PIL cannot open an mp4, so an edit's source silently rendered
                # as nothing at all until 2026-09-11 — the one clip a reader
                # most needs in order to judge "was only the requested thing
                # changed". Clips now get a real <video> like the outputs do.
                "video": (_video_src(preview_of.get(spath, spath), run_dir,
                                     videos_inline)
                          if is_clip and spath.exists() else None),
                "thumb": (None if is_clip
                          else (_thumb_data_uri(spath) if spath.exists() else None)),
                "mini": (None if is_clip
                         else (_mini_data_uri(spath) if spath.exists() else None))})

        # a hidden industry is a display choice, not a data change: the
        # scenario stays, filed under its next industry from the sheet's
        # "also" column (runs and industry_map.yaml are untouched)
        hidden = set(hide_industries)
        ind = industry_map.get(sid, {})
        primary = ind.get("primary", "")
        also = [x for x in (ind.get("also") or []) if x not in hidden]
        if primary in hidden:
            primary = also.pop(0) if also else ""
        scored_cards = [c for c in cards if c["score"] is not None]
        winner = margin = None
        if len(scored_cards) >= 2:
            top = sorted(scored_cards, key=lambda c: -c["score"])
            margin = round(top[0]["score"] - top[1]["score"], 2)
            if margin > TIE_BAND:                 # same tie band as the verdict
                winner = top[0]["model_id"]
        # Gemini-first everywhere the cards are shown: media figures, the
        # diagnostic columns, the summary score run, the mini thumbs
        cards.sort(key=lambda c: _rank.get(c["model_id"], len(_rank)))
        # sort keys for the client-side "Sort by" control
        g_card = next((c for c in cards if vendors.get(c["model_id"]) == "Google"),
                      cards[0] if cards else None)
        c_card = next((c for c in cards if c is not g_card), None)
        g_score = (g_card or {}).get("score")
        c_score = (c_card or {}).get("score")
        evidence.append({"id": sid, "title": smeta.get("title", ""),
                         "prompt": smeta.get("prompt", ""),
                         "expected": smeta.get("expected", ""),
                         "task": smeta.get("task", ""),
                         "family": (smeta.get("tags") or ["-"])[0],
                         "industry": primary,
                         "industry_also": also,
                         "winner": winner, "margin": margin,
                         "g_score": g_score, "c_score": c_score,
                         "gap": (round(g_score - c_score, 2)
                                 if g_score is not None and c_score is not None
                                 else None),
                         "sources": sources, "cards": cards})

    # per-family rollup (a scenario's first tag names its use-case family)
    families: dict = {}
    for e in evidence:
        fam = families.setdefault(e["family"], {"n": 0, "models": {}})
        fam["n"] += 1
        for c in e["cards"]:
            if c["score"] is not None:
                m = fam["models"].setdefault(c["model_id"],
                                             {"scores": [], "wins": 0})
                m["scores"].append(c["score"])
                if e["winner"] == c["model_id"]:
                    m["wins"] += 1
    for fam in families.values():
        for m in fam["models"].values():
            m["mean"] = round(sum(m["scores"]) / len(m["scores"]), 2)
            m["n"] = len(m["scores"])
    _fam_ids = {mid for fam in families.values() for mid in fam["models"]}
    family_models = [mid for mid in model_order if mid in _fam_ids]

    # per-industry rollup (primary industry from the sheet's mapping)
    industries: dict = {}
    for e in evidence:
        if not e["industry"]:
            continue
        ind = industries.setdefault(e["industry"], {"n": 0, "models": {}})
        ind["n"] += 1
        for c in e["cards"]:
            if c["score"] is not None:
                m = ind["models"].setdefault(c["model_id"], {"scores": [], "wins": 0})
                m["scores"].append(c["score"])
                if e["winner"] == c["model_id"]:
                    m["wins"] += 1
    for ind in industries.values():
        for m in ind["models"].values():
            m["mean"] = round(sum(m["scores"]) / len(m["scores"]), 2)
            m["n"] = len(m["scores"])

    # head-to-head duel strip (exactly two scored models). Slot a is the
    # Gemini arm, so the reader always finds it on the same side.
    duel = None
    for task, t in agg["tasks"].items():
        ms = [(mid, t["models"][mid]) for mid in model_order
              if mid in t["models"] and t["models"][mid]["mean"] is not None]
        if len(ms) != 2:
            continue
        (aid, a), (bid, b) = ms

        def metric(key, label, av, bv, fmt, better, client_label=None):
            hi = max(av, bv) or 1
            win = None
            if abs(av - bv) > 1e-9:
                win = ("a" if av > bv else "b") if better == "higher" \
                    else ("a" if av < bv else "b")
            return {"key": key, "label": label,
                    "client_label": client_label or label,
                    "internal_only": key in _INTERNAL_ROWS,
                    "a": fmt.format(av), "b": fmt.format(bv),
                    "a_pct": f"{av * 10:.1f}%", "b_pct": f"{bv * 10:.1f}%",
                    "aw": round(av / hi * 100, 1), "bw": round(bv / hi * 100, 1),
                    "win": win}

        duel = {"task": task, "a": aid, "b": bid, "metrics": [
            metric("mean", "Quality — mean", a["mean"], b["mean"], "{:.2f}",
                   "higher", client_label="Quality — mean rating"),
            metric("worst", "Reliability (worst scenario rating)", a["worst"] or 0, b["worst"] or 0,
                   "{:.1f}", "higher"),
            metric("gen_cost", "Cost per clip", a["gen_cost_per_scenario_usd"],
                   b["gen_cost_per_scenario_usd"], "${:.3f}", "lower"),
            metric("lat_p50", "Latency p50", (a["latency_p50_ms"] or 0) / 1000,
                   (b["latency_p50_ms"] or 0) / 1000, "{:.1f}s", "lower"),
        ]}
        break

    # footnotes
    params_unsupported = sorted({
        f"{r['model_id']}: {p}" for r in telemetry
        for p in (r.get("params_unsupported") or [])})
    estimates = sorted({
        f"{r['model_id']} ({r['cost'].get('basis')})" for r in telemetry
        if r.get("cost", {}).get("usage_source") == "estimated"})
    judge_meta = next((r.get("judge") for r in judge_rows if r.get("judge")), None)
    judge_version = next((r.get("judge_provider_version") for r in judge_rows
                          if r.get("judge_provider_version")), None)
    voice_maps = {m["id"]: m.get("voice_map") for m in manifest.data.get("models", [])
                  if m.get("voice_map")}

    _family = {"Google": "Google — Gemini/DeepMind family",
               "OpenAI": "OpenAI — maker of GPT/ChatGPT",
               "ByteDance": "ByteDance — the Seedance family, via BytePlus ModelArk"}
    vendor_lines = [
        f"{names.get(m['id'], m['id'])} = {m['provider_model']} "
        f"({_family.get(vendors[m['id']], vendors[m['id']])}; "
        f"{'Vertex AI, ADC' if 'vertex' in (m.get('provider') or '') else 'API-key route'})"
        for m in manifest.data.get("models", [])]
    if judge_meta and str(judge_meta.get("provider_model", "")).startswith("gemini"):
        vendor_lines.append(
            f"judge {judge_meta['provider_model']} (Google — Gemini family)")

    totals = {
        "gen_micro": sum(r.get("cost", {}).get("micro_usd", 0) for r in telemetry),
        "judge_micro": sum(r.get("cost", {}).get("micro_usd", 0) for r in judge_rows),
    }

    from .summary import completion_counts
    return dict(
        completion=completion_counts(manifest.data),
        manifest=manifest.data, agg=agg, evidence=evidence, totals=totals,
        families=families, family_models=family_models, model_order=model_order,
        industries=industries, hidden_industries=sorted(hide_industries),
        names=names, duel=duel,
        vendors=vendors, vendor_lines=vendor_lines,
        has_videos=bool(video_paths), videos_inline=videos_inline,
        videos_transcoded=videos_transcoded,
        params_unsupported=params_unsupported, estimates=estimates,
        judge_meta=judge_meta, judge_version=judge_version, voice_maps=voice_maps)
