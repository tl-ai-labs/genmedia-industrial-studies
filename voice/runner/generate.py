"""
The generation runner - fan out, retry, resume, measure (plan v1.2 section 08).

A loop with a thread pool. That is the entire scheduling story: TTS
generation is network-bound, so threads are enough and asyncio buys nothing
worth the complexity.

WHAT THIS FILE ENFORCES, and why each rule is here:

  Same prompt object for all models. The script is read once per scenario and
  passed by value. Nothing appends to it, including the retry path - a retry
  that edited the text would make attempt 2 speak different words from
  attempt 1, and the clip would be compared against a script it no longer
  matches.

  One attempt = one telemetry row. Retries are visible, not smoothed away.

  Per-provider concurrency and pacing. One global pool, a semaphore per
  provider sized from limits.max_concurrency, and an optional rpm pacer.
  Without this the reliability column partly measures our own hammering
  instead of the provider.

  Resume is free. An output file that already exists is never regenerated,
  and its transcript is never re-purchased, so a run that dies at scenario 34
  of 50 costs nothing to finish.

  Budget. A pre-flight estimate before the first call, and a running total
  checked before every call after that.
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import checks as checks_mod
from .adapters import AdapterError, GenRequest, Timeout, build_adapter, params_unsupported
from .asr import Asr, AsrFailure
from .cost import Usage, compute_cost, estimate_scenario_cost
from .matrix import Cell, Matrix
from .mos import QualityPredictor
from .telemetry import RunPaths, Telemetry

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 45.0
# Hard wall-clock ceiling per attempt, enforced by the runner itself rather
# than trusted to the provider SDK. On 2026-09-01 a Gemini call sat in
# CLOSE_WAIT for 98 minutes at 0% CPU: the peer had hung up and the SDK's own
# read timeout never fired, because the hang was inside the OAuth token
# refresh, which has its own transport and its own (absent) timeout. One cell
# held eleven finished clips hostage. A deadline the runner owns cannot be
# defeated by a layer underneath it not having one.
DEADLINE_GRACE_S = 30.0
# When a provider bills per audio-minute we need a duration BEFORE the call to
# estimate; this is the pre-flight assumption only, never a recorded cost.
PREFLIGHT_SECONDS_PER_100_CHARS = 6.5


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Running total in micro-USD, checked before every billable call."""

    def __init__(self, cap_usd: float | None) -> None:
        self.cap_micro = int(cap_usd * 1_000_000) if cap_usd is not None else None
        self._spent = 0
        self._lock = threading.Lock()

    @property
    def spent_micro(self) -> int:
        with self._lock:
            return self._spent

    def add(self, micro: int) -> None:
        with self._lock:
            self._spent += micro

    def guard(self, what: str) -> None:
        if self.cap_micro is None:
            return
        with self._lock:
            if self._spent >= self.cap_micro:
                raise BudgetExceeded(
                    f"budget cap ${self.cap_micro / 1e6:.2f} reached "
                    f"(spent ${self._spent / 1e6:.4f}) - stopping before {what}"
                )


class ProviderLanes:
    """A semaphore per provider, plus an optional requests-per-minute pacer."""

    def __init__(self, models) -> None:
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        self._rpm: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._pace_lock = threading.Lock()
        for m in models:
            if m.provider not in self._sems:
                self._sems[m.provider] = threading.BoundedSemaphore(m.limits.max_concurrency)
            if m.limits.rpm:
                # Two models on one vendor share the lane; the tighter pace wins.
                gap = 60.0 / m.limits.rpm
                self._rpm[m.provider] = max(self._rpm.get(m.provider, 0.0), gap)

    def _pace(self, provider: str) -> None:
        gap = self._rpm.get(provider)
        if not gap:
            return
        with self._pace_lock:
            now = time.monotonic()
            earliest = self._last.get(provider, 0.0) + gap
            wait = earliest - now
            self._last[provider] = max(now, earliest)
        if wait > 0:
            time.sleep(wait)

    def run(self, provider: str, fn: Callable[[], Any]) -> Any:
        sem = self._sems.setdefault(provider, threading.BoundedSemaphore(2))
        with sem:
            self._pace(provider)
            return fn()


@dataclass
class CellOutcome:
    cell: Cell
    status: str
    attempts: int
    output_path: Path | None = None
    transcript_path: Path | None = None
    latency_ms: int | None = None
    cost_micro: int = 0
    asr_cost_micro: int = 0
    check_report: Any = None
    error: str | None = None
    resumed: bool = False
    voice_used: tuple[str | None, str | None] = (None, None)
    params_unsupported: list[str] = field(default_factory=list)


def preflight_estimate(matrix: Matrix, asr_price=None) -> tuple[int, list[str]]:
    """Estimated micro-USD for the whole run. A guess, printed and never stored."""
    total = 0
    notes: list[str] = []
    for c in matrix.cells:
        chars = len(c.scenario.text)
        seconds = chars / 100.0 * PREFLIGHT_SECONDS_PER_100_CHARS
        total += estimate_scenario_cost(chars, c.model.price, seconds)
        if asr_price is not None:
            total += estimate_scenario_cost(chars, asr_price, seconds)
    notes.append(
        f"assumes ~{PREFLIGHT_SECONDS_PER_100_CHARS:g}s of speech per 100 characters; "
        f"replaced by measured duration once each clip exists"
    )
    return total, notes


def call_with_deadline(fn: Callable[[], Any], deadline_s: float, label: str) -> Any:
    """
    Run `fn` with a wall-clock ceiling the RUNNER owns.

    Python cannot kill a thread, so a call that blows the deadline is
    ABANDONED, not cancelled: the worker runs on a daemon thread, we stop
    waiting for it, and the interpreter will not block on it at exit. The
    abandoned thread may still be holding a socket, which is untidy - but a
    leaked socket that the run reports as a timeout is strictly better than a
    run that never finishes and reports nothing at all.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True, name=f"cell:{label}")
    thread.start()
    thread.join(deadline_s)
    if thread.is_alive():
        raise Timeout(
            f"{label}: exceeded the runner's {deadline_s:.0f}s wall-clock deadline and was "
            f"abandoned. The provider SDK's own timeout did not fire, which usually means the "
            f"hang is below it (an auth token refresh, or a socket the peer closed without "
            f"telling us)."
        )
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after:
        return min(BACKOFF_MAX_S, retry_after + random.uniform(0, 2.0))
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S**attempt + random.uniform(0, 3.0))


def _generate_one(
    cell: Cell,
    paths: RunPaths,
    tel: Telemetry,
    lanes: ProviderLanes,
    budget: Budget,
    timeout_s: float,
    log,
) -> CellOutcome:
    scenario, model = cell.scenario, cell.model
    ext = "wav"
    try:
        adapter = build_adapter(model)
        ext = getattr(adapter, "ext", "wav")
    except Exception as exc:  # noqa: BLE001 - construction failure is a real cell failure
        tel.write(
            "telemetry",
            {
                "scenario_id": scenario.id,
                "model_id": model.id,
                "modality": scenario.modality,
                "task": scenario.task,
                "attempt": 1,
                "status": "auth_error" if "not set" in str(exc) else "provider_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return CellOutcome(cell, "failed", 1, error=f"{type(exc).__name__}: {exc}")

    out_path = paths.output_path(scenario.modality, scenario.id, model.id, ext)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume: an existing output is never regenerated and never re-billed.
    if out_path.exists() and out_path.stat().st_size > 0:
        log(f"  resume   {scenario.id:9} {model.id:26} (output already on disk)")
        tel.write(
            "telemetry",
            {
                "scenario_id": scenario.id,
                "model_id": model.id,
                "modality": scenario.modality,
                "task": scenario.task,
                "attempt": 0,
                "status": "resumed",
                "output": {"path": str(out_path.relative_to(paths.dir)), "bytes": out_path.stat().st_size},
                "cost": {"micro_usd": 0, "basis": "resumed - not re-billed"},
            },
        )
        return CellOutcome(cell, "ok", 0, output_path=out_path, resumed=True)

    voice_id, voice_logical = model.resolve_voice(scenario.params.get("voice"))
    requested = {**model.params, **scenario.params}
    req = GenRequest(
        task=scenario.task,
        text=scenario.text,  # by value; nothing below mutates it
        params=dict(scenario.params),
        voice_id=voice_id,
        voice_logical=voice_logical,
        language=scenario.language,
        style=scenario.style,
        timeout_s=timeout_s,
    )

    last_error = "unknown failure"
    last_status = "provider_error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget.guard(f"{scenario.id} x {model.id}")
        t0 = time.perf_counter()
        try:
            result = lanes.run(
                model.provider,
                lambda: call_with_deadline(
                    lambda: adapter.run(req),
                    req.timeout_s + DEADLINE_GRACE_S,
                    f"{scenario.id} x {model.id} attempt {attempt}",
                ),
            )
        except AdapterError as exc:
            latency = int((time.perf_counter() - t0) * 1000)
            last_error, last_status = str(exc), exc.status
            tel.write(
                "telemetry",
                {
                    "scenario_id": scenario.id,
                    "model_id": model.id,
                    "modality": scenario.modality,
                    "task": scenario.task,
                    "provider": model.provider,
                    "provider_model": model.provider_model,
                    "adapter": model.adapter,
                    "attempt": attempt,
                    "status": exc.status,
                    "latency_ms": latency,
                    "error": str(exc)[:1000],
                },
            )
            if not exc.retryable or attempt == MAX_ATTEMPTS:
                break
            time.sleep(_backoff(attempt, getattr(exc, "retry_after", None)))
            continue
        except Exception as exc:  # noqa: BLE001 - an adapter that leaked a raw error
            latency = int((time.perf_counter() - t0) * 1000)
            last_error, last_status = f"{type(exc).__name__}: {exc}", "provider_error"
            tel.write(
                "telemetry",
                {
                    "scenario_id": scenario.id,
                    "model_id": model.id,
                    "modality": scenario.modality,
                    "task": scenario.task,
                    "attempt": attempt,
                    "status": "provider_error",
                    "latency_ms": latency,
                    "error": last_error[:1000],
                },
            )
            if attempt == MAX_ATTEMPTS:
                break
            time.sleep(_backoff(attempt, None))
            continue

        # Latency is wall clock around the ADAPTER CALL only - our file
        # writing and our retries are excluded, or the number would measure
        # us rather than the provider.
        latency = int((time.perf_counter() - t0) * 1000)
        out_path.write_bytes(result.data)

        # Duration is measured off the file we just wrote, not requested, so
        # a per-minute biller is priced on what actually came back.
        try:
            facts = checks_mod.decode(out_path)
            duration_s = facts.duration_s
        except Exception:  # noqa: BLE001 - the checks stage will report it properly
            duration_s = None

        usage = Usage(
            reported=result.usage.reported,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            audio_out_tokens=result.usage.audio_out_tokens,
            audio_in_tokens=result.usage.audio_in_tokens,
            characters=result.usage.characters if result.usage.characters is not None else len(scenario.text),
            audio_seconds=duration_s,
            raw=result.usage.raw,
        )
        cost = compute_cost(usage, model.price, label=f"{model.id}/{scenario.id}")
        budget.add(cost.micro_usd)
        unsupported = params_unsupported(requested, result.applied_params)

        tel.write(
            "telemetry",
            {
                "scenario_id": scenario.id,
                "modality": scenario.modality,
                "task": scenario.task,
                "model_id": model.id,
                "provider": model.provider,
                "provider_model": model.provider_model,
                "provider_version": result.provider_version,
                "adapter": model.adapter,
                "attempt": attempt,
                "status": "ok",
                "params": requested,
                "params_unsupported": unsupported,
                "voice": {"logical": voice_logical, "provider_voice_id": voice_id},
                "latency_ms": latency,
                "request_id": result.provider_request_id,
                "output": {
                    "path": str(out_path.relative_to(paths.dir)),
                    "bytes": len(result.data),
                    "mime": result.mime,
                    "duration_s": round(duration_s, 3) if duration_s else None,
                },
                "usage": {k: v for k, v in usage.__dict__.items() if k != "raw" and v is not None},
                "usage_raw": usage.raw,
                "cost": cost.as_record,
            },
        )
        log(
            f"  ok       {scenario.id:9} {model.id:26} "
            f"{latency / 1000:5.1f}s  {duration_s or 0:5.1f}s audio  "
            f"${cost.micro_usd / 1e6:.5f}{'' if cost.usage_exact else ' (est)'}"
        )
        return CellOutcome(
            cell,
            "ok",
            attempt,
            output_path=out_path,
            latency_ms=latency,
            cost_micro=cost.micro_usd,
            voice_used=(voice_logical, voice_id),
            params_unsupported=unsupported,
        )

    log(f"  FAIL     {scenario.id:9} {model.id:26} {last_status}: {last_error[:80]}")
    return CellOutcome(cell, last_status, MAX_ATTEMPTS, error=last_error)


def measure_cell(
    outcome: CellOutcome,
    paths: RunPaths,
    tel: Telemetry,
    asr: Asr | None,
    predictor: QualityPredictor,
    budget: Budget,
    lanes: ProviderLanes | None,
    log,
) -> CellOutcome:
    """
    Layer 2: transcript, WER, audio quality, gates. Runs after generation and
    before any judging, so a broken clip never reaches a paid judge call.
    """
    scenario, model = outcome.cell.scenario, outcome.cell.model
    if outcome.output_path is None:
        return outcome

    transcript: str | None = None
    asr_error: str | None = None
    tpath = paths.transcript_path(scenario.modality, scenario.id, model.id)

    if tpath.exists() and tpath.read_text(encoding="utf-8").strip():
        # Resume: a transcript already bought is never bought again.
        transcript = tpath.read_text(encoding="utf-8").strip()
    elif asr is None:
        asr_error = "no ASR configured"
    else:
        try:
            duration = checks_mod.decode(outcome.output_path).duration_s
        except Exception as exc:  # noqa: BLE001
            duration = 0.0
            asr_error = f"could not decode audio for ASR: {exc}"
        if asr_error is None:
            budget.guard(f"ASR for {scenario.id} x {model.id}")
            # Through the provider lane, and under the same runner-owned
            # deadline as generation: the ASR backend is a network call like
            # any other and must not be the thing that hangs a cell.
            label = f"asr {scenario.id} x {model.id}"

            def _do_asr():
                return asr.transcribe(outcome.output_path, duration, scenario.language)

            def _guarded():
                return call_with_deadline(_do_asr, 180.0 + DEADLINE_GRACE_S, label)

            try:
                res = (
                    lanes.run(asr.spec.provider, _guarded) if lanes is not None else _guarded()
                )
                if getattr(res, "repeat_collapsed", False):
                    log(f"  ASR-FIX  {scenario.id:9} {model.id:26} "
                        f"transcriber repeated itself; the restatement was removed")
            except Timeout as exc:
                res = AsrFailure(error=str(exc), attempts=1, provider_model=asr.spec.provider_model)
            if isinstance(res, AsrFailure):
                asr_error = res.error
                tel.write(
                    "telemetry",
                    {
                        "scenario_id": scenario.id,
                        "model_id": model.id,
                        "step": "asr",
                        "status": "asr_failed",
                        "attempts": res.attempts,
                        "error": res.error[:1000],
                    },
                )
            else:
                transcript = res.text
                # The RAW transcript is kept beside the audio as evidence.
                # Never cleaned, never normalised on disk.
                tpath.write_text(res.text + "\n", encoding="utf-8")
                budget.add(res.cost.micro_usd)
                outcome.asr_cost_micro = res.cost.micro_usd
                tel.write(
                    "telemetry",
                    {
                        "scenario_id": scenario.id,
                        "model_id": model.id,
                        "step": "asr",
                        "status": "ok",
                        "provider_model": res.provider_model,
                        "attempts": res.attempts,
                        "latency_ms": res.latency_ms,
                        "transcript_path": str(tpath.relative_to(paths.dir)),
                        "repeat_collapsed": res.repeat_collapsed,
                        "cost": res.cost.as_record,
                    },
                )

    report = checks_mod.run_checks(
        scenario, model.id, outcome.output_path, transcript, asr_error, predictor
    )
    outcome.check_report = report
    outcome.transcript_path = tpath if transcript else None
    tel.write("checks", report.as_record)

    verdict = "pass" if report.passed else "FAIL " + ",".join(report.failed_gates)
    wer_txt = (
        f"wer={report.measurements['normalized_wer']:.4f}"
        if "normalized_wer" in report.measurements
        else "wer=unmeasured"
    )
    q = report.measurements.get("audio_quality_1_5")
    log(f"  checks   {scenario.id:9} {model.id:26} {verdict:28} {wer_txt}  q={q}")
    return outcome


def run_generation(
    matrix: Matrix,
    paths: RunPaths,
    tel: Telemetry,
    asr: Asr | None,
    predictor: QualityPredictor,
    budget: Budget,
    workers: int = 4,
    timeout_s: float = 180.0,
    log=print,
) -> list[CellOutcome]:
    lanes = ProviderLanes([c.model for c in matrix.cells])
    outcomes: list[CellOutcome] = []
    aborted = False

    def process(cell: Cell) -> CellOutcome:
        """
        Generate AND measure one cell, in one worker.

        These used to be two phases with a barrier between them, which meant a
        single hung generation held every finished clip's transcript hostage -
        exactly what happened on 2026-09-01. Measurement now follows its own
        cell immediately, so a stuck cell costs one cell.

        The ASR call goes through the SAME provider lane as generation, which
        is what made the barrier look necessary in the first place: the lane
        semaphore is the thing that stops us hammering the vendor, and it
        works just as well here.
        """
        outcome = _generate_one(cell, paths, tel, lanes, budget, timeout_s, log)
        if outcome.status != "ok":
            return outcome
        try:
            return measure_cell(outcome, paths, tel, asr, predictor, budget, lanes, log)
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - a measurement failure is not a lost clip
            log(f"  measure  {cell.scenario.id:9} {cell.model.id:26} FAILED {type(exc).__name__}: {exc}")
            outcome.error = f"measurement failed: {exc}"
            return outcome

    # SCENARIO AT A TIME, ALL MODELS IN PARALLEL WITHIN IT.
    #
    # The cells used to fan out flat, which meant one model could finish a
    # scenario while the other was still going or had failed - leaving a
    # half-answered scenario that LOOKS like data. It is not: a paired
    # comparison needs both arms on the identical input, and one arm alone
    # tells you nothing about which model is better.
    #
    # So the unit of completion is the SCENARIO. Its models run concurrently
    # (that is the parallelism worth having - same input, same moment, same
    # network conditions), and the scenario is reported complete only when
    # every model finished. A scenario with a failed arm is announced as
    # incomplete rather than quietly contributing one usable row.
    by_scenario: dict[str, list[Cell]] = {}
    for cell in matrix.cells:
        by_scenario.setdefault(cell.scenario.id, []).append(cell)

    for sid in sorted(by_scenario):
        group = by_scenario[sid]
        if aborted:
            log(f"  skipped  {sid:12} budget cap already reached")
            continue
        log(f"\n  scenario {sid}  ({len(group)} models)")
        got: list[CellOutcome] = []
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(group)))) as pool:
            futures = {pool.submit(process, cell): cell for cell in group}
            for fut in as_completed(futures):
                cell = futures[fut]
                try:
                    got.append(fut.result())
                except BudgetExceeded as exc:
                    aborted = True
                    log(f"  BUDGET   {exc}")
                except Exception as exc:  # noqa: BLE001
                    log(f"  ERROR    {cell.key}: {type(exc).__name__}: {exc}")
                    got.append(CellOutcome(cell, "provider_error", MAX_ATTEMPTS, error=str(exc)))

        outcomes.extend(got)
        done = [o for o in got if o.status == "ok"]
        if len(done) == len(group):
            log(f"  COMPLETE {sid:12} all {len(group)} models")
        else:
            missing = sorted(o.cell.model.id for o in got if o.status != "ok")
            log(f"  INCOMPLETE {sid:10} {len(done)}/{len(group)} models - missing {missing}")
            tel.write(
                "telemetry",
                {
                    "scenario_id": sid,
                    "step": "scenario",
                    "status": "incomplete",
                    "models_done": len(done),
                    "models_expected": len(group),
                    "missing": missing,
                    "note": "a scenario answered by only some models is not comparable "
                            "and must not be read as a result for the models that did finish",
                },
            )

    if aborted:
        tel.write("telemetry", {"step": "run", "status": "aborted", "reason": "budget cap reached"})

    return sorted(outcomes, key=lambda o: (o.cell.scenario.id, o.cell.model.id))
