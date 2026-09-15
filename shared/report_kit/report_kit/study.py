"""Studies — several runs on one page (kit/combined.html.j2).

A lane builds one report context per run (the same shape kit/run.html.j2
takes); this module turns a list of them into the page's overview, tab labels
and, for a brief study, one mixed scenario list.
"""
from __future__ import annotations

from pathlib import Path

from .compare import gemini_first, rollup, study_title, tab_label
from .rendering import LaneProfile, render_both, study_report_paths

_FAMILY = {"Google": "Google — Gemini/DeepMind family",
           "OpenAI": "OpenAI — maker of GPT/ChatGPT",
           "ByteDance": "ByteDance — the Seedance family, via BytePlus ModelArk",
           "ElevenLabs": "ElevenLabs"}


def vendor_lines(models: list, names: dict, vendors: dict,
                 judge_meta: dict | None = None) -> list:
    """The "Models:" footnote — which provider model each display name is,
    whose family it belongs to, and how it was reached."""
    lines = [
        f"{names.get(m['id'], m['id'])} = {m.get('provider_model', m['id'])} "
        f"({_FAMILY.get(vendors.get(m['id']), vendors.get(m['id'], '?'))}; "
        f"{'Vertex AI, ADC' if 'vertex' in (m.get('provider') or '') else 'API-key route'})"
        for m in models]
    if judge_meta and str(judge_meta.get("provider_model", "")).startswith("gemini"):
        lines.append(f"judge {judge_meta['provider_model']} (Google — Gemini family)")
    return lines


def build_study(ctxs: list, brief: bool = False) -> dict:
    """Context for kit/combined.html.j2 from per-run report contexts."""
    names: dict = {}
    vendors: dict = {}
    for c in ctxs:
        names.update(c["names"])
        vendors.update(c.get("vendors") or {})
    all_ids: list = []
    for c in ctxs:
        mids = []
        for t in c["agg"]["tasks"].values():
            mids = gemini_first(t["models"], vendors)
            break
        c["tab_label"] = tab_label(mids, names)
        all_ids += [m for m in mids if m not in all_ids]

    overview = {
        "title": study_title(gemini_first(all_ids, vendors), names),
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
                    "completed": (c.get("completion") or {}).get("completed", 0),
                    "verdict": (names.get(p["winner"], p["winner"]) + " wins")
                               if p["winner"] else "tie",
                    "detail": p.get("door") or "decided on cost / latency facts",
                    "models": [names.get(m, m) for m in gemini_first(t["models"], vendors)]})

    merged = _brief_merge(ctxs, names, vendors) if brief else None
    return {"runs": ctxs, "overview": overview, "brief": brief, "merged": merged,
            "names": names}


def _brief_merge(ctxs: list, names: dict, vendors: dict) -> dict:
    """One mixed scenario list and one industry table across runs. Arms that
    differ only by tier are grouped under one label (the parenthetical is
    dropped), and the grouping is stated on the page."""
    def base_label(mid: str) -> str:
        return names.get(mid, mid).split(" (")[0]

    evidence = []
    groups: dict = {}
    label_vendor: dict = {}
    for c in ctxs:
        for e in c["evidence"]:
            e = dict(e)
            if e["winner"]:
                e["winner"] = base_label(e["winner"])
            evidence.append(e)
            for card in e["cards"]:
                lbl = base_label(card["model_id"])
                groups.setdefault(lbl, set()).add(names.get(card["model_id"], card["model_id"]))
                label_vendor.setdefault(lbl, vendors.get(card["model_id"]))
    evidence.sort(key=lambda e: e["id"])
    mixed = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    order = sorted(groups, key=lambda l: (0 if label_vendor.get(l) == "Google" else 1, l))
    return {
        "evidence": evidence,
        "industries": rollup(evidence, "industry", label_of=base_label),
        "families": rollup(evidence, "family", label_of=base_label),
        "family_models": order, "model_order": order,
        "hidden_industries": ctxs[0].get("hidden_industries", []),
        "merged_note": ("Grouped columns: " + "; ".join(
            f"{k} covers {' and '.join(v)}" for k, v in mixed.items()) +
            " — per-lane tiers are on each scenario card.") if mixed else "",
    }


def render_study(lane: LaneProfile, ctxs: list, out_path: Path,
                 brief: bool = False) -> Path:
    """Write <out>.html (internal) and <out>-client.html. Returns the internal path."""
    study = build_study(ctxs, brief=brief)
    internal, client = study_report_paths(out_path)
    render_both(lane, "kit/combined.html.j2", study, internal, client, names=study["names"])
    return internal
