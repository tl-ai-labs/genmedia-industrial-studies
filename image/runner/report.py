"""Aggregate one run into a single static HTML file (plan §16).

Images inlined as data URIs so the file opens from the run folder with no
server and can be mailed to anyone. Quality, cost, latency and reliability
are four separate columns — never one blended number.

Presentation — layout, sections, styling, number formats, file names — is the
shared report kit (shared/report_kit). This module only gathers the image
lane's data into the kit's context; image markup lives in
templates/lane_hooks.j2.
"""
from __future__ import annotations

import base64
import io
import webbrowser
from pathlib import Path

from . import _report_kit as kit
from .generate import Manifest, _find_existing_output
from .scoring import TIE_BAND, aggregate
from .telemetry import RunFiles

THUMB_MAX_PX = 800

LANE = kit.LaneProfile(key="image", unit="image", media="image",
                       templates=Path(__file__).parent / "templates")


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


def _env(names: dict | None = None, client: bool = False):
    """The kit's Jinja environment for this lane (kept for callers and tests)."""
    return kit.make_env(LANE, names, client=client)


def _is_tie(diff: float) -> bool:
    """Same rule as the verdict: compared at two decimals, only identical ties."""
    return abs(round(diff, 2)) <= TIE_BAND


def build_report(project_root: Path, run_dir: Path, open_browser: bool = False,
                 hide_industries: tuple = (), complete_only: bool = False) -> Path:
    """Writes report.html (internal) and report-client.html from ONE context.
    Returns the internal path."""
    run_dir = Path(run_dir)
    ctx = _build_context(project_root, run_dir, hide_industries=hide_industries,
                         complete_only=complete_only)
    out = kit.render_run(LANE, ctx, run_dir)
    Manifest(run_dir).set_run_state("reported")
    if open_browser:
        webbrowser.open(out.as_uri())
    return out


def build_combined_report(project_root: Path, run_dirs: list, out_path: Path,
                          open_browser: bool = False,
                          hide_industries: tuple = (),
                          brief: bool = False,
                          complete_only: bool = False) -> Path:
    """One study across several runs — each run a tab (or, brief, stacked
    strips over one mixed scenario list). Writes <out>.html and
    <out>-client.html. Cross-tab numbers are NOT merged.

    complete_only applies per tab, exactly as it does for a single run: a tab
    whose arms completed different scenario sets would otherwise print two
    means that do not measure the same thing.
    """
    ctxs = [_build_context(project_root, Path(d), hide_industries=hide_industries,
                           complete_only=complete_only)
            for d in run_dirs]
    out = kit.render_study(LANE, ctxs, Path(out_path), brief=brief)
    if open_browser:
        webbrowser.open(out.resolve().as_uri())
    return out


def _build_context(project_root: Path, run_dir: Path,
                   hide_industries: tuple = (),
                   complete_only: bool = False) -> dict:
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
                "thumb": _thumb_data_uri(path) if path else None,
                "mini": _mini_data_uri(path) if path else None,
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
            sources.append({"role": row["role"], "sha256": row["sha256"],
                            "path": row["path"],
                            "thumb": _thumb_data_uri(spath) if spath.exists() else None,
                            "mini": _mini_data_uri(spath) if spath.exists() else None})

        # a hidden industry is a display choice, not a data change: the
        # scenario stays, filed under its next industry from the sheet's
        # "also" column (runs and industry_map.yaml are untouched)
        hidden = set(hide_industries)
        ind = industry_map.get(sid, {})
        primary = ind.get("primary", "")
        also = [x for x in (ind.get("also") or []) if x not in hidden]
        if primary in hidden:
            primary = also.pop(0) if also else ""
        winner, margin = kit.scenario_result(cards, _is_tie)
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

    families = kit.rollup(evidence, "family")
    _fam_ids = {mid for fam in families.values() for mid in fam["models"]}
    family_models = [mid for mid in model_order if mid in _fam_ids]
    industries = kit.rollup(evidence, "industry")
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
        completion=completion_counts(manifest.data),
        manifest=manifest.data, agg=agg, evidence=evidence, totals=totals,
        families=families, family_models=family_models, model_order=model_order,
        industries=industries, hidden_industries=sorted(hide_industries),
        names=names, duel=duel, tally=kit.tally(evidence, model_order),
        vendors=vendors, vendor_lines=vendor_lines,
        params_unsupported=params_unsupported, estimates=estimates,
        judge_meta=judge_meta, judge_version=judge_version)
