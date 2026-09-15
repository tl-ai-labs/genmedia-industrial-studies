"""Aggregate one run into a single static HTML file (plan §16).

Images inlined as data URIs so the file opens from the run folder with no
server and can be mailed to anyone. Quality, cost, latency and reliability
are four separate columns — never one blended number.

Presentation — layout, sections, styling, number formats, file names — is the
shared report kit (shared/report_kit). This module only gathers the video
lane's data into the kit's context; video markup lives in
templates/lane_hooks.j2.
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import webbrowser
from pathlib import Path

from . import _report_kit as kit
from .generate import Manifest, _find_existing_output
from .scoring import TIE_BAND, aggregate, is_tie, pairwise_verdict
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


LANE = kit.LaneProfile(key="video", unit="clip", media="video",
                       templates=Path(__file__).parent / "templates")


def _env(names: dict | None = None, client: bool = False):
    """The kit's Jinja environment for this lane (kept for callers and tests)."""
    return kit.make_env(LANE, names, client=client)


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
    """Writes report.html (internal) and report-client.html from ONE context.
    Returns the internal path."""
    run_dir = Path(run_dir)
    ctx = _build_context(project_root, run_dir, hide_industries=hide_industries,
                         self_contained=self_contained,
                         complete_only=complete_only,
                         preview_crf=preview_crf)
    out = kit.render_run(LANE, ctx, run_dir)
    Manifest(run_dir).set_run_state("reported")
    if open_browser:
        webbrowser.open(out.as_uri())
    return out


def build_combined_report(project_root: Path, run_dirs: list, out_path: Path,
                          open_browser: bool = False,
                          hide_industries: tuple = (),
                          brief: bool = False) -> Path:
    """One study across several runs — each run a tab (or, brief, stacked
    strips over one mixed scenario list). Writes <out>.html and
    <out>-client.html. Cross-tab numbers are NOT merged."""
    ctxs = [_build_context(project_root, Path(d), hide_industries=hide_industries)
            for d in run_dirs]
    out = kit.render_study(LANE, ctxs, Path(out_path), brief=brief)
    if open_browser:
        webbrowser.open(out.resolve().as_uri())
    return out


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
        for task, t in agg.get("tasks", {}).items():
            for m in t["models"].values():
                m["mean"] = m.get("mean_complete")
                m["worst"] = m.get("worst_complete")
                m["judged_n"] = m.get("complete_n", 0)
                m["below_5"] = sum(1 for v in m.get("numeric_complete", []) if v < 5)
            # The verdict has to be re-taken on the same means the table now
            # shows. aggregate() decided it on each arm's OWN scenarios: on the
            # 2026-09-11 edits that set Omni's EDIT-01/03/05/07 against
            # Seedance's EDIT-03/05/06/07 and printed a 13.5 pp gap under a
            # table reading 96.7% vs 94.0% — a 2.7 pp tie on the same three.
            mids = sorted(t["models"])
            t["pairs"] = [pairwise_verdict(task, a, b, t["models"], t["scenarios"])
                          for i, a in enumerate(mids) for b in mids[i + 1:]]
            for p in t["pairs"]:
                p["means_over_compared"] = True
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
    vendors = {m["id"]: kit.vendor_of(m.get("provider", ""))
               for m in manifest.data.get("models", [])}
    # one presentation order, used by every table, tile, card and duel slot.
    # Any model that scored but is missing from the manifest still gets a
    # column — a column must never vanish because of an ordering list.
    _all_ids = {m["id"] for m in manifest.data.get("models", [])}
    _all_ids |= {mid for t in agg["tasks"].values() for mid in t["models"]}
    model_order = kit.gemini_first(_all_ids, vendors)
    _rank = {mid: i for i, mid in enumerate(model_order)}

    # per-model W-T-L rollup within each task, plus the transposed metric rows
    for task, t in agg["tasks"].items():
        kit.wtl_rollup(t)
        t["metric_rows"] = kit.metric_rows(t["models"], model_order, LANE.unit)

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
        winner, margin = kit.scenario_result(cards, is_tie)   # same rule as the verdict
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

    # with complete_only, the rollups average only compared scenarios too —
    # otherwise they reprint the unequal-set means the task table fixed
    families = kit.rollup(evidence, "family", complete_only=complete_only)
    _fam_ids = {mid for fam in families.values() for mid in fam["models"]}
    family_models = [mid for mid in model_order if mid in _fam_ids]
    industries = kit.rollup(evidence, "industry", complete_only=complete_only)
    duel = kit.build_duel(agg["tasks"], model_order, LANE.unit)

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

    vendor_lines = kit.vendor_lines(manifest.data.get("models", []), names, vendors,
                                    judge_meta)

    totals = {
        "gen_micro": sum(r.get("cost", {}).get("micro_usd", 0) for r in telemetry),
        "judge_micro": sum(r.get("cost", {}).get("micro_usd", 0) for r in judge_rows),
    }

    from .summary import completion_counts
    return dict(
        tally=kit.tally(evidence, model_order),
        completion=completion_counts(manifest.data),
        manifest=manifest.data, agg=agg, evidence=evidence, totals=totals,
        families=families, family_models=family_models, model_order=model_order,
        industries=industries, hidden_industries=sorted(hide_industries),
        names=names, duel=duel,
        vendors=vendors, vendor_lines=vendor_lines,
        has_videos=bool(video_paths), videos_inline=videos_inline,
        videos_transcoded=videos_transcoded,
        params_unsupported=params_unsupported, estimates=estimates,
        judge_meta=judge_meta, judge_version=judge_version)
