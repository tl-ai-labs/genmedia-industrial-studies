"""
Scoring rollup and the HTML report (plan v1.2 sections 14 and 16).

One static file per run, opened straight from the run folder with no server.
Three blocks: the summary (one table per task), the per-scenario evidence
with a player and a transcript diff, and the footnotes that say what was
assumed.

FOUR COLUMNS, NEVER ONE. Quality, cost, latency and reliability sit side by
side and a human decides. A model that is 20% better and 5x the price is a
business decision, not an arithmetic one.
"""

from __future__ import annotations

import difflib
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment

from . import calibration as calib
from .cost import fmt_usd
from .normalize import normalize
from .rubrics import Rubric
from .scoring import ScoredCell, ModelSummary, paired_wtl, score_cell, summarise, verdict
from .telemetry import (RunPaths, artefact_url, incomplete_scenarios, read_manifest, read_stream,
                        write_manifest)


@dataclass
class CellStats:
    attempts: int = 0
    succeeded: bool = False
    latency_ms: int | None = None
    gen_cost: int = 0
    asr_cost: int = 0
    judge_cost: int = 0
    status: str = "not attempted"
    params_unsupported: tuple[str, ...] = ()
    voice_logical: str | None = None
    voice_provider_id: str | None = None
    duration_s: float | None = None
    cost_exact: bool = True


def collect_stats(paths: RunPaths) -> dict[str, CellStats]:
    """Fold telemetry.jsonl into one record per cell."""
    stats: dict[str, CellStats] = {}
    for row in read_stream(paths, "telemetry"):
        sid, mid = row.get("scenario_id"), row.get("model_id")
        if not sid or not mid:
            continue
        key = f"{sid}|{mid}"
        st = stats.setdefault(key, CellStats())
        if row.get("step") == "asr":
            st.asr_cost += int((row.get("cost") or {}).get("micro_usd", 0))
            continue
        # A generation attempt.
        if row.get("attempt", 0) >= 1:
            st.attempts += 1
        st.gen_cost += int((row.get("cost") or {}).get("micro_usd", 0))
        status = row.get("status")
        st.status = status or st.status
        if status in ("ok", "resumed"):
            st.succeeded = True
            if row.get("latency_ms") is not None:
                st.latency_ms = int(row["latency_ms"])
            out = row.get("output") or {}
            if out.get("duration_s"):
                st.duration_s = float(out["duration_s"])
            st.params_unsupported = tuple(row.get("params_unsupported") or ())
            v = row.get("voice") or {}
            st.voice_logical = v.get("logical") or st.voice_logical
            st.voice_provider_id = v.get("provider_voice_id") or st.voice_provider_id
            cost = row.get("cost") or {}
            if cost.get("usage_exact") is False:
                st.cost_exact = False
    for row in read_stream(paths, "judge"):
        key = f"{row.get('scenario_id')}|{row.get('model_id')}"
        if key in stats:
            stats[key].judge_cost += int((row.get("cost") or {}).get("micro_usd", 0))
    return stats


def build_scores(
    paths: RunPaths, scenarios: list, rubrics: dict[str, Rubric], project_root: Path
) -> tuple[list[ScoredCell], calib.CalibrationState, dict[str, Any]]:
    """
    Score every cell, then evaluate the calibration gate against the judge's
    own composites from this run.

    Order matters: calibration needs judge composites, and scoring needs the
    calibration verdict to mark cells trusted or not. So we score once with
    calibration assumed FALSE to obtain the composites, evaluate the gate,
    then score again for real. Both passes are pure arithmetic over stored
    records - no spend, no regeneration.
    """
    by_id = {s.id: s for s in scenarios}
    checks = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "checks")}
    judges = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "judge")}

    class _Check:
        def __init__(self, rec: dict[str, Any]) -> None:
            self.measurements = rec.get("measurements", {})
            self.failed_gates = rec.get("failed_gates", [])
            self.passed = bool(rec.get("passed"))
            self.raw = rec

    class _Judge:
        def __init__(self, rec: dict[str, Any]) -> None:
            self.status = rec.get("status", "unjudged")
            self.error = rec.get("error")
            scores = rec.get("scores") or {}
            reasoning = rec.get("reasoning") or {}
            self.criteria = [
                type("JC", (), {"name": k, "score": float(v), "reasoning": reasoning.get(k, "")})()
                for k, v in scores.items()
            ]
            self.raw = rec

    # A scenario not every model answered is a GAP, not a result. Scoring the
    # arm that survived would credit it for a comparison that never happened,
    # so those cells are marked `incomplete` with no score - excluded from the
    # mean by summarise(), and from paired win/tie/loss by score is None.
    partial = incomplete_scenarios(paths)

    def _pass(calibrated: bool) -> list[ScoredCell]:
        out: list[ScoredCell] = []
        for key, crec in checks.items():
            sid, mid = key.split("|", 1)
            scenario = by_id.get(sid)
            if scenario is None:
                continue
            rubric = rubrics[scenario.task]
            if sid in partial:
                out.append(
                    ScoredCell(
                        scenario_id=sid, model_id=mid, task=scenario.task,
                        status="incomplete", rubric_hash=rubric.rubric_hash, score=None,
                        note="not every model answered this scenario - one arm alone is "
                             "not comparable, so it is excluded rather than scored",
                    )
                )
                continue
            jrec = judges.get(key)
            out.append(
                score_cell(
                    scenario,
                    rubric,
                    mid,
                    _Check(crec),
                    _Judge(jrec) if jrec else None,
                    calibrated,
                )
            )
        return out

    provisional = _pass(False)
    composites = {f"{c.scenario_id}|{c.model_id}": c.score for c in provisional if c.score is not None}

    any_rubric = next(iter(rubrics.values()))
    judge_model = next((r.get("judge_model") for r in judges.values() if r.get("judge_model")), "unknown")
    state = calib.evaluate(project_root, any_rubric.rubric_hash, judge_model, composites)

    cells = _pass(state.passed)

    # scores.jsonl is rewritten from scratch every time the report runs. It is
    # DERIVED entirely from stored criterion scores and stored measurements, so
    # re-scoring after a weight edit costs nothing, regenerates no audio and
    # re-judges nothing. It is the one file in the run folder that is not
    # append-only, precisely because it is not evidence - it is arithmetic.
    path = paths.stream("scores")
    if path.exists():
        path.unlink()
    from .telemetry import Telemetry

    tel = Telemetry(paths, paths.run_id)
    for cell in sorted(cells, key=lambda c: (c.scenario_id, c.model_id)):
        tel.write("scores", cell.as_record)

    return cells, state, {"judge_model": judge_model}


def word_diff(script: str, transcript: str) -> list[dict[str, str]]:
    """Word-level diff of the NORMALIZED pair - what the gate actually compared."""
    a, b = normalize(script).split(), normalize(transcript).split()
    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            out.append({"kind": "same", "text": " ".join(a[i1:i2])})
        elif tag == "delete":
            out.append({"kind": "missing", "text": " ".join(a[i1:i2])})
        elif tag == "insert":
            out.append({"kind": "added", "text": " ".join(b[j1:j2])})
        else:
            out.append({"kind": "missing", "text": " ".join(a[i1:i2])})
            out.append({"kind": "added", "text": " ".join(b[j1:j2])})
    return out


def render(
    paths: RunPaths,
    scenarios: list,
    rubrics: dict[str, Rubric],
    registry,
    project_root: Path,
) -> Path:
    manifest = read_manifest(paths)
    cells, state, meta = build_scores(paths, scenarios, rubrics, project_root)
    stats = collect_stats(paths)
    by_id = {s.id: s for s in scenarios}
    checks = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "checks")}
    judges = {f"{r['scenario_id']}|{r['model_id']}": r for r in read_stream(paths, "judge")}

    model_ids = sorted({c.model_id for c in cells})
    tasks = sorted({c.task for c in cells})
    spec_by_id = {m.id: m for m in registry.models}

    tables = []
    for task in tasks:
        rows = []
        for mid in model_ids:
            summary: ModelSummary = summarise(cells, mid, task)
            if summary.attempted == 0:
                continue
            keys = [f"{c.scenario_id}|{mid}" for c in cells if c.model_id == mid and c.task == task]
            lat = [stats[k].latency_ms for k in keys if k in stats and stats[k].latency_ms]
            attempts = [stats[k].attempts for k in keys if k in stats and stats[k].succeeded]
            ok = sum(1 for k in keys if k in stats and stats[k].succeeded)
            gen = sum(stats[k].gen_cost for k in keys if k in stats)
            asr = sum(stats[k].asr_cost for k in keys if k in stats)
            jud = sum(stats[k].judge_cost for k in keys if k in stats)
            n = max(1, len(keys))
            rows.append(
                {
                    "model": mid,
                    "mean": summary.mean,
                    "worst": summary.worst,
                    "below_five": summary.below_five,
                    "judged": summary.judged,
                    "attempted": summary.attempted,
                    "unjudged": summary.unjudged,
                    "invalid": summary.invalid,
                    "coverage": summary.coverage,
                    "cost_total": gen + asr + jud,
                    "cost_gen": gen,
                    "cost_asr": asr,
                    "cost_judge": jud,
                    "cost_per_scenario": (gen + asr + jud) / n,
                    "cost_exact": all(stats[k].cost_exact for k in keys if k in stats),
                    "p50": statistics.median(lat) if lat else None,
                    "max": max(lat) if lat else None,
                    "success": ok / n,
                    "attempts_mean": statistics.mean(attempts) if attempts else None,
                    "wtl": None,
                }
            )
        if len(rows) >= 2:
            for r in rows:
                others = [o for o in rows if o["model"] != r["model"]]
                if others:
                    best_other = max(others, key=lambda o: o["mean"] if o["mean"] is not None else -1)
                    p = paired_wtl(cells, r["model"], best_other["model"], task)
                    r["wtl"] = f"{p.wins}-{p.ties}-{p.losses}"
        rows.sort(key=lambda r: (r["mean"] is None, -(r["mean"] or 0)))
        summaries = [summarise(cells, m, task) for m in model_ids]
        tables.append(
            {
                "task": task,
                "rows": rows,
                "verdict": verdict(summaries, cells, task, state.passed),
                "rubric": rubrics[task],
            }
        )

    evidence = []
    for scenario in sorted(scenarios, key=lambda s: s.id):
        entries = []
        for mid in model_ids:
            key = f"{scenario.id}|{mid}"
            cell = next((c for c in cells if c.scenario_id == scenario.id and c.model_id == mid), None)
            if cell is None and key not in stats:
                continue
            crec = checks.get(key, {})
            jrec = judges.get(key, {})
            st = stats.get(key, CellStats())
            transcript = crec.get("transcript_raw")
            spec = spec_by_id.get(mid)
            ext = "wav"
            # The path is what `exists()` needs; the URL is what the <audio>
            # tag needs, and for a variant scenario they differ - see
            # telemetry.artefact_url. Keeping one string for both is what
            # broke the dashboard's players.
            audio_rel = f"outputs/{scenario.modality}/{scenario.id}/{mid}.{ext}"
            entries.append(
                {
                    "model": mid,
                    "cell": cell,
                    "audio": (artefact_url(audio_rel)
                              if (paths.dir / audio_rel).exists() else None),
                    "transcript": transcript,
                    "diff": word_diff(str(scenario.checks.get("wer_reference") or scenario.text), transcript)
                    if transcript
                    else None,
                    "wer": (crec.get("wer") or {}).get("wer"),
                    "gates": crec.get("gates", []),
                    "passed": crec.get("passed"),
                    "measurements": crec.get("measurements", {}),
                    "judge_reasoning": jrec.get("reasoning", {}),
                    "judge_status": jrec.get("status"),
                    "judge_error": jrec.get("error"),
                    "blind_label": jrec.get("blind_label"),
                    "stats": st,
                    "voice": f"{st.voice_logical} -> {st.voice_provider_id}"
                    if st.voice_provider_id
                    else None,
                    "supports": list(spec.supports) if spec else [],
                }
            )
        if entries:
            evidence.append(
                {
                    "scenario": scenario,
                    "entries": entries,
                    "reference_differs": "wer_reference" in (scenario.checks or {}),
                }
            )

    voice_map_rows = []
    for mid in model_ids:
        spec = spec_by_id.get(mid)
        if not spec:
            continue
        for logical, provider_voice in spec.voice_map.items():
            voice_map_rows.append({"model": mid, "logical": logical, "provider_voice": provider_voice})

    unsupported_rows = sorted(
        {(k.split("|")[1], p) for k, s in stats.items() for p in s.params_unsupported}
    )

    totals = {
        "gen": sum(s.gen_cost for s in stats.values()),
        "asr": sum(s.asr_cost for s in stats.values()),
        "judge": sum(s.judge_cost for s in stats.values()),
    }
    totals["all"] = totals["gen"] + totals["asr"] + totals["judge"]

    html = Environment(autoescape=True).from_string(TEMPLATE).render(
        manifest=manifest,
        tables=tables,
        evidence=evidence,
        calibration=state,
        voice_map_rows=voice_map_rows,
        unsupported_rows=unsupported_rows,
        totals=totals,
        fmt_usd=fmt_usd,
        judge_model=meta["judge_model"],
        rubrics=rubrics,
        model_ids=model_ids,
        skipped=manifest.get("skipped", []),
    )
    paths.report.write_text(html, encoding="utf-8")

    manifest["calibration"] = state.as_record
    manifest["cost_totals_micro_usd"] = totals
    write_manifest(paths, manifest)
    return paths.report


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice comparison - {{ manifest.run_id }}</title>
<style>
  :root {
    --ink:#12191b; --ink2:#4d5b5d; --ink3:#758385; --ground:#eef1f1; --card:#fff;
    --rule:#d3dad9; --rule2:#e5eae9; --accent:#0e6b70;
    --ok:#1a6b47; --okbg:#e0efe6; --warn:#8a5606; --warnbg:#f7ecd8; --bad:#93251f; --badbg:#f7e2df;
  }
  @media (prefers-color-scheme: dark) {
    :root { --ink:#e3eaea; --ink2:#9cacad; --ink3:#7b8b8c; --ground:#0d1416; --card:#151e20;
      --rule:#273335; --rule2:#1f2a2c; --accent:#4fc8cc;
      --ok:#62c994; --okbg:#14301f; --warn:#dca63a; --warnbg:#31260c; --bad:#eb8177; --badbg:#3a1a17; }
  }
  * { box-sizing:border-box }
  body { margin:0; padding:0 20px 80px; background:var(--ground); color:var(--ink);
    font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:1120px; margin:0 auto }
  header { padding:44px 0 20px; border-bottom:1px solid var(--rule) }
  h1 { font-size:30px; letter-spacing:-.02em; margin:0 0 8px }
  h2 { font-size:21px; letter-spacing:-.015em; margin:44px 0 12px; padding-bottom:8px;
    border-bottom:2px solid var(--ink) }
  h3 { font-size:16px; margin:26px 0 8px }
  .sub { color:var(--ink2); margin:0 0 12px }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px }
  .badge { display:inline-block; padding:2px 8px; border-radius:3px; font-size:11px;
    font-weight:600; letter-spacing:.06em; text-transform:uppercase; font-family:ui-monospace,monospace }
  .b-ok { background:var(--okbg); color:var(--ok) } .b-warn { background:var(--warnbg); color:var(--warn) }
  .b-bad { background:var(--badbg); color:var(--bad) }
  table { width:100%; border-collapse:collapse; background:var(--card); font-size:14px }
  .scroll { overflow-x:auto; border:1px solid var(--rule); border-radius:4px }
  th,td { padding:9px 11px; text-align:left; border-bottom:1px solid var(--rule2); white-space:nowrap }
  th { font-size:11px; letter-spacing:.07em; text-transform:uppercase; color:var(--ink3); font-weight:600 }
  td.num { text-align:right; font-variant-numeric:tabular-nums; font-family:ui-monospace,monospace }
  tr:last-child td { border-bottom:0 }
  .verdict { background:var(--card); border:1px solid var(--rule); border-left:4px solid var(--accent);
    border-radius:4px; padding:14px 18px; margin:14px 0 }
  .card { background:var(--card); border:1px solid var(--rule); border-radius:4px;
    padding:16px 18px; margin-bottom:14px }
  audio { width:100%; max-width:420px; margin:8px 0 }
  .diff span.same { color:var(--ink2) }
  .diff span.missing { background:var(--badbg); color:var(--bad); text-decoration:line-through; padding:0 2px }
  .diff span.added { background:var(--okbg); color:var(--ok); padding:0 2px }
  .diff { font-family:ui-monospace,monospace; font-size:12.5px; line-height:1.9;
    background:var(--ground); padding:10px 12px; border-radius:3px }
  .gates { font-size:12.5px; color:var(--ink2); margin:8px 0 0 }
  .gates li { margin-bottom:2px }
  details { margin-top:8px } summary { cursor:pointer; font-size:13px; color:var(--accent) }
  footer { margin-top:52px; padding-top:18px; border-top:1px solid var(--rule);
    font-size:12.5px; color:var(--ink3); line-height:1.8 }
  .grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px }
  .k { color:var(--ink3) }
</style></head><body><div class="wrap">

<header>
  <h1>Voice model comparison</h1>
  <p class="sub mono">run {{ manifest.run_id }} &middot; {{ manifest.started_at }} &middot;
     modality {{ manifest.modality }} &middot; {{ manifest.scenario_count }} scenarios &times;
     {{ model_ids|length }} models</p>
  {% if not calibration.passed %}
  <p><span class="badge b-warn">judge uncalibrated</span>
     <span class="mono" style="color:var(--ink2)">{{ calibration.reason }}</span></p>
  {% else %}
  <p><span class="badge b-ok">calibrated</span>
     <span class="mono" style="color:var(--ink2)">{{ calibration.reason }}</span></p>
  {% endif %}
</header>

{% for t in tables %}
<h2>{{ t.task }}</h2>
<div class="scroll"><table>
  <tr>
    <th>Model</th><th>Quality</th><th>W&ndash;T&ndash;L</th><th>Judged</th><th>Worst</th>
    <th>&lt;5</th><th>Cost / scenario</th><th>Latency p50 / max</th><th>Success</th><th>Attempts</th>
  </tr>
  {% for r in t.rows %}
  <tr>
    <td class="mono">{{ r.model }}</td>
    <td class="num">
      {% if r.mean is none %}&mdash;{% else %}{{ "%.2f"|format(r.mean) }}{% endif %}
      {% if not calibration.passed %}<span class="badge b-warn" title="naturalness and prosody not yet validated against humans">unc</span>{% endif %}
    </td>
    <td class="num">{{ r.wtl or "&mdash;"|safe }}</td>
    <td class="num">{{ r.judged }}/{{ r.attempted }}{% if r.unjudged %} <span class="k">({{ r.unjudged }} unjudged)</span>{% endif %}</td>
    <td class="num">{% if r.worst is none %}&mdash;{% else %}{{ "%.2f"|format(r.worst) }}{% endif %}</td>
    <td class="num">{{ r.below_five }}</td>
    <td class="num">{{ fmt_usd(r.cost_per_scenario|int) }}{% if not r.cost_exact %} <span class="badge b-warn">est</span>{% endif %}</td>
    <td class="num">{% if r.p50 %}{{ "%.1f"|format(r.p50/1000) }}s / {{ "%.1f"|format(r.max/1000) }}s{% else %}&mdash;{% endif %}</td>
    <td class="num">{{ "%.0f%%"|format(r.success*100) }}</td>
    <td class="num">{% if r.attempts_mean %}{{ "%.2f"|format(r.attempts_mean) }}{% else %}&mdash;{% endif %}</td>
  </tr>
  {% endfor %}
</table></div>
<div class="verdict">
  <strong>Verdict.</strong> {{ t.verdict.reason }}
  <div class="mono" style="margin-top:6px;color:var(--ink3)">rubric {{ t.rubric.rubric_hash[:16] }}&hellip;
    &middot; weights {% for c in t.rubric.criteria %}{{ c.key }}={{ c.weight }}{% if not loop.last %}, {% endif %}{% endfor %}</div>
</div>
{% endfor %}

<h2>Per-scenario evidence</h2>
{% for e in evidence %}
<div class="card">
  <h3>{{ e.scenario.id }} &middot; {{ e.scenario.title }}
    <span class="badge b-ok" style="background:none;color:var(--ink3)">{{ e.scenario.task }}</span></h3>
  <p class="mono" style="color:var(--ink2)">{{ e.scenario.text }}</p>
  {% if e.reference_differs %}
  <p><span class="badge b-warn">negative control</span>
    <span class="mono">WER is measured against a deliberately different reference &mdash; a working gate must fail this cell.</span></p>
  {% endif %}
  <div class="grid2">
  {% for x in e.entries %}
    <div style="border:1px solid var(--rule2);border-radius:4px;padding:12px">
      <div class="mono"><strong>{{ x.model }}</strong>
        {% if x.blind_label %}<span class="k">judged blind as "{{ x.blind_label }}"</span>{% endif %}</div>
      {% if x.voice %}<div class="mono k">voice {{ x.voice }}</div>{% endif %}
      {% if x.audio %}<audio controls preload="none" src="{{ x.audio }}"></audio>{% endif %}
      <div class="mono">
        {% if x.cell and x.cell.status == 'scored' %}
          score <strong>{{ "%.2f"|format(x.cell.score) }}</strong>
        {% elif x.cell and x.cell.status == 'invalid' %}
          <span class="badge b-bad">invalid &rarr; 0</span>
        {% elif x.cell and x.cell.status == 'unjudged' %}
          <span class="badge b-warn">unjudged &mdash;</span>
        {% elif x.cell and x.cell.status == 'incomplete' %}
          <span class="badge b-warn">n/a &mdash; scenario incomplete</span>
        {% else %}
          <span class="badge b-bad">{{ x.stats.status }}</span>
        {% endif %}
        {% if x.wer is not none %} &middot; WER {{ "%.4f"|format(x.wer) }}{% endif %}
        {% if x.measurements.audio_quality_1_5 %} &middot; audio {{ x.measurements.audio_quality_1_5 }}/5
          {% if not x.measurements.audio_quality_is_mos %}<span class="badge b-warn" title="signal metrics, not a perceptual MOS">not a MOS</span>{% endif %}
        {% endif %}
      </div>
      {% if x.diff %}
      <details open><summary>transcript vs script (normalized)</summary>
        <div class="diff">{% for d in x.diff %}<span class="{{ d.kind }}">{{ d.text }}</span> {% endfor %}</div>
      </details>
      {% endif %}
      {% if x.transcript %}
      <details><summary>raw ASR transcript</summary><div class="mono">{{ x.transcript }}</div></details>
      {% endif %}
      {% if x.judge_reasoning %}
      <details><summary>judge reasoning</summary>
        <ul class="gates">{% for k, v in x.judge_reasoning.items() %}<li><strong>{{ k }}</strong>: {{ v }}</li>{% endfor %}</ul>
      </details>
      {% elif x.judge_error %}
      <div class="mono" style="color:var(--bad)">judge: {{ x.judge_error }}</div>
      {% endif %}
      <details><summary>gates</summary>
        <ul class="gates">{% for g in x.gates %}
          <li>{% if g.passed %}<span style="color:var(--ok)">pass</span>{% else %}<span style="color:var(--bad)">FAIL</span>{% endif %}
            {{ g.gate }} &mdash; {{ g.detail }}</li>{% endfor %}</ul>
      </details>
    </div>
  {% endfor %}
  </div>
</div>
{% endfor %}

<h2>Cost</h2>
<div class="scroll"><table>
  <tr><th>Component</th><th>Total</th></tr>
  <tr><td>Generation</td><td class="num">{{ fmt_usd(totals.gen) }}</td></tr>
  <tr><td>ASR (word accuracy)</td><td class="num">{{ fmt_usd(totals.asr) }}</td></tr>
  <tr><td>Judge</td><td class="num">{{ fmt_usd(totals.judge) }}</td></tr>
  <tr><td><strong>Run total</strong></td><td class="num"><strong>{{ fmt_usd(totals.all) }}</strong></td></tr>
</table></div>
<p class="sub mono">Generation and judging are reported separately and never summed in storage.</p>

<footer>
  <h3>Footnotes</h3>
  <p><strong>Judge.</strong> {{ judge_model }}, temperature 0, one clip per call, labels shuffled per
     scenario, audio re-encoded so no provider metadata reaches it. Judge failure is recorded as
     <em>unjudged</em> and excluded from the mean &mdash; never scored 0.</p>
  <p><strong>Rubrics.</strong>
     {% for task, rb in rubrics.items() %}{{ task }} = {{ rb.rubric_hash[:16] }}&hellip;
     ({{ rb.source_files|join(", ") }}){% if not loop.last %}; {% endif %}{% endfor %}</p>
  <p><strong>Calibration.</strong> {{ calibration.reason }}
     Thresholds: mean |human &minus; judge| &le; {{ calibration.as_record.thresholds.max_mean_abs_diff }},
     rank correlation &ge; {{ calibration.as_record.thresholds.min_rank_correlation }},
     {{ calibration.as_record.thresholds.required_reviewers }} reviewers &times;
     {{ calibration.as_record.thresholds.required_clips }} clips.</p>
  <p><strong>Voices are a declared difference.</strong> Voices are not comparable across providers the
     way sample rates are. One deliberate voice was pinned per provider:
     {% for v in voice_map_rows %}<span class="mono">{{ v.model }}: {{ v.logical }} &rarr; {{ v.provider_voice }}</span>{% if not loop.last %}; {% endif %}{% endfor %}.</p>
  {% if unsupported_rows %}
  <p><strong>Parameters a provider could not honour.</strong>
     {% for m, p in unsupported_rows %}<span class="mono">{{ m }}: {{ p }}</span>{% if not loop.last %}; {% endif %}{% endfor %}.</p>
  {% endif %}
  {% if manifest.mos_fallback_reason %}
  <p><strong>Audio quality predictor.</strong> {{ manifest.mos_fallback_reason }} A signal-metric score
     is a real measurement of the file, but it is <em>not</em> a perceptual MOS and is badged as such.</p>
  {% endif %}
  <p><strong>Estimated costs.</strong> A cost badged <span class="badge b-warn">est</span> was derived
     from the assumptions declared in <span class="mono">configs/models.yaml</span> because the provider
     returned no usage object. Rates themselves are the providers' published list prices with a source
     and an as-of date on every entry.</p>
  {% if skipped %}
  <p><strong>Not attempted.</strong>
     {% for s in skipped %}<span class="mono">{{ s.scenario_id }} &times; {{ s.model_id }}</span> ({{ s.reason }}){% if not loop.last %}; {% endif %}{% endfor %}.
     These are n/a, excluded from every denominator &mdash; not failures and not zeros.</p>
  {% endif %}
  <p class="mono">Two runs a week apart will differ; these models are non-deterministic and providers
     update them silently. A 0.3-point difference between runs is noise, which is why the winner
     threshold is 0.5.</p>
</footer>
</div></body></html>
"""
