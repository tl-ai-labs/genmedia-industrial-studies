"""Turn the November list (100 targeted image scenarios) into runner scenarios.

Ravi's ask after the Gaurav call (2026-11-02): 100 more image scenarios in the
industries where Gemini has a real chance, on the October models and judge. The
content is scenarios/bank-nov/_source/final-100.json: 16 bank scenarios and 84
new ones, with every review fix already in. The conversion follows
import_oct_plan.py (same sizes, rubric names, judge-read text, checklist shape);
only the source of the content differs.

Writes scenarios/bank-nov/<id>.yaml, the run list scenarios/tiers/nov-100.txt,
assets/bank-nov/inputs.json (scenario -> input files) and
assets/bank-nov/source-jobs.json (the input images to make, with their prompts).
It makes no images: the YAML names assets/bank-nov/inputs/<key>.jpg, which
tools/make_nov_sources.py --export writes once the jobs have run and been reviewed.

Usage (from image/):
    python tools/import_nov_list.py --site-v2-dir <site repo .../scenario-bank/v2/image>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from import_oct_plan import DIMS, SIZES, params_for, ratio_in, weights_from

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scenarios" / "bank-nov"
ASSETS = ROOT / "assets" / "bank-nov"
OCT_SOURCES = ROOT / "assets" / "bank-oct" / "sources"

def source_key(prompt: str, aspect: str) -> str:
    return "src-" + hashlib.sha256(f"{aspect}\n{prompt}".encode()).hexdigest()[:12]


def plan_inputs(x: dict, jobs: dict, site_v2_dir: Path) -> list[dict]:
    """The input files of one edit, adding each image still to make to jobs."""
    sid = x["id"]
    uses = []
    if x.get("bank") == "v2":
        aspect = x.get("format") or "16:9"
        # the reviewed prompt; a thumbnail that disagrees with the edit is not used
        prompt = x["source_prompt"]
        guide = site_v2_dir / f"{sid}.png" if x.get("source_guide") else None
        if guide and not guide.exists():
            raise SystemExit(f"{sid}: no site thumbnail at {guide}")
        key = source_key(prompt + ("|guide" if guide else ""), aspect)
        job = {"key": key, "prompt": prompt, "aspect": aspect, "scenarios": []}
        if guide:
            job["guide"] = str(guide)
        jobs.setdefault(key, job)
        jobs[key]["scenarios"].append(sid)
        return [{"role": "Image to edit", "order": 0, "file": f"sources/{key}.png"}]
    for i, src in enumerate(x.get("sources") or []):
        key = source_key(src["prompt"], src["aspect"])
        job = jobs.setdefault(key, {"key": key, "prompt": src["prompt"],
                                    "aspect": src["aspect"], "scenarios": []})
        if (OCT_SOURCES / f"{key}.png").exists():
            job["reuse"] = f"assets/bank-oct/sources/{key}.png"
        job["scenarios"].append(sid)
        uses.append({"role": src["role"], "note": src.get("note"), "order": i,
                     "file": f"sources/{key}.png"})
    return uses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-v2-dir", required=True)
    args = ap.parse_args()

    rows = json.loads((OUT / "_source" / "final-100.json").read_text())
    checkmap = json.loads((OUT / "_source" / "checkmap.json").read_text())
    jobs: dict[str, dict] = {}
    all_inputs: dict[str, list[dict]] = {}
    listed = []
    for x in rows:
        sid = x["id"]
        ev = x["evaluation"]
        bank = x.get("bank") or "new"
        task = "image_edit" if "Edit" in x["taskType"] else "text_to_image"
        deliverable = x.get("deliverable") or (f"Format {x['format']}" if x.get("format") else "")
        required = x.get("requiredText") or None
        text_dim = "text_accuracy_judged"
        weights = weights_from(ev["rubric"], text_dim)
        gates = sorted({(text_dim if r["name"] == "Text accuracy" else DIMS[r["name"]])
                        for r in ev["rubric"] if r.get("gate")})

        # checklist: set-level questions, then each member's, in list order
        cmap = {int(k): v for k, v in (checkmap.get(sid) or {}).items()}
        scope = "whole set" if x.get("set") else "image"
        qs = [(q, scope) for q in ev["checklist"]]
        for k, m in enumerate((x.get("set") or {}).get("members", []), start=1):
            qs += [(q, f"frame {k} ({m['name']})") for q in m.get("checks", [])]
        if bank != "v2" and len(cmap) != len(qs):
            raise SystemExit(f"{sid}: checkmap covers {len(cmap)} of {len(qs)} checks")
        checklist = []
        for i, (q, sc) in enumerate(qs):
            dim = cmap.get(i)
            name = (text_dim if dim == "Text accuracy" else DIMS[dim]) if dim else None
            checklist.append({"q": q, "dim": name, "scope": sc})

        m = re.search(r"score is (\d+(?:\.\d+)?) or more", ev.get("pass") or "")
        src_aspect = None
        if task == "image_edit":
            src_aspect = x.get("format") if bank == "v2" else next(
                (s.get("aspect") for s in x.get("sources") or []
                 if s.get("role") == "Image to edit"), None)
        params = params_for(deliverable, x["prompt"], task, src_aspect,
                            "v2 bank" if bank == "v2" else "v3 bank")

        out_set = None
        outputs = 1
        if x.get("set"):
            members = []
            for mem in x["set"].get("members", []):
                mm = {"name": mem["name"], "brief": mem["brief"], "checks": mem.get("checks", [])}
                r = ratio_in(mem["brief"])
                if r:
                    mm["params"] = {"aspect_ratio": r, "size": SIZES[r]}
                members.append(mm)
            outputs = len(members)
            out_set = {"mode": "each", "keep": x["set"].get("keep") or [], "members": members}

        scen_inputs: dict = {}
        if task == "image_edit":
            uses = plan_inputs(x, jobs, Path(args.site_v2_dir))
            all_inputs[sid] = uses
            refs = 0
            for u in sorted(uses, key=lambda u: u["order"]):
                if u["role"] == "Image to edit" and "source" not in scen_inputs:
                    role = "source"
                else:
                    refs += 1
                    role = "reference" if refs == 1 else f"reference{refs}"
                scen_inputs[role] = f"assets/bank-nov/inputs/{Path(u['file']).stem}.jpg"

        expected = x.get("expected") or f"{x['name']}. Deliverable: {deliverable}."
        if x.get("changes"):
            expected += f"\nMust change: {x['changes']}"
        if x.get("preserve"):
            expected += f"\nMust keep: {x['preserve']}"

        meta = {"bank": bank, "industry": x["industry"], "use_case": x.get("useCase"),
                "customers": x.get("customers") or [], "difficulty": x.get("difficulty"),
                "kind": x.get("useCase"), "edge": "targeted", "theme": x.get("theme") or [],
                "outputs": outputs, "persona": x.get("persona")}
        if x.get("changed_from_bank"):
            meta["changed_from_bank"] = True
        tags = [f"bank:{bank}", f"industry:{meta['industry']}", f"use_case:{meta['use_case']}",
                f"kind:{meta['kind']}", f"difficulty:{meta['difficulty']}", "edge:targeted"]
        tags += [f"customer:{c}" for c in meta["customers"]]

        doc = {"id": sid, "modality": "image", "task": task, "title": x["name"]}
        if scen_inputs:
            doc["inputs"] = scen_inputs
        doc.update({"params": params, "prompt": x["prompt"], "expected": expected,
                    "deliverable": deliverable or None,
                    "checks": {"min_width": 512, "min_height": 512,
                               "formats": ["png", "jpeg", "webp"], "not_blank": True},
                    "criteria": list(weights), "weights": weights,
                    "checklist": checklist, "gate_criteria": gates,
                    "pass_min_score": float(m.group(1)) if m else None,
                    "pass_rule": ev.get("pass"), "required_text": required})
        if out_set:
            doc["output_set"] = out_set
        doc.update({"meta": meta, "tags": tags})
        doc = {k: v for k, v in doc.items() if v is not None}

        header = (f"# {sid} — targeted November list, scenarios/bank-nov/_source/final-100.json.\n"
                  "# Generated by tools/import_nov_list.py — edit the list, not this file.\n")
        (OUT / f"{sid}.yaml").write_text(header + yaml.safe_dump(
            doc, sort_keys=False, allow_unicode=True, width=100))
        listed.append(sid)

    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "inputs.json").write_text(json.dumps(all_inputs, indent=1, ensure_ascii=False))
    (ASSETS / "source-jobs.json").write_text(
        json.dumps(list(jobs.values()), indent=1, ensure_ascii=False))
    tiers = ROOT / "scenarios" / "tiers" / "nov-100.txt"
    tiers.write_text("# November run: 100 targeted scenarios, Nano Banana 2.1 vs GPT Image 2.5 "
                     f"Sunburst (high). {len(listed)} scenarios.\n# Report separately from the "
                     "October 200. Usage: python -m runner.cli run --modality image --scenarios "
                     "scenarios/tiers/nov-100.txt --models "
                     "configs/models-nb21-vs-gpt25-judge31pro.yaml --budget <approved>\n"
                     + "".join(f"../bank-nov/{s}.yaml\n" for s in listed))
    to_make = sum(1 for j in jobs.values() if "reuse" not in j)
    print(f"wrote {len(listed)} scenarios to {OUT.relative_to(ROOT)} and "
          f"{tiers.relative_to(ROOT)}; {len(jobs)} input images, {to_make} to make")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
