"""Fan out scenario x model, save outputs, write telemetry (plan §8).

A loop with a thread pool — that is the entire scheduling story. Rules
enforced here:
  * same prompt string for every model, passed by value
  * one attempt = one telemetry row; retries are visible, never smoothed
  * per-provider semaphore (limits.max_concurrency) + optional rpm pacing
  * resume is free: an existing output is never regenerated
  * pre-flight budget estimate; missing key is a hard stop before any spend
  * caps are enforced in total AND per provider, because one run bills more
    than one account and a combined cap protects neither
  * latency measured around the adapter call only
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml

from . import adapters as adapter_registry
from .adapters.base import Asset, GenRequest, SafetyRefusal
from .cost import MICRO, compute_cost, estimate_call_micro_usd
from .loaders import effective_criteria, load_rubric, sha256_file
from .matrix import build_matrix
from .telemetry import RunFiles, utcnow

# video is a long-running operation: submit + poll can take many minutes.
# Raised 900 -> 1800 on 2026-09-10 for the 1080p run. Seedance's MEASURED max
# at the far lighter 720p/8s pilot was 578.9s (median 244.4s); this run is
# 2.25x the pixels, audio on, and outputs up to 19.2s. Crossing the old cap
# was not just a lost clip: Timeout is retryable, so the runner would retry
# up to MAX_ATTEMPTS while the provider's task kept running and BILLING
# server-side — paying two or three times for one clip, with only one charge
# ever reaching telemetry. The budget cap cannot see spend we never record.
TIMEOUTS_S = {"image": 120, "voice": 180, "video": 1800}
MAX_ATTEMPTS = 3

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
        "audio/wav": "wav", "audio/mpeg": "mp3", "video/mp4": "mp4"}


class RunRejected(Exception):
    """Validation / missing key / pre-flight budget — before any spend."""


def adc_available() -> bool:
    """Application Default Credentials present (for the Vertex auth route)."""
    try:
        import google.auth
        google.auth.default()
        return True
    except Exception:
        return False


class BudgetExceeded(Exception):
    pass


# --------------------------------------------------------------------------
# Manifest — run + cell state, written at every transition
# --------------------------------------------------------------------------

class Manifest:
    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "manifest.json"
        self._lock = threading.Lock()
        self.data: dict = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False, default=str))
        os.replace(tmp, self.path)

    def set_run_state(self, state: str, reason: str = "") -> None:
        with self._lock:
            self.data["state"] = state
            self.data.setdefault("events", []).append(
                {"ts": utcnow(), "state": state, **({"reason": reason} if reason else {})})
            self.save()

    def set_cell_state(self, key: str, state: str, reason: str = "") -> None:
        with self._lock:
            cell = self.data["cells"][key]
            cell["state"] = state
            if reason:
                cell["reason"] = reason
            self.save()


# --------------------------------------------------------------------------
# Throttling and budget
# --------------------------------------------------------------------------

class RpmPacer:
    def __init__(self, rpm: int):
        self.min_interval = 60.0 / rpm
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_at)
            self._next_at = start + self.min_interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


class BudgetGuard:
    """The total cap, plus an independent cap per provider.

    One run bills more than one account: the Google arm draws on Vertex,
    the BytePlus arm on a prepaid ModelArk balance. A single combined cap
    protects neither. It trips on the sum, so a cheap arm eats the headroom
    the expensive one needed, and a cap set to a prepaid balance stops the
    run long before that balance is actually gone. Each cap is therefore
    checked on its own and the first to trip stops that call.

    A cap RESERVES before a call and settles after it, rather than checking
    spend and recording it as two separate steps. That distinction is not
    academic: on 2026-09-11 a $14 cap let $18.94 through, because two workers
    both passed the check while `spent` was still zero — neither call had
    returned, so neither had been recorded. Under `reserve`, the second worker
    sees the first one's estimate already held against the cap.

    The reservation is the ESTIMATE; settlement records what was actually
    billed. So a cap can still be overshot by however much one call exceeds
    its own estimate — $7.28 on a Seedance edit that day, whose real cost was
    2.7x the flat figure. Reservations bound concurrency, not bad estimates.
    Leave a margin for both.

    Generation only. Judging bills separately and is not counted here.
    """

    def __init__(self, budget_micro: int | None, already_spent_micro: int = 0,
                 provider_caps_micro: dict[str, int] | None = None,
                 already_spent_by_provider: dict[str, int] | None = None):
        self.budget = budget_micro
        self.spent = already_spent_micro
        self.caps = dict(provider_caps_micro or {})
        self.by_provider = dict(already_spent_by_provider or {})
        self.reserved = 0
        self.reserved_by_provider: dict[str, int] = {}
        self._lock = threading.Lock()

    def before_call(self, est_micro: int, provider: str | None = None) -> None:
        """Hold `est_micro` against every applicable cap, or refuse the call.

        Must be paired with settle() — including on the failure path, or a
        failed attempt holds budget for the rest of the run.
        """
        with self._lock:
            if (self.budget is not None
                    and self.spent + self.reserved + est_micro > self.budget):
                raise BudgetExceeded(
                    f"spent {self.spent / MICRO:.4f} USD with "
                    f"{self.reserved / MICRO:.4f} in flight; next call (~"
                    f"{est_micro / MICRO:.4f}) would exceed budget "
                    f"{self.budget / MICRO:.2f}")
            cap = self.caps.get(provider)
            if cap is not None:
                spent = self.by_provider.get(provider, 0)
                held = self.reserved_by_provider.get(provider, 0)
                if spent + held + est_micro > cap:
                    raise BudgetExceeded(
                        f"{provider}: spent {spent / MICRO:.4f} USD with "
                        f"{held / MICRO:.4f} in flight; next call (~"
                        f"{est_micro / MICRO:.4f}) would exceed that "
                        f"provider's cap {cap / MICRO:.2f}")
            self.reserved += est_micro
            if provider is not None:
                self.reserved_by_provider[provider] = (
                    self.reserved_by_provider.get(provider, 0) + est_micro)

    def settle(self, est_micro: int, actual_micro: int,
               provider: str | None = None) -> None:
        """Release the reservation and record what the call really cost.

        actual_micro is 0 for a call that failed or was refused — those bill
        nothing, and the held estimate must come back either way.
        """
        with self._lock:
            self.reserved = max(0, self.reserved - est_micro)
            if provider is not None:
                self.reserved_by_provider[provider] = max(
                    0, self.reserved_by_provider.get(provider, 0) - est_micro)
            if actual_micro:
                self.spent += actual_micro
                if provider is not None:
                    self.by_provider[provider] = (
                        self.by_provider.get(provider, 0) + actual_micro)

    def add(self, micro: int, provider: str | None = None) -> None:
        with self._lock:
            self.spent += micro
            if provider is not None:
                self.by_provider[provider] = self.by_provider.get(provider, 0) + micro


def backoff_s(attempt: int) -> float:
    return (2.0 ** attempt) + random.uniform(0, 1)


# --------------------------------------------------------------------------
# Run preparation — freeze, validate, plan; reject before any spend
# --------------------------------------------------------------------------

def _git_sha(project_root: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root,
                              capture_output=True, text=True, timeout=5,
                              check=True).stdout.strip()
    except Exception:
        return None


def prepare_run(project_root: Path, runs_root: Path, run_id: str | None,
                scenarios, models, modality: str, budget_usd: float | None,
                rubrics_dir: Path, provider_caps: dict[str, float] | None = None):
    """Create (or reopen, for resume) the run folder; freeze everything;
    build the matrix; hard-stop on validation or missing keys."""
    resuming = run_id is not None
    if run_id is None:
        run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_{modality}"
    run_dir = runs_root / run_id
    if resuming and not run_dir.exists():
        raise RunRejected(f"cannot resume: {run_dir} does not exist")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "outputs").mkdir(exist_ok=True)
    (run_dir / "scenarios").mkdir(exist_ok=True)

    manifest = Manifest(run_dir)
    if not manifest.data:
        manifest.data = {"run_id": run_id, "created": utcnow(), "state": "draft",
                         "modality": modality, "git_sha": _git_sha(project_root),
                         "budget_usd": budget_usd,
                         "budget_by_provider": dict(provider_caps or {}) or None,
                         "events": []}
        manifest.set_run_state("draft")

    # ---- rubrics + effective weights (validated now, recorded now) --------
    rubric_hashes, effective_weights, rubrics = {}, {}, {}
    for s in scenarios:
        rubric = rubrics.get(s.task) or load_rubric(rubrics_dir, s.modality, s.task)
        rubrics[s.task] = rubric
        rubric_hashes[s.task] = rubric.rubric_hash
        crits = effective_criteria(rubric, s)   # raises -> rejected below
        effective_weights[s.id] = {c.name: round(c.weight, 6) for c in crits}

    # ---- freeze scenarios + input assets ----------------------------------
    frozen_assets: dict[str, dict[str, Path]] = {}
    input_shas: dict[str, list[dict]] = {}
    for s in scenarios:
        frozen = run_dir / "scenarios" / f"{s.id}.yaml"
        if not frozen.exists():
            src = Path(s.source_path) if s.source_path and ":" not in s.source_path[2:] else None
            if src and src.exists() and src.suffix in (".yaml", ".yml"):
                shutil.copy2(src, frozen)
            else:  # CSV-sourced rows are frozen as YAML dumps
                frozen.write_text(yaml.safe_dump(
                    s.model_dump(exclude={"source_path"}, exclude_none=True),
                    sort_keys=False, allow_unicode=True))
        frozen_assets[s.id], input_shas[s.id] = {}, []
        for role, rel in (s.inputs or {}).items():
            if role == "bbox":
                continue
            src_asset = (project_root / rel).resolve()
            if not src_asset.exists():
                raise RunRejected(f"scenario {s.id}: input asset missing: {src_asset}")
            dst = run_dir / "inputs" / s.id / Path(rel).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src_asset, dst)
            sha = sha256_file(dst)
            frozen_assets[s.id][role] = dst
            input_shas[s.id].append({"role": role, "sha256": sha, "path": str(dst.relative_to(run_dir))})

    scenario_set_hash = hashlib.sha256(
        b"".join(sorted((run_dir / "scenarios" / f"{s.id}.yaml").read_bytes()
                        for s in scenarios))).hexdigest()

    # ---- matrix ------------------------------------------------------------
    cells = build_matrix(scenarios, models)
    existing_cells = manifest.data.get("cells", {})
    manifest.data.update({
        "models": [{"id": m.id, "provider": m.provider, "adapter": m.adapter,
                    "provider_model": m.provider_model, "supports": m.supports,
                    "price_as_of": m.price.as_of, "voice_map": m.voice_map or None}
                   for m in models],
        "rubric_hashes": rubric_hashes,
        "rubric_files": {t: r.source_files for t, r in rubrics.items()},
        "effective_weights": effective_weights,
        "scenario_set_hash": scenario_set_hash,
        "inputs": input_shas,
        "cells": {c.key: {"scenario_id": c.scenario_id, "model_id": c.model_id,
                          "task": c.task, "modality": c.modality,
                          "state": existing_cells.get(c.key, {}).get("state", c.state),
                          "reason": existing_cells.get(c.key, {}).get("reason", c.reason)}
                  for c in cells},
    })
    manifest.set_run_state("planned")

    # ---- hard stops, before any spend --------------------------------------
    active_models = {c.model_id for c in cells if c.state != "skipped"}
    missing = [f"{m.id} ({m.auth_env})" for m in models
               if m.id in active_models and m.auth_env and not os.environ.get(m.auth_env)]
    if missing:
        manifest.set_run_state("rejected", f"missing API keys: {', '.join(missing)}")
        raise RunRejected(
            "missing API key(s), before any spend:\n  " + "\n  ".join(missing)
            + "\n  -> put them in .env (see .env.example) or export them.")
    vertex_models = [m for m in models if m.id in active_models and m.vertex]
    if vertex_models and not adc_available():
        who = ", ".join(f"{m.id} (project {m.vertex.project})" for m in vertex_models)
        manifest.set_run_state("rejected", f"no ADC for Vertex models: {who}")
        raise RunRejected(
            f"Vertex models need Application Default Credentials, before any "
            f"spend: {who}\n  -> run: gcloud auth application-default login "
            f"(as an account with Vertex access on that project)")

    return run_dir, manifest, cells, rubrics, frozen_assets


# --------------------------------------------------------------------------
# One cell = one scenario x one model; up to 3 attempts, one row each
# --------------------------------------------------------------------------

def _find_existing_output(out_dir: Path, model_id: str) -> Path | None:
    if not out_dir.exists():
        return None
    for f in sorted(out_dir.glob(f"{model_id}.*")):
        if ".invalid" not in f.name and f.suffix != ".json":
            return f
    return None


def one_cell(run_id, scenario, model, adapter, run_dir: Path, files: RunFiles,
             manifest: Manifest, sem: threading.BoundedSemaphore,
             pacer: RpmPacer | None, budget: BudgetGuard,
             assets: dict, input_sha_rows: list) -> dict:
    key = f"{scenario.id}::{model.id}"
    out_dir = run_dir / "outputs" / scenario.modality / scenario.id
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = _find_existing_output(out_dir, model.id)
    if existing is not None:  # resume: never pay twice for the same cell
        state = manifest.data["cells"][key].get("state", "planned")
        if state in ("planned", "prepared", "failed"):
            manifest.set_cell_state(key, "generated", "resumed from existing output")
        return {"cell": key, "status": "resumed", "path": existing}

    params = {**model.params, **scenario.params}
    req = GenRequest(task=scenario.task, text=scenario.text,
                     inputs=[Asset(role=r, path=p, mime=_guess_mime(p), sha256=next(
                         (row["sha256"] for row in input_sha_rows if row["role"] == r), ""))
                             for r, p in assets.items()],
                     params=params)

    base_row = {
        "run_id": run_id, "scenario_id": scenario.id, "modality": scenario.modality,
        "task": scenario.task, "model_id": model.id, "provider": model.provider,
        "provider_model": model.provider_model, "adapter": model.adapter,
        "params": params, "inputs": input_sha_rows,
    }

    last_exc: Exception | None = None
    attempt = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        est_micro = estimate_call_micro_usd(model.price, scenario.task)
        budget.before_call(est_micro, model.provider)
        # Whatever happens below — success, refusal, retry, early return — the
        # reservation must come back, or a failed attempt holds budget for the
        # rest of the run and the cap strangles work it should have allowed.
        billed_micro = 0
        try:
            with sem:
                if pacer:
                    pacer.wait()
                t0 = time.perf_counter()
                try:
                    res = adapter.run(req)
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                except Exception as e:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    last_exc = e
                    status = getattr(e, "status", "provider_error")
                    files.telemetry.append({"ts": utcnow(), **base_row, "attempt": attempt,
                                            "status": status, "latency_ms": latency_ms,
                                            "error": str(e)})
                    if isinstance(e, SafetyRefusal) or not getattr(e, "retryable", False):
                        break
                    if attempt < MAX_ATTEMPTS:
                        retry_after = getattr(e, "retry_after", None)
                        time.sleep(retry_after if retry_after else backoff_s(attempt))
                    continue

            ext = _EXT.get(res.mime, "bin")
            path = out_dir / f"{model.id}.{ext}"
            path.write_bytes(res.data)
            cost = compute_cost(model.price, res.usage)
            billed_micro = cost["micro_usd"]
            out_meta = {"path": str(path.relative_to(run_dir)),
                        "sha256": hashlib.sha256(res.data).hexdigest(),
                        "bytes": len(res.data)}
            try:
                from PIL import Image
                if res.mime.startswith("image/"):
                    with Image.open(path) as im:
                        out_meta["width"], out_meta["height"] = im.size
            except Exception:
                pass
            files.telemetry.append({
                "ts": utcnow(), **base_row, "attempt": attempt, "status": "ok",
                "provider_version": res.provider_version,
                "params_unsupported": res.params_unsupported,
                "applied_params": res.applied_params,
                "latency_ms": latency_ms, "request_id": res.request_id,
                "output": out_meta, "usage": res.usage, "cost": cost})
            manifest.set_cell_state(key, "generated")
            return {"cell": key, "status": "ok", "path": path}
        finally:
            budget.settle(est_micro, billed_micro, model.provider)

    status = getattr(last_exc, "status", "provider_error") if last_exc else "provider_error"
    manifest.set_cell_state(key, "failed", f"{status} after {attempt} attempt(s): {last_exc}")
    return {"cell": key, "status": status, "path": None}


def _guess_mime(path: Path) -> str:
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "wav": "audio/wav",
            "mp4": "video/mp4"}.get(path.suffix.lstrip("."),
                                    "application/octet-stream")


# --------------------------------------------------------------------------
# The run command: generate everything, then run deterministic checks
# --------------------------------------------------------------------------

def run_generation(project_root: Path, scenarios, models, modality: str,
                   budget_usd: float | None, workers: int = 4,
                   run_id: str | None = None, runs_root: Path | None = None,
                   rubrics_dir: Path | None = None,
                   provider_caps: dict[str, float] | None = None) -> Path:
    project_root = Path(project_root)
    runs_root = runs_root or project_root / "runs"
    rubrics_dir = rubrics_dir or project_root / "configs" / "rubrics"
    provider_caps = dict(provider_caps or {})

    # A cap naming a provider that is not running protects nothing. That is a
    # typo, not a preference, so it is refused here rather than ignored — the
    # whole point of the flag is that someone is relying on it.
    known = {m.provider for m in models}
    unknown = sorted(set(provider_caps) - known)
    if unknown:
        raise RunRejected(
            f"provider cap names no enabled provider: {', '.join(unknown)} "
            f"(enabled: {', '.join(sorted(known))}); nothing was called")

    run_dir, manifest, cells, rubrics, frozen_assets = prepare_run(
        project_root, runs_root, run_id, scenarios, models, modality,
        budget_usd, rubrics_dir, provider_caps)
    run_id = manifest.data["run_id"]
    files = RunFiles(run_dir)
    scen_by_id = {s.id: s for s in scenarios}
    model_by_id = {m.id: m for m in models}

    # ---- pre-flight budget estimate ---------------------------------------
    todo = [c for c in cells if c.state == "planned"
            and _find_existing_output(run_dir / "outputs" / c.modality / c.scenario_id,
                                      c.model_id) is None]
    est_micro = sum(estimate_call_micro_usd(model_by_id[c.model_id].price, c.task)
                    for c in todo)
    spent_micro = sum(r.get("cost", {}).get("micro_usd", 0) for r in files.read("telemetry"))
    est_by_provider: dict[str, int] = {}
    for c in todo:
        m = model_by_id[c.model_id]
        est_by_provider[m.provider] = (est_by_provider.get(m.provider, 0)
                                       + estimate_call_micro_usd(m.price, c.task))
    # resume: what each provider has already been billed on this run
    spent_by_provider: dict[str, int] = {}
    for r in files.read("telemetry"):
        micro = r.get("cost", {}).get("micro_usd", 0)
        if micro:
            spent_by_provider[r.get("provider")] = (
                spent_by_provider.get(r.get("provider"), 0) + micro)
    print(f"[pre-flight] {len(todo)} cell(s) to generate; estimated "
          f"{est_micro / MICRO:.4f} USD (spent so far {spent_micro / MICRO:.4f})")
    for provider, cap in sorted(provider_caps.items()):
        print(f"[pre-flight] {provider}: estimated "
              f"{est_by_provider.get(provider, 0) / MICRO:.4f} USD (spent so far "
              f"{spent_by_provider.get(provider, 0) / MICRO:.4f}) against cap "
              f"{cap:.2f}")
    if budget_usd is not None and spent_micro + est_micro > budget_usd * MICRO:
        manifest.set_run_state(
            "rejected", f"pre-flight estimate {(spent_micro + est_micro) / MICRO:.4f} USD "
                        f"exceeds budget {budget_usd:.2f}")
        raise RunRejected(
            f"pre-flight estimate {(spent_micro + est_micro) / MICRO:.4f} USD exceeds "
            f"--budget {budget_usd:.2f}; nothing was called")
    for provider, cap in sorted(provider_caps.items()):
        total = spent_by_provider.get(provider, 0) + est_by_provider.get(provider, 0)
        if total > cap * MICRO:
            manifest.set_run_state(
                "rejected", f"pre-flight estimate for {provider} "
                            f"{total / MICRO:.4f} USD exceeds its cap {cap:.2f}")
            raise RunRejected(
                f"pre-flight estimate for {provider} {total / MICRO:.4f} USD exceeds "
                f"its cap {cap:.2f}; nothing was called")

    # ---- adapters, semaphores, pacers -------------------------------------
    timeout_s = TIMEOUTS_S.get(modality, 180)
    active = sorted({c.model_id for c in cells if c.state != "skipped"})
    adapter_by_model = {mid: adapter_registry.get(model_by_id[mid].adapter,
                                                  model_by_id[mid], timeout_s)
                        for mid in active}
    sems: dict[str, threading.BoundedSemaphore] = {}
    pacers: dict[str, RpmPacer | None] = {}
    for mid in active:
        m = model_by_id[mid]
        if m.provider not in sems:
            sems[m.provider] = threading.BoundedSemaphore(m.limits.max_concurrency)
            pacers[m.provider] = RpmPacer(m.limits.rpm) if m.limits.rpm else None

    budget = BudgetGuard(round(budget_usd * MICRO) if budget_usd is not None else None,
                         spent_micro,
                         {p: round(c * MICRO) for p, c in provider_caps.items()},
                         spent_by_provider)
    manifest.set_run_state("running")

    # ---- fan out -----------------------------------------------------------
    aborted = False
    gen_cells = [c for c in cells if c.state != "skipped"]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for c in gen_cells:
            s, m = scen_by_id[c.scenario_id], model_by_id[c.model_id]
            futures[pool.submit(
                one_cell, run_id, s, m, adapter_by_model[m.id], run_dir, files,
                manifest, sems[m.provider], pacers[m.provider], budget,
                frozen_assets.get(s.id, {}),
                manifest.data["inputs"].get(s.id, []))] = c
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                result = fut.result()
                print(f"  [{result['status']:>14}] {c.scenario_id} x {c.model_id}")
            except BudgetExceeded as e:
                aborted = True
                manifest.set_cell_state(c.key, "failed", f"budget: {e}")
                print(f"  [ budget-stop ] {c.scenario_id} x {c.model_id}: {e}")
            except Exception as e:
                manifest.set_cell_state(c.key, "failed", f"runner error: {e}")
                print(f"  [ runner-error] {c.scenario_id} x {c.model_id}: {e}")

    if aborted:
        manifest.set_run_state("aborted", "budget cap hit — partial run, clearly labelled")
    else:
        manifest.set_run_state("generated")

    # ---- deterministic checks (free, always, before any judging) ----------
    run_deterministic_checks(run_dir, manifest, scen_by_id, model_by_id,
                             adapter_by_model, files, frozen_assets, budget,
                             sems, pacers)

    # a scenario is only complete when EVERY required model finished — partial
    # completion is tracked, never hidden
    from .summary import build_navigation, completion_counts, refresh_scenario_status
    refresh_scenario_status(manifest)
    cc = completion_counts(manifest.data)
    print("[scenarios] " + " · ".join(f"{k}={v}" for k, v in sorted(cc.items())))
    build_navigation(run_dir)   # by-model symlink view + INDEX.csv
    return run_dir


def run_deterministic_checks(run_dir, manifest, scen_by_id, model_by_id,
                             adapter_by_model, files: RunFiles, frozen_assets,
                             budget, sems, pacers) -> None:
    from .checks import run_checks
    already = {(r["scenario_id"], r["model_id"]): r for r in files.read("checks")}
    run_id = manifest.data["run_id"]
    for key, cell in manifest.data["cells"].items():
        if cell["state"] not in ("generated",):
            continue
        s = scen_by_id.get(cell["scenario_id"])
        if s is None:
            continue
        prior = already.get((s.id, cell["model_id"]))
        if prior is not None:  # checked in a previous invocation — restore state
            manifest.set_cell_state(key, "measured" if prior["passed"] else "invalid")
            continue
        out_dir = run_dir / "outputs" / cell["modality"] / s.id
        path = _find_existing_output(out_dir, cell["model_id"])
        if path is None:
            continue
        outcome = run_checks(s, path, frozen_assets.get(s.id, {}))

        if not outcome.passed:
            # one regeneration attempt, then stop (plan §18)
            bad = path.with_name(f"{path.stem}.invalid-1{path.suffix}")
            path.rename(bad)
            m = model_by_id[cell["model_id"]]
            result = one_cell(run_id, s, m, adapter_by_model[m.id], run_dir, files,
                              manifest, sems[m.provider], pacers[m.provider],
                              budget, frozen_assets.get(s.id, {}),
                              manifest.data["inputs"].get(s.id, []))
            path2 = result.get("path")
            outcome = run_checks(s, path2, frozen_assets.get(s.id, {})) if path2 else outcome

        files.checks.append({"ts": utcnow(), "run_id": run_id, "scenario_id": s.id,
                             "model_id": cell["model_id"], "task": s.task,
                             "gates": outcome.gates, "measures": outcome.measures,
                             "passed": outcome.passed})
        manifest.set_cell_state(
            key, "measured" if outcome.passed else "invalid",
            "" if outcome.passed else "failed gates: " + ", ".join(
                g["gate"] for g in outcome.gates if not g["passed"]))
