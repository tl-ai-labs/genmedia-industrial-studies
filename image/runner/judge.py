"""Blind AI judging (plan §11).

Practices enforced here, mechanically:
  * Blind: outputs are copied to a temp folder as A.png, B.png... in a
    per-scenario shuffled order (seed = sha256(scenario_id), reproducible),
    metadata stripped by re-encoding; the prompt never contains a model or
    provider name (asserted). The mapping lives only in judge.jsonl.
  * Absolute, not comparative: one output per judge call.
  * Structured output, reasoning first; strict schema validation.
  * Fixed everything: judge model + temperature 0 from configs/models.yaml,
    rubric hash verified against the manifest, prompt sha256 recorded.
  * Judge failure is a state, not a zero: API error -> retry x2; unparseable
    JSON -> one repair retry; then `unjudged`, raw response kept.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path

import yaml

from . import adapters as adapter_registry
from .adapters.base import SafetyRefusal
from .cost import compute_cost
from .generate import Manifest, RunRejected, _find_existing_output, backoff_s
from .loaders import Scenario, effective_criteria, load_rubric
from .telemetry import RunFiles, utcnow

LABELS = "ABCDEFGH"
JUDGE_TIMEOUT_S = 120
API_RETRIES = 2      # after the first call
REPAIR_RETRIES = 1   # one repair retry with the schema restated


def blind_order(scenario_id: str, model_ids: list[str]) -> list[str]:
    """Deterministic per-scenario shuffle — position rotation without
    memorising anything (plan: seed = hash(scenario_id))."""
    import random
    seed = int.from_bytes(hashlib.sha256(scenario_id.encode()).digest()[:8], "big")
    order = sorted(model_ids)
    random.Random(seed).shuffle(order)
    return order


def strip_image_metadata(path: Path) -> bytes:
    """Re-encode as clean PNG: EXIF, XMP, text chunks all dropped."""
    from PIL import Image
    with Image.open(path) as im:
        clean = Image.new(im.mode, im.size)
        clean.paste(im)
        buf = io.BytesIO()
        clean.save(buf, format="PNG")
        return buf.getvalue()


def build_judge_prompt(scenario: Scenario, judge_criteria, measured_facts: list[str],
                       measured_names: list[str]) -> str:
    names = [c.name for c in judge_criteria]
    criteria_lines = "\n".join(f"- {c.name}: {c.description}" for c in judge_criteria)
    facts_block = ""
    if measured_facts:
        facts_block = ("MEASURED FACTS (established by code — treat as true, do not "
                       "re-estimate):\n" + "\n".join(f"  - {f}" for f in measured_facts) + "\n")
        if measured_names:
            facts_block += (f"The criteria {measured_names} are scored from these "
                            f"measurements by code, not by you. Do not score them and "
                            f"do not let them double-count into other criteria.\n")
    schema = {"criteria": [{"name": n, "reasoning": "...", "score": 0} for n in names],
              "overall_note": "..."}
    return (
        "You are evaluating one generated image against a brief. Score only what "
        "you can observe. You will never be told which system produced it.\n\n"
        f"BRIEF (given to the generator, verbatim):\n{scenario.text}\n\n"
        f"EXPECTED RESULT:\n{scenario.expected}\n\n"
        f"{facts_block}\n"
        "CRITERIA — for each, write reasoning FIRST (1-2 sentences, each citing "
        "something specific you can see in this output), then an integer or "
        "decimal score from 0 to 10:\n"
        f"{criteria_lines}\n\n"
        "Return JSON only, exactly this shape, criteria in exactly this order, "
        "reasoning before score in every object:\n"
        f"{json.dumps(schema)}")


class JudgeSchemaError(ValueError):
    pass


def parse_judge_response(text: str, expected_names: list[str]) -> dict:
    """Strict: every expected criterion present, score 0-10, non-empty reasoning."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise JudgeSchemaError(f"unparseable JSON: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), list):
        raise JudgeSchemaError("missing criteria array")
    seen = {}
    for item in data["criteria"]:
        if not isinstance(item, dict):
            raise JudgeSchemaError("criteria entries must be objects")
        name, reasoning, score = item.get("name"), item.get("reasoning"), item.get("score")
        if name not in expected_names:
            raise JudgeSchemaError(f"unexpected criterion {name!r}")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise JudgeSchemaError(f"criterion {name!r}: empty reasoning")
        if not isinstance(score, (int, float)) or isinstance(score, bool) \
                or not (0 <= float(score) <= 10):
            raise JudgeSchemaError(f"criterion {name!r}: score out of range: {score!r}")
        seen[name] = {"reasoning": reasoning.strip(), "score": float(score)}
    missing = [n for n in expected_names if n not in seen]
    if missing:
        raise JudgeSchemaError(f"missing criteria: {missing}")
    return {"criteria": seen, "overall_note": str(data.get("overall_note", "")).strip()}


def judge_run(project_root: Path, run_dir: Path, models_path: Path) -> dict:
    project_root, run_dir = Path(project_root), Path(run_dir)
    from .loaders import load_models
    mf = load_models(models_path)
    manifest = Manifest(run_dir)
    if not manifest.data:
        raise RunRejected(f"no manifest in {run_dir}")
    modality = manifest.data["modality"]
    judge_cfg = mf.judge.get(modality)
    if judge_cfg is None:
        raise RunRejected(f"no judge configured for modality {modality!r} in {models_path}")
    if judge_cfg.vertex:
        from .generate import adc_available
        if not adc_available():
            raise RunRejected(
                f"judge uses Vertex (project {judge_cfg.vertex.project}) but no "
                f"Application Default Credentials — run: gcloud auth "
                f"application-default login. Nothing was called.")
    elif not os.environ.get(judge_cfg.auth_env):
        raise RunRejected(f"judge API key missing ({judge_cfg.auth_env}) — nothing was called")

    # frozen scenarios are the truth for this run
    scenarios = {}
    for f in sorted((run_dir / "scenarios").glob("*.yaml")):
        data = yaml.safe_load(f.read_text())
        data.pop("source_path", None)
        s = Scenario(**data, source_path=str(f))
        scenarios[s.id] = s

    # rubric must match the manifest hash — a rubric edit means a new run
    rubrics_dir = project_root / "configs" / "rubrics"
    rubrics = {}
    for task, expected_hash in manifest.data.get("rubric_hashes", {}).items():
        rubric = load_rubric(rubrics_dir, modality, task)
        if rubric.rubric_hash != expected_hash:
            raise RunRejected(
                f"rubric for task {task!r} has changed since this run was created "
                f"(hash {rubric.rubric_hash[:12]} != manifest {expected_hash[:12]}). "
                f"A rubric edit means a NEW run — never a re-judged old one.")
        rubrics[task] = rubric

    files = RunFiles(run_dir)
    already = {(r["scenario_id"], r["model_id"]) for r in files.read("judge")
               if r.get("status") == "judged"}
    checks_by_cell = {(r["scenario_id"], r["model_id"]): r for r in files.read("checks")}

    adapter = adapter_registry.get(judge_cfg.adapter, judge_cfg, JUDGE_TIMEOUT_S)
    run_id = manifest.data["run_id"]

    # eligible: passed the gates, not yet judged (resume: never pay twice)
    by_scenario: dict[str, list[str]] = {}
    for key, cell in manifest.data["cells"].items():
        if cell["state"] in ("measured", "judged"):  # judged = re-run for unscored
            if (cell["scenario_id"], cell["model_id"]) not in already:
                by_scenario.setdefault(cell["scenario_id"], []).append(cell["model_id"])

    counts = {"judged": 0, "unjudged": 0, "skipped_existing": len(already)}
    for scenario_id in sorted(by_scenario):
        s = scenarios.get(scenario_id)
        if s is None:
            continue
        rubric = rubrics[s.task]
        crits = effective_criteria(rubric, s)
        judge_crits = [c for c in crits if c.judged_by == "judge"]
        measured_names = [c.name for c in crits if c.judged_by == "measured"]

        order = blind_order(scenario_id, by_scenario[scenario_id])
        blind_map = {LABELS[i]: mid for i, mid in enumerate(order)}

        for label, model_id in blind_map.items():
            out_dir = run_dir / "outputs" / s.modality / s.id
            path = _find_existing_output(out_dir, model_id)
            if path is None:
                continue
            clean_bytes = strip_image_metadata(path)

            check_row = checks_by_cell.get((s.id, model_id), {})
            measures = check_row.get("measures", {})
            facts = []
            if measures.get("width"):
                facts.append(f"resolution: {measures['width']}x{measures['height']}")
            if "ocr_match" in measures:
                facts.append(f"text found by OCR: {json.dumps(measures.get('ocr_text', ''))} "
                             f"(fuzzy match {measures['ocr_match']:.2f} against the "
                             f"required text)")
            if "preservation_phash_distance" in measures:
                facts.append(f"measured preservation (global pHash distance vs source): "
                             f"{measures['preservation_phash_distance']}")
            if "preservation_ssim_outside" in measures:
                facts.append(f"measured preservation (SSIM outside declared region): "
                             f"{measures['preservation_ssim_outside']:.4f}")

            prompt = build_judge_prompt(s, judge_crits, facts, measured_names)
            media = []
            if s.task in ("image_edit", "inpaint_mask"):
                src_rel = next((i for i in manifest.data.get("inputs", {}).get(s.id, [])
                                if i["role"] == "source"), None)
                if src_rel:
                    media.append((strip_image_metadata(run_dir / src_rel["path"]),
                                  "image/png"))
                    prompt = ("The FIRST image is the untouched SOURCE; the SECOND is "
                              "the edited RESULT you are scoring.\n\n") + prompt
            media.append((clean_bytes, "image/png"))

            leaked = [w for w in
                      {m["id"] for m in manifest.data["models"]}
                      | {m["provider"] for m in manifest.data["models"]}
                      | {m["provider_model"] for m in manifest.data["models"]}
                      if w.lower() in prompt.lower()]
            if leaked:
                # blinding is structurally impossible for this scenario — an
                # unjudged cell with a loud reason, never a silently-unblind call
                key = f"{s.id}::{model_id}"
                reason = f"blind violation: {leaked} would appear in the judge prompt"
                files.judge.append({"ts": utcnow(), "run_id": run_id,
                                    "scenario_id": s.id, "model_id": model_id,
                                    "task": s.task, "status": "unjudged",
                                    "error": reason})
                manifest.set_cell_state(key, "unjudged", reason)
                counts["unjudged"] += 1
                continue
            prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()

            row = {"ts": utcnow(), "run_id": run_id, "scenario_id": s.id,
                   "model_id": model_id, "task": s.task, "blind_label": label,
                   "blind_map": blind_map,
                   "judge": {"adapter": judge_cfg.adapter,
                             "provider_model": judge_cfg.provider_model,
                             "temperature": judge_cfg.temperature},
                   "rubric_version": rubric.version, "rubric_hash": rubric.rubric_hash,
                   "prompt_sha256": prompt_sha}

            outcome = _call_judge(adapter, prompt, media,
                                  [c.name for c in judge_crits])
            row.update(outcome)
            if outcome.get("usage") is not None:
                row["cost"] = compute_cost(judge_cfg.price, outcome["usage"])
            files.judge.append(row)

            key = f"{s.id}::{model_id}"
            if outcome["status"] == "judged":
                manifest.set_cell_state(key, "judged")
                counts["judged"] += 1
            else:
                manifest.set_cell_state(key, "unjudged", outcome.get("error", ""))
                counts["unjudged"] += 1
            print(f"  [{outcome['status']:>8}] {s.id} x {model_id} (blind {label})")

    manifest.set_run_state("judged")
    from .summary import refresh_scenario_status
    refresh_scenario_status(manifest)
    return counts


def _call_judge(adapter, prompt: str, media, expected_names: list[str]) -> dict:
    """API error -> retry x2; unparseable/invalid JSON -> one repair retry.
    Anything beyond that is `unjudged` — never a zero."""
    raw, error = None, None
    repair_used = False
    api_attempts = 0
    current_prompt = prompt
    while api_attempts <= API_RETRIES:
        api_attempts += 1
        try:
            result = adapter.judge(current_prompt, media)
        except SafetyRefusal as e:
            return {"status": "unjudged", "error": f"judge refused: {e}",
                    "raw_response": None, "usage": None}
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            if getattr(e, "retryable", True) and api_attempts <= API_RETRIES:
                time.sleep(backoff_s(api_attempts))
                continue
            return {"status": "unjudged", "error": error,
                    "raw_response": None, "usage": None}
        raw = result.text
        try:
            parsed = parse_judge_response(raw, expected_names)
            return {"status": "judged", "criteria": parsed["criteria"],
                    "overall_note": parsed["overall_note"], "raw_response": raw,
                    "usage": result.usage,
                    "judge_provider_version": result.provider_version}
        except JudgeSchemaError as e:
            error = str(e)
            if not repair_used:
                repair_used = True
                current_prompt = (prompt + "\n\nYour previous response did not match "
                                  f"the schema ({e}). Return ONLY the JSON object in "
                                  "exactly the shape specified, nothing else.")
                continue
            return {"status": "unjudged", "error": f"schema: {error}",
                    "raw_response": raw, "usage": result.usage}
    return {"status": "unjudged", "error": error, "raw_response": raw, "usage": None}
