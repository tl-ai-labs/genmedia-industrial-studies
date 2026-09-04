"""Aggregate one run into a single static HTML file (plan §16).

Images inlined as data URIs so the file opens from the run folder with no
server and can be mailed to anyone. Quality, cost, latency and reliability
are four separate columns — never one blended number.
"""
from __future__ import annotations

import base64
import io
import json
import webbrowser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .generate import Manifest, _find_existing_output
from .scoring import aggregate
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


def _env(names: dict | None = None) -> Environment:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["usd"] = lambda micro: f"${micro / 1e6:,.4f}"
    env.filters["s"] = lambda ms: f"{ms / 1000:.1f}s" if ms is not None else "—"
    nm = names or {}
    env.filters["disp"] = lambda mid: nm.get(mid, mid)
    return env


def build_report(project_root: Path, run_dir: Path, open_browser: bool = False,
                 hide_industries: tuple = ()) -> Path:
    run_dir = Path(run_dir)
    ctx = _build_context(project_root, run_dir, hide_industries=hide_industries)
    html = _env(ctx["names"]).get_template("report.html.j2").render(**ctx)
    out = run_dir / "report.html"
    out.write_text(html)
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
    for c in ctxs:
        names.update(c["names"])
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
                    "models": [names.get(m, m) for m in sorted(t["models"])]})
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
        merged = {
            "evidence": merged_evidence, "industries": industries,
            "families": families,
            "family_models": sorted(groups),
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
                   hide_industries: tuple = ()) -> dict:
    run_dir = Path(run_dir)
    manifest = Manifest(run_dir)
    files = RunFiles(run_dir)
    agg = aggregate(run_dir)
    telemetry = files.read("telemetry")
    judge_rows = files.read("judge")
    scores = files.read("scores")
    checks = files.read("checks")

    score_by_cell = {(r["scenario_id"], r["model_id"]): r for r in scores}
    checks_by_cell = {(r["scenario_id"], r["model_id"]): r for r in checks}
    judge_by_cell = {(r["scenario_id"], r["model_id"]): r for r in judge_rows
                     if r.get("status") == "judged"}

    # per-model W-T-L rollup within each task
    for task, t in agg["tasks"].items():
        for mid, m in t["models"].items():
            w = l = ti = 0
            for p in t["pairs"]:
                if p["a"] == mid:
                    w, l, ti = w + p["wins_a"], l + p["wins_b"], ti + p["ties"]
                elif p["b"] == mid:
                    w, l, ti = w + p["wins_b"], l + p["wins_a"], ti + p["ties"]
            m["wtl"] = f"{w}-{ti}-{l}"

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
    total_video_bytes = sum(p.stat().st_size for p in video_paths)
    videos_inline = 0 < total_video_bytes <= VIDEO_INLINE_TOTAL_MAX

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
                "video": (_video_src(path, run_dir, videos_inline)
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
        scored_cards = [c for c in cards if c["score"] is not None]
        winner = margin = None
        if len(scored_cards) >= 2:
            top = sorted(scored_cards, key=lambda c: -c["score"])
            margin = round(top[0]["score"] - top[1]["score"], 2)
            if margin > 0.5:                      # same tie band as the verdict
                winner = top[0]["model_id"]
        evidence.append({"id": sid, "title": smeta.get("title", ""),
                         "prompt": smeta.get("prompt", ""),
                         "expected": smeta.get("expected", ""),
                         "task": smeta.get("task", ""),
                         "family": (smeta.get("tags") or ["-"])[0],
                         "industry": primary,
                         "industry_also": also,
                         "winner": winner, "margin": margin,
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
    family_models = sorted({mid for fam in families.values() for mid in fam["models"]})

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

    # head-to-head duel strip (exactly two scored models)
    duel = None
    for task, t in agg["tasks"].items():
        ms = [(mid, m) for mid, m in sorted(t["models"].items())
              if m["mean"] is not None]
        if len(ms) != 2:
            continue
        (aid, a), (bid, b) = ms

        def metric(label, av, bv, fmt, better):
            hi = max(av, bv) or 1
            win = None
            if abs(av - bv) > 1e-9:
                win = ("a" if av > bv else "b") if better == "higher" \
                    else ("a" if av < bv else "b")
            return {"label": label, "a": fmt.format(av), "b": fmt.format(bv),
                    "aw": round(av / hi * 100, 1), "bw": round(bv / hi * 100, 1),
                    "win": win}

        duel = {"task": task, "a": aid, "b": bid, "metrics": [
            metric("Quality — mean", a["mean"], b["mean"], "{:.2f}", "higher"),
            metric("Worst scenario", a["worst"] or 0, b["worst"] or 0,
                   "{:.1f}", "higher"),
            metric("Cost per scenario", a["gen_cost_per_scenario_usd"],
                   b["gen_cost_per_scenario_usd"], "${:.3f}", "lower"),
            metric("Latency p50", (a["latency_p50_ms"] or 0) / 1000,
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

    # vendor attribution — say plainly which arm is the Google/Gemini side
    # and which is OpenAI, in the tiles, the duel strip and the footnotes
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
        families=families, family_models=family_models,
        industries=industries, hidden_industries=sorted(hide_industries),
        names=names, duel=duel,
        vendors=vendors, vendor_lines=vendor_lines,
        has_videos=bool(video_paths), videos_inline=videos_inline,
        params_unsupported=params_unsupported, estimates=estimates,
        judge_meta=judge_meta, judge_version=judge_version, voice_maps=voice_maps)
