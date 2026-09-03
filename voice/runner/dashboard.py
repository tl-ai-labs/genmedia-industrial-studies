"""
The GenMedia runs dashboard - one page across every run.

WHY THIS EXISTS SEPARATELY FROM report.py.
`report.py` renders ONE run: its clips, its gates, its judge reasoning. That is
the evidence surface, and it is deliberately self-contained so a run folder can
be zipped and mailed. But three runs of the same scenario produce three
disconnected reports and no way to see the thing that matters most about
repeated runs - how much the numbers move between them. This file is that view.

DESIGN REFERENCE, NOT A COPY.
The visual language follows apps/dashboard/DESIGN_SYSTEM.md: Space Grotesk for
display, IBM Plex Sans for body, IBM Plex Mono for identifiers and labels;
three grays and no more; one accent used only on the active tab and primary
links; status colour confined to pills; per-model colour used ONLY where it
identifies a model in a bar, never as decoration. None of that app's code is
imported or copied - it is a React/Tailwind app and this is one static file
with no build step, which is the constraint the plan sets for this project.

WHAT IT DELIBERATELY DOES NOT DO.
No blending of quality, cost, latency and reliability into one number. No
winner where the runs say tie. Absent measurements render as a dash and are
counted, never zero-filled.
"""

from __future__ import annotations

import html
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cost import fmt_usd
from .telemetry import RunPaths, incomplete_scenarios

# Per-model accent, assigned in load order. Used ONLY inside spread bars and
# the model legend, where the colour identifies a model. Never on card chrome.
MODEL_ACCENTS = ("#1F5081", "#87590B", "#1B6A49", "#6D3080", "#8D2A23")


@dataclass
class Cell:
    """One clip: scenario x model x run."""

    run_id: str
    run_label: str
    scenario_id: str
    model_id: str
    task: str
    status: str
    score: float | None
    wer: float | None
    duration_s: float | None
    latency_ms: int | None
    cost_micro: int
    asr_micro: int
    judge_micro: int
    audio_q: float | None
    audio_q_is_mos: bool
    gates_passed: int
    gates_total: int
    attempts: int
    cost_exact: bool
    voice: str | None
    blind_label: str | None
    calibration_trusted: bool
    criterion_scores: dict[str, float] = field(default_factory=dict)
    audio_rel: str | None = None
    # sha256 of the scenario INPUT as this run froze it. Two cells are
    # repeats of each other only if this matches - see rollup_models().
    scenario_hash: str = ""
    # What the Evidence tab needs to show the clip beside what it produced.
    gates: list[dict[str, Any]] = field(default_factory=list)
    transcript: str | None = None

    @property
    def total_micro(self) -> int:
        return self.cost_micro + self.asr_micro + self.judge_micro


@dataclass
class RunSummary:
    run_id: str
    label: str
    started_at: str
    modality: str
    scenario_count: int
    model_ids: list[str]
    judge_model: str
    calibration_passed: bool
    calibration_reason: str
    mos_predictor: str
    git_sha: str
    cells: list[Cell] = field(default_factory=list)

    @property
    def total_micro(self) -> int:
        return sum(c.total_micro for c in self.cells)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_runs(runs_root: Path, modality: str | None = None) -> list[RunSummary]:
    """Every run directory that carries a manifest, newest last."""
    out: list[RunSummary] = []
    for d in sorted(p for p in Path(runs_root).iterdir() if p.is_dir()):
        man_path = d / "manifest.json"
        if not man_path.exists():
            continue
        man = json.loads(man_path.read_text(encoding="utf-8"))
        if modality and man.get("modality") != modality:
            continue

        checks = {f"{r['scenario_id']}|{r['model_id']}": r for r in _read_jsonl(d / "checks.jsonl")}
        judges = {f"{r['scenario_id']}|{r['model_id']}": r for r in _read_jsonl(d / "judge.jsonl")}
        scores = {f"{r['scenario_id']}|{r['model_id']}": r for r in _read_jsonl(d / "scores.jsonl")}

        gen: dict[str, dict[str, Any]] = {}
        asr_cost: dict[str, int] = {}
        attempts: dict[str, int] = {}
        for row in _read_jsonl(d / "telemetry.jsonl"):
            sid, mid = row.get("scenario_id"), row.get("model_id")
            if not sid or not mid:
                continue
            key = f"{sid}|{mid}"
            if row.get("step") == "asr":
                asr_cost[key] = asr_cost.get(key, 0) + int((row.get("cost") or {}).get("micro_usd", 0))
                continue
            if row.get("attempt", 0) >= 1:
                attempts[key] = attempts.get(key, 0) + 1
            if row.get("status") in ("ok", "resumed"):
                gen[key] = row

        cal = man.get("calibration") or {}
        run = RunSummary(
            run_id=man["run_id"],
            label=man["run_id"].split("_")[-1],
            started_at=man.get("started_at", ""),
            modality=man.get("modality", "voice"),
            scenario_count=int(man.get("scenario_count", 0)),
            model_ids=[m["id"] for m in man.get("models", [])],
            judge_model=(man.get("judge") or {}).get("provider_model", "—"),
            calibration_passed=bool(cal.get("passed")),
            calibration_reason=str(cal.get("reason", "not evaluated")),
            mos_predictor=(man.get("mos") or {}).get("predictor", "—"),
            git_sha=man.get("git_sha", "—"),
        )

        # Same rule as the report: a scenario not every model answered is a
        # gap, and must not contribute a score to the arm that finished.
        partial = incomplete_scenarios(RunPaths(Path(runs_root), d.name))

        voices = {m["id"]: m.get("voice_map", {}) for m in man.get("models", [])}
        # The manifest freezes a hash per scenario. It is what makes "the same
        # scenario, run twice" a checkable claim rather than a naming
        # convention: an id is reused across edits, a hash is not.
        scen_hash = {sc.get("id"): (sc.get("hash") or "") for sc in (man.get("scenarios") or [])}
        for key, crec in checks.items():
            sid, mid = key.split("|", 1)
            srec = scores.get(key, {})
            jrec = judges.get(key, {})
            grow = gen.get(key, {})
            meas = crec.get("measurements", {})
            gates = crec.get("gates", [])
            gcfg = (grow.get("cost") or {})
            vinfo = grow.get("voice") or {}
            ext = "wav"
            run.cells.append(
                Cell(
                    run_id=run.run_id,
                    run_label=run.label,
                    scenario_id=sid,
                    model_id=mid,
                    task=srec.get("task", "—"),
                    status="incomplete" if sid in partial else srec.get("status", "unknown"),
                    score=None if sid in partial else srec.get("score"),
                    wer=meas.get("normalized_wer"),
                    duration_s=meas.get("duration_s"),
                    latency_ms=grow.get("latency_ms"),
                    cost_micro=int(gcfg.get("micro_usd", 0)),
                    asr_micro=asr_cost.get(key, 0),
                    judge_micro=int((jrec.get("cost") or {}).get("micro_usd", 0)),
                    scenario_hash=scen_hash.get(sid, ""),
                    gates=gates,
                    transcript=crec.get("transcript_raw"),
                    audio_q=meas.get("audio_quality_1_5"),
                    audio_q_is_mos=bool(meas.get("audio_quality_is_mos")),
                    gates_passed=sum(1 for g in gates if g.get("passed")),
                    gates_total=len(gates),
                    attempts=attempts.get(key, 0),
                    cost_exact=gcfg.get("usage_exact") is not False,
                    voice=(vinfo.get("provider_voice_id") or voices.get(mid, {}).get("female_mid_warm")),
                    blind_label=jrec.get("blind_label"),
                    calibration_trusted=bool(srec.get("calibration_trusted", True)),
                    criterion_scores=srec.get("criterion_scores", {}),
                    audio_rel=f"{run.run_id}/outputs/{run.modality}/{sid}/{mid}.{ext}",
                )
            )
        out.append(run)
    return out


@dataclass
class ModelRollup:
    model_id: str
    accent: str
    n: int
    scores: list[float]
    costs: list[int]
    latencies: list[int]
    durations: list[float]
    wers: list[float]
    audio_qs: list[float]
    ok: int
    attempts: list[int]
    unjudged: int
    invalid: int
    # How many cells the mean is actually over, against how many were
    # EVALUATED. A cell from a run that died partway (status "incomplete")
    # is not a model outcome - the credential outage on 2026-09-03 left one
    # arm with two such cells and the other with none, and counting them made
    # the two models' denominators differ for a reason neither model caused.
    scored_n: int = 0
    evaluated_n: int = 0
    # (scenario_id, scenario_hash) -> every score that exact scenario
    # produced, one per run. This is what separates the two kinds of spread
    # below.
    #
    # THE HASH IS PART OF THE KEY ON PURPOSE. A scenario id is reused across
    # edits - vr-game-02 was run, found to carry an unpassable gate, fixed,
    # and run again under the same id. Keyed on the id alone those two runs
    # look like a repeat, and their difference would be reported as this
    # machine's NOISE FLOOR when it is really the size of an edit we made.
    # That number is the one every "is this gap real" verdict divides by, so
    # poisoning it would quietly corrupt every comparison on the board.
    by_scenario: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    def _stat(self, xs: list[float]):
        if not xs:
            return None, None, None
        return (
            statistics.mean(xs),
            min(xs),
            max(xs),
        )

    @property
    def mean_score(self):
        """Quality over the cells that were actually scored. Never read this
        without `scored_n`/`n` beside it - see the note in rollup_models."""
        return statistics.mean(self.scores) if self.scores else None

    @property
    def gate_pass_rate(self):
        """How often this model produced a clip that cleared its gates.

        The other half of the answer. Quality says how good the output is
        when it works; this says how often it works, and a model is ranked
        on this first so that failing more can never look like scoring
        higher.
        """
        base = self.evaluated_n or self.n
        return (base - self.invalid) / base if base else 0.0

    @property
    def score_spread(self):
        """
        Total spread, best to worst, across everything this model scored.

        DO NOT read this as run-to-run noise. With one scenario per run it
        mixes two different things - how much a model varies when asked the
        SAME thing twice, and how differently it handles DIFFERENT scenarios.
        The two properties below separate them, and only the first is the
        machine's noise floor.
        """
        return (max(self.scores) - min(self.scores)) if len(self.scores) > 1 else 0.0

    @property
    def repeat_spread(self):
        """
        Worst within-scenario spread: the same script, asked more than once.

        THIS is the noise floor - the number that says whether a gap between
        two models means anything. Returns None when no scenario has been run
        twice, because with a single sample there is no noise to measure and
        reporting 0.0 would claim perfect consistency we have not observed.

        "The same script" means byte-identical INPUT, enforced by the frozen
        hash in the key - not merely the same scenario id.
        """
        repeats = [max(v) - min(v) for v in self.by_scenario.values() if len(v) > 1]
        return max(repeats) if repeats else None

    @property
    def repeated_scenarios(self):
        return sum(1 for v in self.by_scenario.values() if len(v) > 1)

    @property
    def scenario_spread(self):
        """Spread of per-scenario MEANS - how differently it handles different work."""
        import statistics as _st
        means = [_st.mean(v) for v in self.by_scenario.values() if v]
        return (max(means) - min(means)) if len(means) > 1 else 0.0

    @property
    def mean_cost(self):
        return statistics.mean(self.costs) if self.costs else 0

    @property
    def mean_latency(self):
        return statistics.mean(self.latencies) if self.latencies else None

    @property
    def mean_audio_q(self):
        return statistics.mean(self.audio_qs) if self.audio_qs else None

    @property
    def worst_wer(self):
        return max(self.wers) if self.wers else None

    @property
    def success_rate(self):
        return self.ok / self.n if self.n else 0.0

    @property
    def mean_attempts(self):
        return statistics.mean(self.attempts) if self.attempts else None


def rollup_models(runs: list[RunSummary]) -> list[ModelRollup]:
    by: dict[str, list[Cell]] = {}
    for r in runs:
        for c in r.cells:
            by.setdefault(c.model_id, []).append(c)
    out: list[ModelRollup] = []
    for i, (mid, cells) in enumerate(sorted(by.items())):
        # A cell that failed its gates carries score 0.0 and status "invalid".
        # Averaging those into the quality mean answers no question anyone
        # asks: on 2026-09-03 ten of sixteen cells were gated on TIMING, and
        # the board read 2.4/10 for two models that had tied at ~9.8 on the
        # one scenario they both cleared. Worse, the zeros came from fitness
        # gates - a perfectly clean read that was 2.5s too long for an ad slot
        # is not a quality failure, it is the wrong length.
        #
        # So: quality is meaned over cells that were SCORED, and the count of
        # invalid cells is carried beside it, never folded into it. This is
        # the same rule `unjudged` already follows - unmeasured is not zero.
        # The denominator travels with the number everywhere it is rendered,
        # because a 9.7 over one cell and a 9.7 over eight are not the same
        # claim, and a model must not look better for having failed more.
        counted = [c for c in cells if c.score is not None and c.status != "invalid"]
        per_scenario: dict[tuple[str, str], list[float]] = {}
        for c in counted:
            per_scenario.setdefault((c.scenario_id, c.scenario_hash), []).append(c.score)
        out.append(
            ModelRollup(
                model_id=mid,
                accent=MODEL_ACCENTS[i % len(MODEL_ACCENTS)],
                n=len(cells),
                scores=[c.score for c in counted],
                costs=[c.total_micro for c in cells if c.total_micro],
                latencies=[c.latency_ms for c in cells if c.latency_ms],
                durations=[c.duration_s for c in cells if c.duration_s],
                wers=[c.wer for c in cells if c.wer is not None],
                audio_qs=[c.audio_q for c in cells if c.audio_q is not None],
                ok=sum(1 for c in cells if c.status in ("scored", "invalid")),
                attempts=[c.attempts for c in cells if c.attempts],
                unjudged=sum(1 for c in cells if c.status == "unjudged"),
                invalid=sum(1 for c in cells if c.status == "invalid"),
                scored_n=len(counted),
                evaluated_n=sum(1 for c in cells if c.status != "incomplete"),
                by_scenario=per_scenario,
            )
        )
    # Rank on the GATE first, quality only within the set that cleared it.
    # Meaning quality over scored cells alone would otherwise reward failing:
    # a model that produced one usable clip out of eight, scoring 9.9, would
    # outrank one that produced eight at 9.5. Same rule the study console
    # applies to runs - cheapest is never best unless it verified.
    out.sort(key=lambda m: (-m.gate_pass_rate, m.mean_score is None, -(m.mean_score or 0)))
    return out


def _e(v: Any) -> str:
    return html.escape(str(v), quote=True)


def _num(v, fmt="{:.2f}", dash="—"):
    return dash if v is None else fmt.format(v)




# ---------------------------------------------------------------------------
# Rendering. The data above is the whole truth; this half only arranges it.
#
# The markup follows the image lane's report (image/runner/templates) so the
# two modalities read as one product - same tokens, same tiles, same duel
# strip, same "four columns, never one number" framing. What differs is what
# a voice lane actually has to say: a REPEATS tab, because a spoken clip is
# not reproducible the way a rendered image is, and evidence you LISTEN to
# rather than look at.
# ---------------------------------------------------------------------------

def _fmt_usd(micro: int | float | None) -> str:
    if not micro:
        return "$0.00"
    d = float(micro) / 1e6
    return f"${d:,.4f}" if d < 0.01 else f"${d:,.2f}"


def _fmt_s(ms: float | None) -> str:
    return "—" if not ms else f"{float(ms) / 1000:.1f}s"


def _bar_widths(a: float | None, b: float | None, lower_is_better: bool):
    """Two bars sharing a scale, and which side won. None means no bar."""
    if a is None or b is None:
        return 0, 0, None
    top = max(abs(a), abs(b)) or 1.0
    aw, bw = abs(a) / top * 100, abs(b) / top * 100
    if a == b:
        win = None
    elif lower_is_better:
        win = "a" if a < b else "b"
    else:
        win = "a" if a > b else "b"
    return aw, bw, win


def _duel(models: list[ModelRollup]) -> dict[str, Any] | None:
    """Head to head, but only between the top two - three-way bars lie."""
    if len(models) < 2:
        return None
    a, b = models[0], models[1]
    spec = [
        ("Quality (scored cells)", a.mean_score, b.mean_score, False, "{:.3f}"),
        ("Gates passed", a.gate_pass_rate * 100, b.gate_pass_rate * 100, False, "{:.0f}%"),
        ("Run-to-run spread", a.repeat_spread, b.repeat_spread, True, "±{:.3f}"),
        ("Worst WER", a.worst_wer, b.worst_wer, True, "{:.4f}"),
        ("Cost per clip", a.mean_cost, b.mean_cost, True, None),
        ("Latency (mean)", a.mean_latency, b.mean_latency, True, None),
    ]
    metrics = []
    for label, av, bv, lower, fmt in spec:
        aw, bw, win = _bar_widths(av, bv, lower)
        if fmt is None:
            fa = _fmt_usd(av) if "Cost" in label else _fmt_s(av)
            fb = _fmt_usd(bv) if "Cost" in label else _fmt_s(bv)
        else:
            fa = fmt.format(av) if av is not None else "—"
            fb = fmt.format(bv) if bv is not None else "—"
        metrics.append({"label": label, "a": fa, "b": fb, "aw": aw, "bw": bw, "win": win})
    return {"a": a.model_id, "b": b.model_id, "metrics": metrics,
            "basis": f"{a.scored_n + b.scored_n} scored cells"}


def _scenario_blocks(runs: list[RunSummary], duel: dict | None) -> list[dict[str, Any]]:
    """
    One block per scenario: every pass side by side, each model's own spread,
    and the verdict that spread licenses.

    A repeat is the same SCRIPT and the same GATES. Runs of an edited scenario
    are excluded and counted, because their difference is the size of an edit
    rather than the machine's noise - and that number is what every verdict
    here divides by.
    """
    out: list[dict[str, Any]] = []
    sids = sorted({c.scenario_id for r in runs for c in r.cells})
    for sid in sids:
        # SCORED means scored. A gated cell carries 0.0, and letting it in
        # here made vr-ads-06 read "+9.650, larger than noise" - one model's
        # real score minus the other's failure. Same rule as rollup_models.
        def _scored(c, _sid=sid):
            return c.scenario_id == _sid and c.score is not None and c.status != "invalid"

        def _here(c, _sid=sid):
            return c.scenario_id == _sid

        # PASSES counts how often the scenario was RUN, not how often it
        # scored. A scenario run twice that was gated both times is not
        # "0 passes" - it ran twice and produced nothing, which is a
        # different and more useful thing to say.
        cand = [r for r in runs if any(_here(c) for c in r.cells)]

        def _defhash(r, _sid=sid):
            return next((c.scenario_hash for c in r.cells if _here(c)), "")

        newest = _defhash(cand[-1]) if cand else ""
        sruns = [r for r in cand if _defhash(r) == newest]
        stale = len(cand) - len(sruns)

        # KEYED ON run_id, NOT label. A label is run_id.split("_")[-1], so two
        # runs of one scenario share it - "voice-vr-game-02" names four
        # different runs here. Keyed on the label they collapsed into one
        # entry and the same score rendered in two columns, which looked like
        # a model reproducing itself perfectly when it had been asked once.
        by_model: dict[str, dict[str, float]] = {}
        for r in sruns:
            for c in r.cells:
                if _scored(c):
                    by_model.setdefault(c.model_id, {})[r.run_id] = c.score

        keys = [r.run_id for r in sruns]
        short = [(r.started_at or r.run_id)[5:16].replace("T", " ") for r in sruns]
        rows, own = [], {}
        for mid in sorted(by_model):
            vals = by_model[mid]
            got = [vals[k] for k in keys if k in vals]
            own[mid] = (max(got) - min(got)) if len(got) > 1 else None
            rows.append({"model_id": mid,
                         # NOT "values": Jinja resolves dict.values to the
                         # method, not the key, and the template silently
                         # iterates a bound method instead of the scores.
                         "pass_scores": [vals.get(k) for k in keys],
                         "spread": own[mid]})

        gap = floor = None
        gaps: list[float | None] = []
        if not by_model:
            verdict = "No score"
            detail = (f"Ran {len(keys)} time{'' if len(keys) == 1 else 's'}; every cell failed "
                      f"its gates, so there is nothing to compare. The failures are in Evidence.")
        else:
            verdict, detail = "Not measured", "No scored cell on this scenario yet."
        ids = sorted(by_model)
        if len(ids) >= 2 and duel:
            a, b = duel["a"], duel["b"]
            if a in by_model and b in by_model:
                gaps = [(by_model[a][k] - by_model[b][k])
                        if k in by_model[a] and k in by_model[b] else None for k in keys]
                real = [g for g in gaps if g is not None]
                gap = statistics.mean(real) if real else None
                floors = [v for v in own.values() if v is not None]
                floor = max(floors) if floors else None
                if gap is not None and floor is not None:
                    if abs(gap) <= floor:
                        verdict = "Inside the noise"
                        detail = (f"The gap ({gap:+.3f}) is no bigger than the spread one model "
                                  f"shows against itself (±{floor:.3f}). This scenario does not "
                                  f"separate them.")
                    elif abs(gap) <= 2 * floor:
                        verdict = "Marginal"
                        detail = (f"The gap ({gap:+.3f}) clears the noise floor (±{floor:.3f}) "
                                  f"but not by much. More repeats before quoting it.")
                    else:
                        verdict = "Larger than noise"
                        detail = (f"The gap ({gap:+.3f}) is over twice the noise floor "
                                  f"(±{floor:.3f}), so it is unlikely to be run-to-run variation.")
                elif gap is not None:
                    verdict = "Single pass"
                    detail = ("One measurement per model, so there is no noise floor to compare "
                              "the gap against. Run it again.")
        elif len(ids) == 1:
            verdict, detail = "One arm only", "Only one model produced a scored cell here."

        out.append({"id": sid, "n_passes": len(keys), "labels": short, "rows": rows,
                    "gaps": gaps, "gap": gap, "floor": floor, "verdict": verdict,
                    "detail": detail, "stale": stale})
    return out


def _clip(c: Cell, lead: bool = False) -> dict[str, Any]:
    return {"model_id": c.model_id, "run_label": c.run_label, "score": c.score,
            "status": c.status, "wer": c.wer, "duration_s": c.duration_s,
            "audio_rel": c.audio_rel, "transcript": c.transcript,
            "gates": c.gates, "lead": lead}


def _median_cell(cells: list[Cell]) -> Cell:
    """
    The clip that LEADS is the median one, never the best.

    A best-of-N sample flatters the model and quietly disagrees with the mean
    shown one tab over - the reader hears the good take and reads the average
    of all of them. Ranking puts unscored cells last so a gated clip is never
    chosen to represent a model that also produced usable ones.
    """
    ranked = sorted(cells, key=lambda c: (c.score is None, c.score or 0.0))
    return ranked[len(ranked) // 2]


def _evidence(runs: list[RunSummary]) -> list[dict[str, Any]]:
    """
    Grouped by scenario. One representative clip per model leads; the rest
    stay on the page behind a toggle, because they are the evidence the
    spread figure rests on and hiding them entirely would make that number
    unfalsifiable.
    """
    by_sid: dict[str, dict[str, list[Cell]]] = {}
    for r in runs:
        for c in r.cells:
            by_sid.setdefault(c.scenario_id, {}).setdefault(c.model_id, []).append(c)

    blocks = []
    for sid in sorted(by_sid):
        leads, more = [], []
        for mid in sorted(by_sid[sid]):
            cells = by_sid[sid][mid]
            rep = _median_cell(cells)
            leads.append(_clip(rep, lead=True))
            more.extend(_clip(c) for c in cells if c is not rep)
        blocks.append({"scenario_id": sid, "title": f"{len(leads) + len(more)} clips",
                       "leads": leads, "more": more, "n_more": len(more)})
    return blocks


def _overall(models: list[ModelRollup]) -> dict[str, Any]:
    """
    Whether the top two are separated AT ALL, judged against measured noise.

    This replaces a fixed 0.5-point band that was a rule of thumb chosen
    before any run - it named winners on gaps nothing had shown to be real.
    The floor here is the larger of the two models' own run-to-run spreads,
    and no winner is named until a gap clears twice it.
    """
    if len(models) < 2:
        return {"verdict": "Single model", "detail": "Nothing to compare.", "winner": None}
    a, b = models[0], models[1]
    if a.mean_score is None or b.mean_score is None:
        return {"verdict": "Not comparable",
                "detail": "One model has no scored cell, so there is no quality to compare.",
                "winner": None}
    gap = a.mean_score - b.mean_score
    floors = [f for f in (a.repeat_spread, b.repeat_spread) if f is not None]
    floor = max(floors) if floors else None
    if floor is None:
        return {"verdict": "Not measured",
                "detail": (f"{a.model_id} leads by {abs(gap):.3f}, but no scenario has been run "
                           f"twice, so nothing here says whether that gap survives a re-run."),
                "winner": None}
    if abs(gap) <= floor:
        return {"verdict": "Tie",
                "detail": (f"The {abs(gap):.3f} gap is inside the noise floor (±{floor:.3f}) - "
                           f"the spread a model shows against itself. No winner can be named."),
                "winner": None}
    if abs(gap) <= 2 * floor:
        return {"verdict": "Marginal",
                "detail": (f"The {abs(gap):.3f} gap clears the noise floor (±{floor:.3f}) but not "
                           f"by twice it. Not yet a result worth quoting."),
                "winner": None}
    return {"verdict": f"{a.model_id} leads",
            "detail": (f"The {abs(gap):.3f} gap is over twice the noise floor (±{floor:.3f}), so "
                       f"it is unlikely to be run-to-run variation alone."),
            "winner": a.model_id}


def render_dashboard(runs_root: Path, modality: str = "voice") -> Path:
    """Write runs/index.html - the cross-run dashboard."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    runs = load_runs(runs_root, modality)
    if not runs:
        raise SystemExit(f"no {modality} runs under {runs_root}")
    models = rollup_models(runs)
    all_cells = [c for r in runs for c in r.cells]
    duel = _duel(models)
    scenarios = _scenario_blocks(runs, duel)

    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parent / "templates")),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["usd"] = _fmt_usd
    env.filters["s"] = _fmt_s

    run_rows = [{
        "label": r.label, "started": (r.started_at or "")[:16].replace("T", " "),
        "scenario": ", ".join(sorted({c.scenario_id for c in r.cells})) or "—",
        "cells": len(r.cells),
        "passed": sum(1 for c in r.cells if c.status == "scored"),
        "cost": sum(c.total_micro for c in r.cells),
        "judge": r.judge_model, "predictor": r.mos_predictor,
    } for r in reversed(runs)]

    uncalibrated = any(not r.calibration_passed for r in runs)
    footnotes = [
        "<b>Quality is meaned over scored cells only</b>, and always carries its denominator. "
        "A gated cell is counted in <em>Invalid</em> and in the gate rate, never averaged into "
        "quality - a clean read that is too long for an ad slot is the wrong length, not bad audio.",
        "<b>Models are ranked on the gate first</b>, quality second, so failing more can never "
        "look like scoring higher.",
        "<b>A repeat is the same script and the same gates.</b> Runs of an edited scenario are "
        "excluded from every spread on this page.",
        "<b>The objective audio number is a signal metric, not a MOS.</b> It measures SNR, "
        "spectral flatness, clipping and bandwidth - it is labelled as such wherever it appears "
        "and must not be quoted as a mean opinion score.",
        "<b>Cost is what the run believed it paid</b>, at the rates frozen in its own manifest.",
    ]
    if uncalibrated:
        footnotes.insert(0, "<b>The judge is uncalibrated.</b> The 2-humans x 5-clips gate has "
                            "never been run, so <code>naturalness</code> and <code>clarity</code> "
                            "carry no evidence of agreement with a human ear.")

    html = env.get_template("dashboard.html.j2").render(
        models=[{
            "model_id": m.model_id, "accent": m.accent, "mean": m.mean_score,
            "scored_n": m.scored_n, "evaluated_n": m.evaluated_n or m.n,
            "gate_pass_rate": m.gate_pass_rate, "repeat_spread": m.repeat_spread,
            "worst_wer": m.worst_wer, "mean_cost": m.mean_cost,
            "p50_latency": m.mean_latency, "invalid": m.invalid,
        } for m in models],
        runs=runs, run_rows=run_rows, scenarios=scenarios, duel=duel,
        evidence=_evidence(runs), overall=_overall(models),
        n_scenarios=len({c.scenario_id for c in all_cells}),
        n_clips=len(all_cells),
        max_passes=max((s["n_passes"] for s in scenarios), default=0),
        repeated_n=sum(1 for s in scenarios if s["n_passes"] > 1),
        gen_micro=sum(c.cost_micro for c in all_cells),
        asr_micro=sum(c.asr_micro for c in all_cells),
        judge_micro=sum(c.judge_micro for c in all_cells),
        judge_model=runs[-1].judge_model,
        uncalibrated=uncalibrated,
        footnotes=footnotes,
    )
    out = Path(runs_root) / "index.html"
    out.write_text(html, encoding="utf-8")
    return out
