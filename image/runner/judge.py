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
# 120s judges every output in the bank so far. A large still (IMG-GEDIT-18's
# 1847x851 on 2026-09-21) can outlast it and come back 504 DEADLINE_EXCEEDED
# on every retry, which strands a paid-for, gate-passing image as `unjudged`.
# Raise it for that cell rather than re-running the same call:
#     GENMEDIA_JUDGE_TIMEOUT_S=300 python -m runner.cli judge --run <id> --retry-unjudged
JUDGE_TIMEOUT_S = int(os.environ.get("GENMEDIA_JUDGE_TIMEOUT_S", "120"))
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


def blind_leaks(terms, authored: str, ocr_text: str = "") -> list:
    """Vendor names that would actually unblind the judge.

    Authored text (scenario title / prompt / expected) is matched as a raw
    substring — deliberately strict, because a human wrote it and any
    occurrence is real.

    OCR text is different. The reader strips spaces, so a poster reading
    "FUSION OPEN AIR" arrives as "FUSIONOPENAIR" and a naive scan sees
    "openai". Inside OCR output a term must therefore stand as its own
    word (no letter/digit either side).

    Known limit: a brand genuinely rendered in an image can also arrive
    space-stripped ("MADEBYOPENAI") and will not match here. That is
    accepted because the judge is shown the image itself — withholding the
    OCR fact would not have blinded it anyway.
    """
    import re
    hits = {w for w in terms if w.lower() in authored.lower()}
    if ocr_text:
        low = ocr_text.lower()
        hits |= {w for w in terms
                 if re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", low)}
    return sorted(hits)


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


def build_checklist_judge_prompt(scenario: Scenario, judge_crits, input_roles: list[str],
                                 n_outputs: int, measured_facts: list[str],
                                 measured_names: list[str]) -> str:
    """Scenario-bank v2/v3 judging: every checklist question answered yes/no
    with evidence BEFORE any score, then each criterion scored 0-10 in the
    light of its own checks. The check -> criterion mapping is fixed in the
    scenario (the same for both arms), never chosen here."""
    names = [c.name for c in judge_crits]
    lines = []
    n_in = len(input_roles)
    if n_in:
        lines.append("IMAGES: the first %d image(s) are the INPUTS the generator was given: "
                     % n_in + "; ".join(f"image {i + 1} = {r}"
                                        for i, r in enumerate(input_roles)) + ".")
    if n_outputs > 1:
        lines.append(f"The next {n_outputs} images (images {n_in + 1}-{n_in + n_outputs}) are "
                     f"the OUTPUT SET you are scoring, in frame order. Judge them together.")
    else:
        lines.append(f"Image {n_in + 1} is the OUTPUT you are scoring.")
    brief = f"BRIEF (given to the generator, verbatim):\n{scenario.gen_text}\n"
    if scenario.set_members:
        brief += ("\nEach output was its own request: the brief above plus this line "
                  "for that frame:\n" + "\n".join(
                      f"  frame {k}: {m['name']} — {m['brief']}"
                      for k, m in enumerate(scenario.set_members, start=1)))
    keep = (scenario.output_set or {}).get("keep") or []
    if keep:
        brief += "\nMust stay the same across the set:\n" + "\n".join(
            f"  - {x}" for x in keep)
    if scenario.meta.get("bank") == "v2" and scenario.expected:
        brief += f"\nEXPECTED RESULT:\n{scenario.expected}"
    if scenario.deliverable:
        import re as _re
        target = _re.sub(r"\s*\d{3,4}\s*x\s*\d{3,4}\s*/?\s*", " ", scenario.deliverable).strip()
        brief += f"\nTARGET FORMAT (from the scenario bank, not part of the brief): {target}"
    if scenario.required_text:
        brief += ("\nREQUIRED TEXT, exact (check spelling and correctly formed script "
                  f"letter by letter):\n{scenario.required_text}")
    facts_block = ""
    if measured_facts:
        facts_block = ("\nMEASURED FACTS (established by code — treat as true, do not "
                       "re-estimate):\n" + "\n".join(f"  - {f}" for f in measured_facts))
        if measured_names:
            facts_block += (f"\nThe criteria {measured_names} are scored from these "
                            f"measurements by code, not by you.")
    crit_label = {c.name: c.name for c in judge_crits}
    checks = scenario.checklist or []
    check_lines = "\n".join(
        f"  {i}. [{c.get('scope') or 'image'}]"
        + (f" ({crit_label.get(c.get('dim'), c.get('dim'))})" if c.get("dim") else "")
        + f" {c['q']}" for i, c in enumerate(checks))
    criteria_lines = "\n".join(f"- {c.name}: {c.description}" for c in judge_crits)
    schema = {"checks": [{"i": 0, "evidence": "...", "answer": "yes"}],
              "criteria": [{"name": n, "reasoning": "...", "score": 0} for n in names],
              "overall_note": "..."}
    return (
        "You are evaluating generated images against a brief. Score only what you "
        "can observe. You will never be told which system produced them.\n\n"
        + "\n".join(lines) + "\n\n" + brief + "\n" + facts_block + "\n\n"
        "CHECKLIST — answer EVERY question first, in order. For each: one sentence of "
        "evidence citing what you see (name the frame for a set), then \"yes\" or "
        "\"no\". \"yes\" only when it fully holds; anything partial, missing or "
        "unreadable is \"no\".\n" + check_lines + "\n\n"
        "CRITERIA — then score each from 0 to 10, reasoning first. The checks tagged "
        "with a criterion are its evidence: start from the share answered yes "
        "(all yes = 10) and take off more for a severe failure than a slight one. "
        "A criterion with no tagged checks is scored on its description.\n"
        f"{criteria_lines}\n\n"
        "Return JSON only, exactly this shape: every check once, in order; criteria "
        "in exactly this order; evidence/reasoning before answer/score:\n"
        f"{json.dumps(schema)}")


class JudgeSchemaError(ValueError):
    pass


def parse_checklist_response(text: str, expected_names: list[str], n_checks: int) -> dict:
    """parse_judge_response, plus every check answered exactly once yes/no."""
    parsed = parse_judge_response(text, expected_names)
    data = json.loads(text)
    items = data.get("checks")
    if not isinstance(items, list):
        raise JudgeSchemaError("missing checks array")
    answers: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            raise JudgeSchemaError("checks entries must be objects")
        i, ans, ev = item.get("i"), str(item.get("answer", "")).strip().lower(), item.get("evidence")
        if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < n_checks):
            raise JudgeSchemaError(f"check index out of range: {i!r}")
        if ans not in ("yes", "no"):
            raise JudgeSchemaError(f"check {i}: answer must be yes or no, got {ans!r}")
        if not isinstance(ev, str) or not ev.strip():
            raise JudgeSchemaError(f"check {i}: empty evidence")
        if i in answers:
            raise JudgeSchemaError(f"check {i} answered more than once")
        answers[i] = {"i": i, "answer": ans == "yes", "evidence": ev.strip()}
    missing = [i for i in range(n_checks) if i not in answers]
    if missing:
        raise JudgeSchemaError(f"unanswered checks: {missing}")
    parsed["checks"] = [answers[i] for i in range(n_checks)]
    return parsed


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


def judge_run(project_root: Path, run_dir: Path, models_path: Path,
              retry_unjudged: bool = False) -> dict:
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
        # `unjudged` is a terminal state on purpose — a cell the judge could not
        # score is excluded from the mean, never given a 0. But the state does
        # not distinguish "the judge refused this content" from "Vertex
        # returned 429 RESOURCE_EXHAUSTED", and the second is a transient
        # infrastructure failure that permanently costs a paid-for data point.
        # Re-judging is free, so it is offered explicitly rather than silently:
        # only --retry-unjudged reopens those cells.
        eligible = ("measured", "judged") + (("unjudged",) if retry_unjudged else ())
        if cell["state"] in eligible:  # judged = re-run for unscored
            if (cell["scenario_id"], cell["model_id"]) not in already:
                by_scenario.setdefault(cell["scenario_id"], []).append(cell["model_id"])

    counts = {"judged": 0, "unjudged": 0, "skipped_existing": len(already)}
    jobs = []
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
            jobs.append((s, rubric, judge_crits, measured_names, blind_map,
                         label, model_id))

    def _judge_one(s, rubric, judge_crits, measured_names, blind_map, label, model_id):
        out_dir = run_dir / "outputs" / s.modality / s.id
        path = _find_existing_output(out_dir, model_id)
        if path is None:
            return None
        clean_bytes = strip_image_metadata(path)

        check_row = checks_by_cell.get((s.id, model_id), {})
        measures = check_row.get("measures", {})
        facts = []
        if measures.get("width"):
            facts.append(f"resolution: {measures['width']}x{measures['height']}")
        ocr_fact = ""
        if "ocr_match" in measures:
            ocr_fact = (f"text found by OCR: {json.dumps(measures.get('ocr_text', ''))} "
                        f"(fuzzy match {measures['ocr_match']:.2f} against the "
                        f"required text)")
            facts.append(ocr_fact)
        if "preservation_phash_distance" in measures:
            facts.append(f"measured preservation (global pHash distance vs source): "
                         f"{measures['preservation_phash_distance']}")
        if "preservation_ssim_outside" in measures:
            facts.append(f"measured preservation (SSIM outside declared region): "
                         f"{measures['preservation_ssim_outside']:.4f}")

        if s.checklist is not None:
            # bank v2/v3: no resolution fact (identical sizes are requested
            # from both arms, and a size can name a vendor); every input
            # and reference is shown, then every output of the set
            facts = [f for f in facts if not f.startswith("resolution:")]
            ins = [i for i in manifest.data.get("inputs", {}).get(s.id, [])
                   if i["role"] not in ("mask", "bbox")]
            outs = [path]
            if s.set_members:
                from .generate import _find_member_outputs
                mem = _find_member_outputs(path.parent, model_id)
                outs = [mem[k] for k in sorted(mem)]
            prompt = build_checklist_judge_prompt(
                s, judge_crits, [_role_label(i["role"]) for i in ins], len(outs),
                facts, measured_names)
            media = [(strip_image_metadata(run_dir / i["path"]), "image/png") for i in ins]
            media += [(strip_image_metadata(p), "image/png") for p in outs]
        else:
            prompt = build_judge_prompt(s, judge_crits, facts, measured_names)
            media = []
        if s.checklist is None and s.task in ("image_edit", "inpaint_mask"):
            src_rel = next((i for i in manifest.data.get("inputs", {}).get(s.id, [])
                            if i["role"] == "source"), None)
            if src_rel:
                media.append((strip_image_metadata(run_dir / src_rel["path"]),
                              "image/png"))
                prompt = ("The FIRST image is the untouched SOURCE; the SECOND is "
                          "the edited RESULT you are scoring.\n\n") + prompt
        if s.checklist is None:
            media.append((clean_bytes, "image/png"))

        terms = ({m["id"] for m in manifest.data["models"]}
                 | {m["provider"] for m in manifest.data["models"]}
                 | {m["provider_model"] for m in manifest.data["models"]})
        # OCR output is scanned under a word-boundary rule; everything a
        # human authored is still matched as a raw substring.
        leaked = blind_leaks(terms,
                             prompt.replace(ocr_fact, "") if ocr_fact else prompt,
                             measures.get("ocr_text", ""))
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
            return "unjudged"
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()

        row = {"ts": utcnow(), "run_id": run_id, "scenario_id": s.id,
               "model_id": model_id, "task": s.task, "blind_label": label,
               "blind_map": blind_map,
               "judge": {"adapter": judge_cfg.adapter,
                         "provider_model": judge_cfg.provider_model,
                         "temperature": judge_cfg.temperature,
                         "thinking_level": judge_cfg.thinking_level},
               "rubric_version": rubric.version, "rubric_hash": rubric.rubric_hash,
               "prompt_sha256": prompt_sha, "media_count": len(media)}

        outcome = _call_judge(adapter, prompt, media,
                              [c.name for c in judge_crits],
                              n_checks=(len(s.checklist)
                                        if s.checklist is not None else None))
        row.update(outcome)
        if outcome.get("usage") is not None:
            row["cost"] = compute_cost(judge_cfg.price, outcome["usage"])
        files.judge.append(row)

        key = f"{s.id}::{model_id}"
        if outcome["status"] == "judged":
            manifest.set_cell_state(key, "judged")
        else:
            manifest.set_cell_state(key, "unjudged", outcome.get("error", ""))
        print(f"  [{outcome['status']:>8}] {s.id} x {model_id} (blind {label})")
        return outcome["status"]

    # Cells are independent: each call is blind and absolute, so order does
    # not matter. GENMEDIA_JUDGE_WORKERS > 1 judges that many at once.
    workers = max(1, int(os.environ.get("GENMEDIA_JUDGE_WORKERS", "1")))
    if workers == 1:
        results = [_judge_one(*j) for j in jobs]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda j: _judge_one(*j), jobs))
    for r in results:
        if r in ("judged", "unjudged"):
            counts[r] += 1


    manifest.set_run_state("judged")
    from .summary import refresh_scenario_status
    refresh_scenario_status(manifest)
    return counts


def _role_label(role: str) -> str:
    if role == "source":
        return "the image to edit"
    if role.startswith("reference"):
        n = role[len("reference"):]
        return f"reference {n}" if n else "reference"
    return role


def _call_judge(adapter, prompt: str, media, expected_names: list[str],
                n_checks: int | None = None) -> dict:
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
            if n_checks is None:
                parsed = parse_judge_response(raw, expected_names)
            else:
                parsed = parse_checklist_response(raw, expected_names, n_checks)
            out = {"status": "judged", "criteria": parsed["criteria"],
                   "overall_note": parsed["overall_note"], "raw_response": raw,
                   "usage": result.usage,
                   "judge_provider_version": result.provider_version}
            if "checks" in parsed:
                out["checks"] = parsed["checks"]
            return out
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
