"""Turn the October run plan (200 image scenarios) into runner scenarios.

Content comes from the scenario bank itself (site repo, scenario-bank v2/v3
image.json); the plan supplies only WHICH 200 ids run and its own labels
(Gemini edge, kind). Writes scenarios/bank-oct/<id>.yaml and the run list
scenarios/tiers/oct-200.txt.

Decisions applied here (2026-10-09, Sai):
  * size: both arms get the SAME pixels — Gemini's 1K size for the canvas
    ratio (16:9 = 1376x768 ...), which GPT Image 2.5 accepts as a custom
    size. The deliverable's own words decide first (wide -> 16:9, landscape
    -> 3:2, portrait -> 2:3); a ratio counts only when it describes the
    canvas, not a panel, frame or thumbnail inside it. Edits that keep the
    canvas get the source image's ratio (never size: auto, which let each
    arm pick its own canvas); an edit whose brief asks for a new canvas
    (extend to 16:9, recompose as 9:16) gets that.
  * rubric: the bank's categories and weights (percentages normalised to sum
    to 1). Style -> realism_style; Preservation -> preservation_judged.
  * checklist: every question carries the category it belongs to (the
    mapping in bank-oct/_source/checkmap.json, made once for both arms).
  * text: judge-read (text_accuracy_judged) against the exact required text,
    for every scenario. The bank marks 18 v2 strings for OCR, but they list
    several labels, old and new text together, or numbers whose reading
    order depends on layout; a fuzzy OCR match there scores noise (review
    2026-10-09, and the image-judging audit). People check text in the panel.
  * sets: "each output is its own request, from the same inputs".

Usage (from image/):
    python tools/import_oct_plan.py --plan <runplan-data.json> \
        --bank-v2 <v2/image.json> --bank-v3 <v3/image.json> --checkmap <checkmap.json>
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scenarios" / "bank-oct"
INPUTS = ROOT / "assets" / "bank-oct" / "inputs.json"

# Gemini's 1K output for each ratio; all are multiples of 16 within GPT Image
# 2.5's custom-size rules (ratio <= 3:1, 655,360..8,294,400 pixels).
SIZES = {"1:1": "1024x1024", "2:3": "848x1264", "3:2": "1264x848",
         "3:4": "896x1200", "4:3": "1200x896", "4:5": "928x1152",
         "5:4": "1152x928", "9:16": "768x1376", "16:9": "1376x768",
         "21:9": "1584x672", "9:21": "672x1584"}

DIMS = {"Prompt adherence": "prompt_adherence", "Visual quality": "visual_quality",
        "Composition": "composition", "Realism & style": "realism_style",
        "Style": "realism_style", "Edit fidelity": "edit_fidelity",
        "Set consistency": "set_consistency", "Reference fidelity": "reference_fidelity",
        "Physics plausibility": "physics_plausibility",
        "Preservation": "preservation_judged"}



# "six 9:16 panels", "16:9 frame lines", "a 9:16 thumbnail" describe something
# inside the canvas; "a 9:16 vertical frame" is the canvas itself.
_NOT_CANVAS = re.compile(r"^\s*(?:vertical\s+|horizontal\s+|portrait\s+|landscape\s+)?"
                         r"(?:panels?\b|frames\b|frame\s+lines|thumbnails?\b|tiles?\b"
                         r"|cells?\b|strips?\b)", re.I)

# Canvases the wording cannot settle, decided by reading the brief.
CANVAS_OVERRIDES = {
    "CHR-GAM-03": "3:2",   # a sheet of six studies "two rows of three", from a 2:3 sketch
}


def ratio_in(text: str | None) -> str | None:
    """The first supported N:M that describes a canvas — one followed by
    'panel', 'frame', 'thumbnail' ... describes something inside it."""
    for m in re.finditer(r"(?<![\d:])(\d{1,2}):(\d{1,2})(?![\d:])", text or ""):
        r = f"{m.group(1)}:{m.group(2)}"
        if r in SIZES and not _NOT_CANVAS.match(text[m.end():m.end() + 24]):
            return r
    return None


def orientation_in(text: str | None) -> str | None:
    t = (text or "").lower()
    if "portrait-leaning" in t:
        return "3:4"
    if "wide" in t:
        return "16:9"
    if "landscape" in t:
        return "3:2"
    if "portrait" in t or "vertical" in t or "tall" in t:
        return "2:3"
    if "square" in t:
        return "1:1"
    return None


def params_for(deliverable: str, prompt: str, task: str, source_aspect: str | None,
               bank: str) -> dict:
    d = deliverable or ""
    if task == "image_edit" and bank == "v2 bank":
        # v2 'format' is the SOURCE's format; a reframing brief names the new one
        # 'vertical' in a brief is usually about lines or surfaces ("vertical
        # panels", "vertical surfaces"), not the canvas: only landscape/portrait
        # count (fixed 2026-10-10 after it reshaped IMGEDIT-068, -090 and -107)
        r = ratio_in(prompt) or orientation_in(" ".join(
            w for w in prompt.lower().split() if w in ("landscape", "portrait")))
        r = r or source_aspect or ratio_in(d)
    elif task == "image_edit" and "same size" in d.lower():
        r = source_aspect
    else:
        r = orientation_in(d) or ratio_in(d) or ratio_in(prompt)
        if not r:
            r = source_aspect if task == "image_edit" else "3:2"
    r = r if r in SIZES else (source_aspect if source_aspect in SIZES else "3:2")
    return {"aspect_ratio": r, "size": SIZES[r]}


def weights_from(rubric: list[dict], text_dim: str) -> dict[str, float]:
    raw: dict[str, float] = {}
    for r in rubric:
        name = text_dim if r["name"] == "Text accuracy" else DIMS[r["name"]]
        raw[name] = raw.get(name, 0.0) + float(str(r["weight"]).rstrip("%")) / 100.0
    total = sum(raw.values())
    w = {k: round(v / total, 6) for k, v in raw.items()}
    last = list(w)[-1]
    w[last] = round(w[last] + (1.0 - sum(w.values())), 6)
    return w


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--bank-v2", required=True)
    ap.add_argument("--bank-v3", required=True)
    ap.add_argument("--checkmap", required=True)
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text())
    banks = {"v2 bank": {s["id"]: s for s in json.loads(Path(args.bank_v2).read_text())["scenarios"]},
             "v3 bank": {s["id"]: s for s in json.loads(Path(args.bank_v3).read_text())["scenarios"]}}
    checkmap = json.loads(Path(args.checkmap).read_text())
    inputs = json.loads(INPUTS.read_text())

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_source").mkdir(exist_ok=True)
    (OUT / "_source" / "checkmap.json").write_text(json.dumps(checkmap, indent=0))
    listed = []
    for row in plan["img"]:
        sid, bank = row["id"], row["src"]
        b = banks[bank][sid]
        ev = b["evaluation"]
        task = "image_edit" if row["t"] == "Edit" else "text_to_image"
        deliverable = b.get("deliverable") or (f"Format {b['format']}" if b.get("format") else "")
        required = b.get("requiredText") or None

        ocr_text = False   # every required string is judge-read (see the docstring)
        text_dim = "text_accuracy" if ocr_text else "text_accuracy_judged"
        weights = weights_from(ev["rubric"], text_dim)
        gates = sorted({(text_dim if r["name"] == "Text accuracy" else DIMS[r["name"]])
                        for r in ev["rubric"] if r.get("gate")})

        # checklist: set-level questions, then each member's, in bank order
        cmap = {int(k): v for k, v in (checkmap.get(sid) or {}).items()}
        scope = "whole set" if b.get("set") else "image"
        qs = [(q, scope) for q in ev["checklist"]]
        for k, m in enumerate((b.get("set") or {}).get("members", []), start=1):
            qs += [(q, f"frame {k} ({m['name']})") for q in m.get("checks", [])]
        checklist = []
        for i, (q, sc) in enumerate(qs):
            dim = cmap.get(i)
            name = None
            if dim:
                name = text_dim if dim == "Text accuracy" else DIMS[dim]
            checklist.append({"q": q, "dim": name, "scope": sc})
        if bank == "v3 bank" and len(cmap) != len(qs):
            raise SystemExit(f"{sid}: checkmap covers {len(cmap)} of {len(qs)} checks")

        m = re.search(r"score is (\d+(?:\.\d+)?) or more", ev.get("pass") or "")
        src_aspect = None
        if task == "image_edit":
            if bank == "v3 bank":
                src_aspect = next((s.get("aspect") for s in (b.get("sources") or [])
                                   if s.get("role") == "Image to edit"), None)
            else:
                src_aspect = b.get("format")
        params = params_for(deliverable, b["prompt"], task, src_aspect, bank)
        if sid in CANVAS_OVERRIDES:
            r = CANVAS_OVERRIDES[sid]
            params = {"aspect_ratio": r, "size": SIZES[r]}

        out_set = None
        if b.get("set"):
            mode = "each" if b["set"]["mode"].startswith("Each") else "panels"
            members = []
            for mem in b["set"].get("members", []):
                mm = {"name": mem["name"], "brief": mem["brief"],
                      "checks": mem.get("checks", [])}
                r = ratio_in(mem["brief"])
                if r and mode == "each":
                    mm["params"] = {"aspect_ratio": r, "size": SIZES[r]}
                members.append(mm)
            out_set = {"mode": mode, "keep": b["set"].get("keep") or [], "members": members}

        scen_inputs: dict = {}
        if task == "image_edit":
            refs = 0
            for u in sorted(inputs[sid], key=lambda u: u["order"]):
                if u["role"] == "Image to edit" and "source" not in scen_inputs:
                    role = "source"
                else:
                    refs += 1
                    role = "reference" if refs == 1 else f"reference{refs}"
                scen_inputs[role] = f"assets/bank-oct/{u['file']}"

        if bank == "v2 bank":
            expected = b.get("expected") or b["name"]
            if b.get("changes"):
                expected += f"\nMust change: {b['changes']}"
            if b.get("preserve"):
                expected += f"\nMust keep: {b['preserve']}"
        else:
            expected = f"{b['name']}. Deliverable: {deliverable}."

        meta = {"bank": bank.split()[0], "industry": b.get("industry"),
                "use_case": b.get("useCase"), "customers": b.get("customers") or [],
                "difficulty": b.get("difficulty"), "plan_difficulty": row.get("d"),
                "kind": row.get("kind"), "edge": row.get("e"),
                "theme": (b.get("tags") or {}).get("theme") or [],
                "levers": row.get("lv") or [], "outputs": row.get("outputs"),
                "persona": b.get("persona")}
        tags = [f"bank:{meta['bank']}", f"industry:{meta['industry']}",
                f"use_case:{meta['use_case']}", f"kind:{meta['kind']}",
                f"difficulty:{meta['difficulty']}", f"edge:{meta['edge']}"]
        tags += [f"customer:{c}" for c in meta["customers"]]

        checks = {"min_width": 512, "min_height": 512,
                  "formats": ["png", "jpeg", "webp"], "not_blank": True}
        if ocr_text:
            checks["must_read_text"] = [ln.strip() for ln in required.splitlines() if ln.strip()]

        doc = {"id": sid, "modality": "image", "task": task, "title": b["name"]}
        if scen_inputs:
            doc["inputs"] = scen_inputs
        doc.update({"params": params, "prompt": b["prompt"], "expected": expected,
                    "deliverable": deliverable or None, "checks": checks,
                    "criteria": list(weights), "weights": weights,
                    "checklist": checklist, "gate_criteria": gates,
                    "pass_min_score": float(m.group(1)) if m else None,
                    "pass_rule": ev.get("pass")})
        if required and not ocr_text:
            doc["required_text"] = required
        if out_set:
            doc["output_set"] = out_set
        doc.update({"meta": meta, "tags": tags})
        doc = {k: v for k, v in doc.items() if v is not None}

        header = (f"# {sid} — scenario bank {meta['bank']} (site repo scenario-bank/"
                  f"{meta['bank']}/image.json), October run plan 2026-10-09.\n"
                  f"# Generated by tools/import_oct_plan.py — edit the bank, not this file.\n")
        (OUT / f"{sid}.yaml").write_text(header + yaml.safe_dump(
            doc, sort_keys=False, allow_unicode=True, width=100))
        listed.append(sid)

    tiers = ROOT / "scenarios" / "tiers" / "oct-200.txt"
    tiers.write_text("# October run: Nano Banana 2.1 vs GPT Image 2.5 Sunburst (high), "
                     f"{len(listed)} scenarios.\n# Usage: python -m runner.cli run --modality "
                     "image --scenarios scenarios/tiers/oct-200.txt --models "
                     "configs/models-nb21-vs-gpt25.yaml --budget <approved>\n"
                     + "".join(f"../bank-oct/{s}.yaml\n" for s in listed))
    print(f"wrote {len(listed)} scenarios to {OUT.relative_to(ROOT)} and {tiers.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
