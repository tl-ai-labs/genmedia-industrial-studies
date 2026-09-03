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


def render_dashboard(runs_root: Path, modality: str = "voice") -> Path:
    """Write runs/index.html - the cross-run dashboard."""
    runs = load_runs(runs_root, modality)
    if not runs:
        raise SystemExit(f"no {modality} runs under {runs_root}")
    models = rollup_models(runs)
    all_cells = [c for r in runs for c in r.cells]
    scenarios = sorted({c.scenario_id for c in all_cells})

    total = sum(r.total_micro for r in runs)
    judged = [c for c in all_cells if c.score is not None]
    uncalibrated = any(not r.calibration_passed for r in runs)

    # Paired head-to-head across every run, for the top two models.
    pair_rows: list[str] = []
    verdict_line = "Fewer than two models — nothing to compare."
    if len(models) >= 2:
        a, b = models[0].model_id, models[1].model_id
        w = t = l = 0
        for sid in scenarios:
            for r in runs:
                ca = next((c for c in r.cells if c.scenario_id == sid and c.model_id == a and c.score is not None), None)
                cb = next((c for c in r.cells if c.scenario_id == sid and c.model_id == b and c.score is not None), None)
                if not ca or not cb:
                    continue
                d = ca.score - cb.score
                res = "tie" if abs(d) <= 0.5 else ("win" if d > 0 else "loss")
                t += res == "tie"; w += res == "win"; l += res == "loss"
                pair_rows.append(
                    f'<tr><td class="mono">{_e(sid)}</td><td class="mono">{_e(r.label)}</td>'
                    f'<td class="n">{ca.score:.4f}</td><td class="n">{cb.score:.4f}</td>'
                    f'<td class="n">{d:+.4f}</td>'
                    f'<td><span class="pill pill-{"neutral" if res=="tie" else "success"}">{res}</span></td></tr>'
                )
        decided = w + l
        gap = (models[0].mean_score or 0) - (models[1].mean_score or 0)
        rate = (w / decided) if decided else None
        if gap >= 0.5 or (rate is not None and rate >= 0.70):
            verdict_line = (
                f"<strong>{_e(a)}</strong> beats {_e(b)} — mean gap {gap:.3f}, "
                f"{w} wins of {decided} decided."
            )
        else:
            rate_txt = f"{rate:.0%}" if rate is not None else "n/a"
            verdict_line = (
                f"<strong>Tie.</strong> Mean gap {gap:.3f} is inside the 0.5 band, and "
                f"{_e(a)} won {w} of {decided} decided ({rate_txt}) — below the 70% door. "
                f"Decide on cost, latency, reliability and worst case."
            )

    # ---- model rollup rows + spread bars ---------------------------------
    lo = min((min(m.scores) for m in models if m.scores), default=0)
    hi = max((max(m.scores) for m in models if m.scores), default=10)
    pad = max(0.05, (hi - lo) * 0.25)
    axis_lo, axis_hi = lo - pad, hi + pad
    span = axis_hi - axis_lo or 1

    model_rows, spread_rows, legend = [], [], []
    for m in models:
        legend.append(
            f'<span class="lg"><i style="background:{m.accent}"></i>'
            f'<span class="mono">{_e(m.model_id)}</span></span>'
        )
        model_rows.append(
            "<tr>"
            f'<td class="mono"><i class="dot" style="background:{m.accent}"></i>{_e(m.model_id)}</td>'
            f'<td class="n">{_num(m.mean_score, "{:.3f}")}'
            + f' <span class="dim">({m.scored_n} of {m.evaluated_n or m.n})</span>'
            + (' <span class="pill pill-warning">unc</span>' if uncalibrated else "")
            + "</td>"
            f'<td class="n">±{m.score_spread:.3f}</td>'
            + (f'<td class="n">±{m.repeat_spread:.3f}</td>'
               if m.repeat_spread is not None
               else '<td class="n dim">n/a</td>')
            + f'<td class="n">{_num(m.worst_wer, "{:.4f}")}</td>'
            f'<td class="n">{fmt_usd(int(m.mean_cost))}'
            + ("" if all(c.cost_exact for r in runs for c in r.cells if c.model_id == m.model_id)
               else ' <span class="pill pill-warning">est</span>')
            + "</td>"
            f'<td class="n">{_num(m.mean_latency/1000 if m.mean_latency else None, "{:.1f}s")}</td>'
            f'<td class="n">{_num(m.mean_audio_q, "{:.2f}")}</td>'
            f'<td class="n">{m.success_rate:.0%}</td>'
            f'<td class="n">{_num(m.mean_attempts, "{:.2f}")}</td>'
            f'<td class="n">{len(m.scores)}/{m.n}</td>'
            "</tr>"
        )
        if m.scores:
            left = (min(m.scores) - axis_lo) / span * 100
            width = max(0.6, (max(m.scores) - min(m.scores)) / span * 100)
            mean_pos = ((m.mean_score or 0) - axis_lo) / span * 100
            spread_rows.append(
                f'<div class="srow"><span class="mono sname">{_e(m.model_id)}</span>'
                f'<span class="strack"><span class="sbar" style="left:{left:.2f}%;width:{width:.2f}%;'
                f'background:{m.accent}"></span>'
                f'<span class="smean" style="left:{mean_pos:.2f}%"></span></span>'
                f'<span class="mono sval">{min(m.scores):.3f} – {max(m.scores):.3f}</span></div>'
            )

    # ---- runs tab ---------------------------------------------------------
    run_rows = []
    for r in reversed(runs):
        cal_pill = (
            '<span class="pill pill-success">calibrated</span>'
            if r.calibration_passed
            else '<span class="pill pill-warning">uncalibrated</span>'
        )
        gates_ok = all(c.gates_passed == c.gates_total for c in r.cells)
        run_rows.append(
            "<tr>"
            f'<td class="mono"><a href="{_e(r.run_id)}/report.html">{_e(r.label)}</a></td>'
            f'<td class="mono dim">{_e(r.started_at[:16].replace("T", " "))}</td>'
            f'<td class="n">{len(r.cells)}</td>'
            f'<td class="n">{sum(1 for c in r.cells if c.status == "scored")}</td>'
            f'<td>{"".join(f"<span class=chip>{_e(m)}</span>" for m in r.model_ids)}</td>'
            f'<td>{"<span class=\"pill pill-success\">all pass</span>" if gates_ok else "<span class=\"pill pill-danger\">gate failure</span>"}</td>'
            f'<td>{cal_pill}</td>'
            f'<td class="n">{fmt_usd(r.total_micro)}</td>'
            f'<td class="mono dim">{_e(r.git_sha)}</td>'
            "</tr>"
        )

    # ---- evidence tab -----------------------------------------------------
    #
    # ONE representative clip per model up front, the rest behind a toggle.
    # Three near-identical cards per model read as noise and bury the thing
    # worth looking at; hiding them would lose the repeat evidence entirely.
    # A toggle keeps both.
    #
    # The representative is the MEDIAN run, not the best. A best-of-N clip is
    # a flattering sample and would quietly disagree with the mean shown one
    # tab over - median is the honest answer to "what does this usually
    # sound like", which is what someone opening this tab is asking.
    def _median_cell(cells: list[Cell]) -> Cell:
        scored = sorted([c for c in cells if c.score is not None], key=lambda c: c.score)
        return scored[len(scored) // 2] if scored else cells[0]

    def _card(c: Cell, representative: bool) -> str:
        acc = next((m.accent for m in models if m.model_id == c.model_id), "#666")
        crit = "".join(
            f'<tr><td class="mono dim">{_e(k)}</td><td class="n">{v:.2f}</td></tr>'
            for k, v in sorted(c.criterion_scores.items())
        )
        tag = ' <span class="pill pill-neutral">median run</span>' if representative else ""
        return (
            f'<div class="ecard"><div class="ehead">'
            f'<span class="mono"><i class="dot" style="background:{acc}"></i>{_e(c.model_id)}</span>'
            f'<span class="mono dim">run {_e(c.run_label)}{tag}'
            + (f' · blind “{_e(c.blind_label)}”' if c.blind_label else "")
            + "</span></div>"
            f'<audio controls preload="none" src="{_e(c.audio_rel)}"></audio>'
            f'<div class="emeta mono">'
            f'score <strong>{_num(c.score, "{:.3f}")}</strong> · '
            f'WER {_num(c.wer, "{:.4f}")} · {_num(c.duration_s, "{:.1f}s")} · '
            f'{fmt_usd(c.total_micro)} · gates {c.gates_passed}/{c.gates_total}'
            + ("" if c.audio_q is None else
               f' · audio {c.audio_q:.2f}/5'
               + ("" if c.audio_q_is_mos else ' <span class="pill pill-warning">not a MOS</span>'))
            + (f' · voice {_e(c.voice)}' if c.voice else "")
            + "</div>"
            f'<details><summary>criterion scores</summary><table class="tbl mini">{crit}</table></details>'
            "</div>"
        )

    ev = []
    for sid in scenarios:
        by_model: dict[str, list[Cell]] = {}
        for r in runs:
            for c in (x for x in r.cells if x.scenario_id == sid):
                by_model.setdefault(c.model_id, []).append(c)

        lead, rest = [], []
        for mid in sorted(by_model):
            cells = by_model[mid]
            rep = _median_cell(cells)
            lead.append(_card(rep, representative=len(cells) > 1))
            rest.extend(_card(c, representative=False) for c in cells if c is not rep)

        block = f'<h3 class="mono">{_e(sid)}</h3><div class="egrid">{"".join(lead)}</div>'
        if rest:
            block += (
                f'<details class="more"><summary>{len(rest)} more clip'
                f'{"s" if len(rest) != 1 else ""} from the other runs — the repeat evidence '
                f'behind the spread figure</summary>'
                f'<div class="egrid">{"".join(rest)}</div></details>'
            )
        ev.append(block)

    # ---- repeats tab ------------------------------------------------------
    #
    # The question this tab exists to answer: is the gap between two models
    # bigger than the gap a single model shows against ITSELF when asked the
    # same thing twice? Until a scenario has been run more than once there is
    # no answer, and the tab says so rather than implying stability.
    rep_blocks: list[str] = []
    for sid in scenarios:
        cand = [r for r in runs if any(c.scenario_id == sid and c.score is not None for c in r.cells)]

        # A repeat is the same SCRIPT and the same GATES, asked twice - not
        # the same id twice. vr-game-02 was run, found to carry a gate no
        # correct reading could pass, fixed, and run again under its own id;
        # comparing across that edit would report the size of our change as
        # the machine's noise floor. Runs are newest-last, so the current
        # definition wins and earlier ones are excluded and counted.
        def _defhash(r, _sid=sid):
            return next((c.scenario_hash for c in r.cells
                         if c.scenario_id == _sid and c.score is not None), "")

        newest = _defhash(cand[-1]) if cand else ""
        sruns = [r for r in cand if _defhash(r) == newest]
        stale = len(cand) - len(sruns)

        by_model: dict[str, dict[str, float]] = {}
        for r in sruns:
            for c in r.cells:
                if c.scenario_id == sid and c.score is not None:
                    by_model.setdefault(c.model_id, {})[r.label] = c.score
        if not by_model:
            continue
        labels = [r.label for r in sruns]
        n = len(labels)

        # Column headers name the RUN, and the scenario is already the heading
        # above the table - so "voice-retfix-voi-ret-01" says "voi-ret-01"
        # twice and buries the one word that distinguishes the columns.
        def _short(label: str) -> str:
            out = label.removeprefix(f"{modality}-").removesuffix(f"-{sid}")
            return out or label

        head = "".join(f'<th class="n" title="{_e(l)}">{_e(_short(l))}</th>' for l in labels)
        body = ""
        own: dict[str, float | None] = {}
        for mid in sorted(by_model):
            vals = by_model[mid]
            got = [vals[l] for l in labels if l in vals]
            own[mid] = (max(got) - min(got)) if len(got) > 1 else None
            cells_html = "".join(
                f'<td class="n">{vals[l]:.3f}</td>' if l in vals else '<td class="n dim">—</td>'
                for l in labels
            )
            spread = f"±{own[mid]:.3f}" if own[mid] is not None else '<span class="dim">n/a</span>'
            body += f'<tr><td class="mono">{_e(mid)}</td>{cells_html}<td class="n">{spread}</td></tr>'

        foot = ""
        verdict = ""
        ids = sorted(by_model)
        if len(ids) >= 2:
            a, b = ids[0], ids[1]
            gaps = [by_model[a][l] - by_model[b][l] for l in labels
                    if l in by_model[a] and l in by_model[b]]
            gcells = "".join(f'<td class="n">{g:+.3f}</td>' for g in gaps)
            gcells += '<td class="n dim">—</td>' * (n - len(gaps))
            # The noise floor is the LARGER of the two models' own spreads:
            # a gap has to clear the noisier of the pair to mean anything.
            floors = [v for v in own.values() if v is not None]
            floor = max(floors) if floors else None
            fl = f"±{floor:.3f}" if floor is not None else "not measured"
            foot = (f'<tr class="gaprow"><td class="mono">Δ {_e(a)} − {_e(b)}</td>{gcells}'
                    f'<td class="n">noise {fl}</td></tr>')
            if gaps and floor is not None:
                worst = max(abs(g) for g in gaps)
                if worst <= floor:
                    verdict = (f'<strong>Inside the noise.</strong> The largest gap between these '
                               f'models ({worst:.3f}) is no bigger than the spread one model shows '
                               f'against itself ({fl}). This scenario does not separate them.')
                elif worst <= 2 * floor:
                    verdict = (f'<strong>Marginal.</strong> The largest gap ({worst:.3f}) clears the '
                               f'noise floor ({fl}) but not by much. More repeats before quoting it.')
                else:
                    verdict = (f'<strong>Larger than noise.</strong> The gap ({worst:.3f}) is over '
                               f'twice the noise floor ({fl}), so it is unlikely to be run-to-run '
                               f'variation alone.')
            elif gaps:
                verdict = ('<strong>Single run.</strong> One measurement per model, so there is no '
                           'noise floor to compare the gap against. Run it again.')

        warn = ""
        if n == 2:
            warn = ('<p class="lead">Two runs is the fewest that yields a spread at all — read it '
                    'as an order of magnitude, not a confidence interval.</p>')
        elif n < 2:
            warn = ('<p class="lead">Run this scenario again to measure how much its scores move '
                    'on their own.</p>')
        if stale:
            warn += (f'<p class="lead">{stale} earlier run{"s" if stale != 1 else ""} of this '
                     f'scenario used a <strong>different version</strong> of it and '
                     f'{"are" if stale != 1 else "is"} excluded from the spread above. A repeat '
                     f'has to be the same script and the same gates, or what it measures is our '
                     f'edit rather than the model.</p>')

        rep_blocks.append(
            f'<h3 class="mono">{_e(sid)} <span class="dim">· {n} run{"s" if n != 1 else ""}</span></h3>'
            f'<div class="scroll"><table class="tbl">'
            f'<thead><tr><th>Model</th>{head}<th class="n">Own spread</th></tr></thead>'
            f"<tbody>{body}{foot}</tbody></table></div>"
            + (f'<div class="callout">{verdict}</div>' if verdict else "")
            + warn
        )

    ctx = dict(
        repeats="".join(rep_blocks),
        runs=runs, models=models, all_cells=all_cells, total=total, judged=judged,
        uncalibrated=uncalibrated, model_rows="".join(model_rows),
        spread_rows="".join(spread_rows), legend="".join(legend),
        run_rows="".join(run_rows), pair_rows="".join(pair_rows),
        verdict_line=verdict_line, evidence="".join(ev),
        axis_lo=axis_lo, axis_hi=axis_hi,
    )
    out = Path(runs_root) / "index.html"
    out.write_text(_page(ctx), encoding="utf-8")
    return out


def _page(c: dict[str, Any]) -> str:
    runs, models = c["runs"], c["models"]
    judge_models = sorted({r.judge_model for r in runs})
    predictors = sorted({r.mos_predictor for r in runs})
    scen = sorted({x.scenario_id for x in c["all_cells"]})
    wers = [x.wer for x in c["all_cells"] if x.wer is not None]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GenMedia runs</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{{
  --ink:#0A0A0A; --body:#1F1F1F; --muted:#6B6B6B;
  --canvas:#FFFFFF; --surface:#F5F5F5; --line:#E5E5E5;
  --brand:#E00917;
  --ok:#047857; --ok-bg:#ECFDF5; --warn:#92400E; --warn-bg:#FFFBEB;
  --danger:#BE123C; --danger-bg:#FFF1F2; --info:#1D4ED8; --info-bg:#EFF6FF;
  --display:"Space Grotesk",system-ui,sans-serif;
  --sans:"IBM Plex Sans",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --ink:#F5F5F5; --body:#D8D8D8; --muted:#8E8E8E;
  --canvas:#0B0B0C; --surface:#151517; --line:#26262A;
  --brand:#FF4D57;
  --ok:#34D399; --ok-bg:#052E23; --warn:#FBBF24; --warn-bg:#2E2205;
  --danger:#FB7185; --danger-bg:#340C15; --info:#93C5FD; --info-bg:#0B1F3D;
}}}}
:root[data-theme="dark"]{{
  --ink:#F5F5F5; --body:#D8D8D8; --muted:#8E8E8E;
  --canvas:#0B0B0C; --surface:#151517; --line:#26262A;
  --brand:#FF4D57;
  --ok:#34D399; --ok-bg:#052E23; --warn:#FBBF24; --warn-bg:#2E2205;
  --danger:#FB7185; --danger-bg:#340C15; --info:#93C5FD; --info-bg:#0B1F3D;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--canvas);color:var(--body);
  font-family:var(--sans);font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 24px}}
a{{color:var(--brand);text-decoration:none}} a:hover{{text-decoration:underline}}

.eyebrow{{font-family:var(--mono);font-size:11.5px;font-weight:500;letter-spacing:.22em;
  text-transform:uppercase;color:var(--muted);display:flex;align-items:center;gap:8px;margin:0 0 14px}}
.eyebrow::before{{content:"";width:6px;height:6px;border-radius:50%;background:var(--brand)}}
h1{{font-family:var(--display);font-weight:600;font-size:clamp(28px,3.4vw,36px);
  letter-spacing:-.025em;line-height:1.10;color:var(--ink);margin:0 0 10px}}
h2{{font-family:var(--display);font-weight:600;font-size:24px;letter-spacing:-.02em;
  line-height:1.2;color:var(--ink);margin:0 0 4px}}
h3{{font-family:var(--display);font-weight:600;font-size:18px;letter-spacing:-.015em;
  color:var(--ink);margin:28px 0 10px}}
p{{margin:0 0 14px;max-width:78ch}}
.lead{{color:var(--muted)}}
.label{{font-family:var(--mono);font-size:11px;font-weight:500;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);line-height:1.4}}
.mono{{font-family:var(--mono);font-size:12.5px}}
.dim{{color:var(--muted)}}

header.top{{border-bottom:1px solid var(--line);padding:44px 0 0}}
.statband{{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
  gap:1px;background:var(--line);border:1px solid var(--line);border-bottom:0;margin-top:26px}}
.sc{{background:var(--canvas);padding:16px 18px}}
.sc .v{{font-family:var(--display);font-weight:600;font-size:28px;letter-spacing:-.02em;
  color:var(--ink);line-height:1.1;font-variant-numeric:tabular-nums lining-nums}}
.sc .k{{margin-top:2px}}

nav.tabs{{display:flex;gap:2px;border-bottom:1px solid var(--line);margin-top:0;overflow-x:auto}}
nav.tabs button{{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
  font-family:var(--mono);font-size:12.5px;font-weight:500;color:var(--muted);
  padding:13px 16px;cursor:pointer;white-space:nowrap}}
nav.tabs button:hover{{color:var(--ink)}}
nav.tabs button[aria-selected="true"]{{color:var(--ink);border-bottom-color:var(--brand)}}
nav.tabs button:focus-visible{{outline:2px solid var(--brand);outline-offset:-2px}}

section.panel{{padding:30px 0 60px}}
section.panel[hidden]{{display:none}}

.scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:4px;margin:14px 0}}
.gaprow td{{border-top:2px solid var(--line);font-weight:600}}
.gaprow td:first-child{{font-size:12px}}
table.tbl{{width:100%;border-collapse:collapse;font-size:14px;background:var(--canvas)}}
.tbl th{{font-family:var(--mono);font-size:11px;font-weight:500;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);text-align:left;background:var(--surface);
  padding:10px 12px;white-space:nowrap;border-bottom:1px solid var(--line)}}
.tbl th.n,.tbl td.n{{text-align:right;font-variant-numeric:tabular-nums lining-nums}}
.tbl td{{padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;color:var(--body)}}
.tbl tbody tr:last-child td{{border-bottom:0}}
.tbl.mini td{{padding:5px 10px;font-size:13px}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;vertical-align:1px}}
.chip{{display:inline-block;font-family:var(--mono);font-size:11px;color:var(--body);
  background:var(--surface);border:1px solid var(--line);border-radius:3px;
  padding:1px 6px;margin-right:5px}}

.pill{{display:inline-block;font-family:var(--mono);font-size:10.5px;font-weight:500;
  letter-spacing:.06em;padding:2px 7px;border-radius:3px;white-space:nowrap}}
.pill-success{{background:var(--ok-bg);color:var(--ok)}}
.pill-warning{{background:var(--warn-bg);color:var(--warn)}}
.pill-danger{{background:var(--danger-bg);color:var(--danger)}}
.pill-neutral{{background:var(--surface);color:var(--muted)}}
.pill-info{{background:var(--info-bg);color:var(--info)}}

.callout{{border:1px solid var(--line);border-left:3px solid var(--brand);border-radius:4px;
  background:var(--surface);padding:16px 18px;margin:16px 0}}
.callout.warn{{border-left-color:var(--warn)}}
.callout p:last-child{{margin:0}}

.spread{{border:1px solid var(--line);border-radius:4px;padding:20px 22px;margin:14px 0}}
.srow{{display:grid;grid-template-columns:220px 1fr 150px;gap:14px;align-items:center;margin-bottom:11px}}
.srow:last-child{{margin-bottom:0}}
.strack{{position:relative;height:22px;background:var(--surface);border-radius:3px}}
.sbar{{position:absolute;top:7px;height:8px;border-radius:2px;min-width:3px}}
.smean{{position:absolute;top:3px;width:2px;height:16px;background:var(--ink);opacity:.55}}
.sval{{text-align:right;color:var(--muted)}}
.saxis{{display:grid;grid-template-columns:220px 1fr 150px;gap:14px;margin-top:8px}}
.saxis .ax{{display:flex;justify-content:space-between;font-family:var(--mono);
  font-size:10.5px;color:var(--muted)}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;margin-top:14px}}
.lg{{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--muted)}}
.lg i{{width:9px;height:9px;border-radius:50%;display:inline-block}}

.egrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}}
.ecard{{border:1px solid var(--line);border-radius:4px;padding:14px 16px;background:var(--canvas)}}
.ehead{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
  flex-wrap:wrap;margin-bottom:8px}}
audio{{width:100%;margin:6px 0 8px}}
.emeta{{color:var(--muted);line-height:1.8}}
.emeta strong{{color:var(--ink)}}
details{{margin-top:8px}} summary{{cursor:pointer;font-family:var(--mono);font-size:12px;color:var(--brand)}}
details.more{{margin:16px 0 0;border-top:1px solid var(--line);padding-top:14px}}
details.more>summary{{font-size:12.5px;margin-bottom:12px}}
details.more[open]>summary{{margin-bottom:14px}}

footer{{border-top:1px solid var(--line);padding:22px 0 60px;color:var(--muted);
  font-family:var(--mono);font-size:12px;line-height:1.8}}
footer p{{max-width:88ch;margin:0 0 7px}}
</style></head><body>

<header class="top"><div class="wrap">
  <p class="eyebrow">GenMedia runs · {c['runs'][0].modality}</p>
  <h1>Text-to-speech model comparison</h1>
  <p class="lead">{len(runs)} run{'s' if len(runs)!=1 else ''} ·
     {len(scen)} scenario{'s' if len(scen)!=1 else ''} ·
     {len(models)} models · {len(c['all_cells'])} clips.
     Quality, cost, latency and reliability are reported side by side and never blended.</p>
  <div class="statband">
    <div class="sc"><div class="v">{len(c['all_cells'])}</div><div class="k label">clips</div></div>
    <div class="sc"><div class="v">{max(wers) if wers else 0:.4f}</div><div class="k label">worst wer</div></div>
    <div class="sc"><div class="v">{fmt_usd(c['total'])}</div><div class="k label">spend</div></div>
    <div class="sc"><div class="v">{len(c['judged'])}/{len(c['all_cells'])}</div><div class="k label">scored</div></div>
    <div class="sc"><div class="v">{'no' if c['uncalibrated'] else 'yes'}</div><div class="k label">calibrated</div></div>
  </div>
</div></header>

<div class="wrap">
<nav class="tabs" role="tablist">
  <button role="tab" aria-selected="true"  data-p="models">Models</button>
  <button role="tab" aria-selected="false" data-p="runs">Runs</button>
  <button role="tab" aria-selected="false" data-p="paired">Head to head</button>
  <button role="tab" aria-selected="false" data-p="repeats">Repeats</button>
  <button role="tab" aria-selected="false" data-p="evidence">Evidence</button>
</nav>

<section class="panel" id="p-models">
  <h2>Model rollup</h2>
  <p class="lead"><strong>Spread</strong> is best-to-worst across everything a model
     scored, so it mixes two different things. <strong>Repeat</strong> is the part that
     is actually noise — the same scenario asked more than once — and it is the number
     that says whether a gap between models means anything. It reads n/a until a
     scenario has been run twice. See the Repeats tab.</p>
  <div class="callout {'warn' if c['uncalibrated'] else ''}">
    <p><strong>Verdict.</strong> {c['verdict_line']}</p>
  </div>
  <div class="scroll"><table class="tbl">
    <thead><tr><th>Model</th><th class="n">Quality</th><th class="n">Spread</th><th class="n">Repeat</th>
      <th class="n">Worst WER</th><th class="n">Cost / clip</th><th class="n">Latency</th>
      <th class="n">Audio q.</th><th class="n">Success</th><th class="n">Attempts</th>
      <th class="n">Scored</th></tr></thead>
    <tbody>{c['model_rows']}</tbody>
  </table></div>

  <h3>Score spread, drawn to scale</h3>
  <div class="spread">
    {c['spread_rows']}
    <div class="saxis"><span></span><span class="ax"><span>{c['axis_lo']:.2f}</span>
      <span>{c['axis_hi']:.2f}</span></span><span></span></div>
    <div class="legend">{c['legend']}</div>
  </div>
  <p class="lead">The tick marks the mean; the bar spans best to worst across runs.
     Where the bars overlap, the models are not separated by this evidence.</p>
</section>

<section class="panel" id="p-runs" hidden>
  <h2>Runs</h2>
  <p class="lead">Every run is a self-contained folder — click a run id for its own
     report with players, transcript diffs and judge reasoning.</p>
  <div class="scroll"><table class="tbl">
    <thead><tr><th>Run</th><th>Started</th><th class="n">Clips</th><th class="n">Scored</th>
      <th>Models</th><th>Gates</th><th>Calibration</th><th class="n">Spend</th>
      <th>Commit</th></tr></thead>
    <tbody>{c['run_rows']}</tbody>
  </table></div>
</section>

<section class="panel" id="p-paired" hidden>
  <h2>Head to head</h2>
  <p class="lead">Both models answered the identical scenario in the same run, so this
     comparison is paired. A gap of 0.5 or less is a declared tie. That 0.5 is a fixed
     rule of thumb set before any run, NOT a measured threshold — the Repeats tab
     carries the noise actually observed, which is the number to trust.</p>
  <div class="scroll"><table class="tbl">
    <thead><tr><th>Scenario</th><th>Run</th>
      <th class="n">{_e(models[0].model_id) if models else 'A'}</th>
      <th class="n">{_e(models[1].model_id) if len(models)>1 else 'B'}</th>
      <th class="n">Δ</th><th>Result</th></tr></thead>
    <tbody>{c['pair_rows'] or '<tr><td colspan=6 class=dim>No paired cells.</td></tr>'}</tbody>
  </table></div>
</section>

<section class="panel" id="p-repeats" hidden>
  <h2>Repeats</h2>
  <p class="lead">The same scenario, asked more than once. A model scored against itself
     is the only honest yardstick for a gap between two models — if one model moves by
     0.2 between two runs of the same script, a 0.2 gap against a rival is not a finding.</p>
  {c['repeats'] or '<p class="dim">No scenarios yet.</p>'}
</section>

<section class="panel" id="p-evidence" hidden>
  <h2>Evidence</h2>
  <p class="lead">Every clip, playable, with its measured facts. Audio streams from the
     run folder beside this page — open this file from disk, not from a copy.</p>
  {c['evidence']}
</section>
</div>

<footer><div class="wrap">
  <p><strong>Judge.</strong> {' · '.join(_e(j) for j in judge_models)}, temperature 0, one clip
     per call, labels shuffled per scenario, audio re-encoded so no provider metadata reaches it.
     Judge failure is recorded as unjudged and excluded from the mean — never scored 0.</p>
  <p><strong>Audio quality.</strong> Predictor: {' · '.join(_e(p) for p in predictors)}. A signal
     metric is a real measurement of the file but is <em>not</em> a perceptual MOS, and is badged
     as such wherever it appears.</p>
  <p><strong>Calibration.</strong> {_e(runs[-1].calibration_reason)}</p>
  <p><strong>Declared differences.</strong> Voices are not comparable across providers; one
     deliberate voice is pinned per provider and recorded in each run manifest. A parameter a
     provider cannot honour is recorded in params_unsupported and footnoted in that run's report.</p>
  <p>These models are non-deterministic and providers update them silently. Two runs a week apart
     will differ — which is why the winner threshold is 0.5 and why spread is on the same screen
     as the mean.</p>
</div></footer>

<script>
const tabs=[...document.querySelectorAll('nav.tabs button')];
function show(name){{
  tabs.forEach(t=>t.setAttribute('aria-selected',String(t.dataset.p===name)));
  document.querySelectorAll('section.panel').forEach(s=>{{s.hidden=(s.id!=='p-'+name);}});
  history.replaceState(null,'','#'+name);
}}
tabs.forEach(t=>t.addEventListener('click',()=>show(t.dataset.p)));
const initial=(location.hash||'#models').slice(1);
if(tabs.some(t=>t.dataset.p===initial)) show(initial);
</script>
</body></html>"""
