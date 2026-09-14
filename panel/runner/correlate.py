"""
Human majority against the LLM judge, per lane and overall.

THE QUESTION. Chom's requirement was one number: does the judge agree with
people? It is reported per lane before it is reported overall, because the
judge may be well aligned on image (a proven pattern) and poorly aligned on
voice (prosody, emotion, mispronunciation nuance) - and a pooled figure
would hide exactly that.

WHAT COUNTS AS A VERDICT. Per scenario, the human verdict is the arm with
more picks; equal picks is a tie, and "can't tell" votes count for neither
arm (they are reported as a share, not folded into a side). The judge
verdict is the arm with the higher score, unless the gap is inside the
lane's own tie band - the same TIE_BAND each lane's scoring uses, so the
judge is held to the verdict its report actually printed.

THREE NUMBERS, BECAUSE ONE MISLEADS.
- agreement on decided scenarios: of the scenarios where BOTH sides picked
  an arm, how often the same one. This is the headline, with a Wilson 95%
  interval because n is tens, not thousands.
- three-way agreement and Cohen's kappa over {ref, other, tie}: credits
  the judge for calling a tie a tie, and corrects for chance.
- Spearman's rho between the human margin (share of decisive votes for the
  reference arm) and the judge's score gap: does the judge's *strength* of
  preference track the panel's, not just its direction.

A POOR NUMBER IS A FINDING. The reviewers found Gemini's voice more soothing
while the judge scored it lower. If that shows up here, it is evidence about
the rubric, and it is printed, not smoothed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runs import read_scores
from .stats import cohen_kappa, spearman, wilson

# Each lane's own TIE_BAND (image/runner/scoring.py, video/runner/scoring.py,
# voice/runner/scoring.py). Voice scores on 0-1, the others on 0-10.
TIE_BANDS: dict[str, float] = {"image": 0.5, "video": 0.5, "voice": 0.05}
DEFAULT_TIE_BAND = 0.5

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_vote(v: dict[str, Any]) -> dict[str, Any]:
    """Read both shapes the votes file has carried. Until 2026-09-11 a line
    named the side (`pick`), both models by side (`left_model`,
    `right_model`) and the choice (`picked_model`); since then it names only
    the choice and what it beat (`picked`, `over`). The rest of this module
    reads the older field names, so a new line is given them here - with
    `side_known: False`, because it carries no side and must not count
    toward the left-pick share."""
    v = dict(v)
    if "picked_model" not in v and "picked" in v:
        v["picked_model"] = v["picked"]
    if "left_model" in v and "right_model" in v:
        v["side_known"] = True
    else:
        v["left_model"], v["right_model"] = v.get("picked_model"), v.get("over")
        v["side_known"] = False
    if v.get("pick") not in ("left", "right", "tie"):
        v["pick"] = "tie" if v.get("picked_model") is None else "decided"
    return v


def load_votes(path: Path) -> list[dict[str, Any]]:
    """votes.jsonl, or the Google Sheet's `votes` tab downloaded as CSV
    (File → Download → CSV): same columns, empty cell = null."""
    p = Path(path)
    if p.suffix.lower() == ".csv":
        import csv
        with p.open(newline="", encoding="utf-8") as f:
            return [normalize_vote({k: (v if v != "" else None) for k, v in row.items()})
                    for row in csv.DictReader(f) if row.get("ts")]
    votes = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            votes.append(normalize_vote(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return votes


def dedupe(votes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One vote per (reviewer, lane, run, scenario): the LAST one. The page
    lets a reviewer revisit and change their mind, and the file is
    append-only, so the earlier record stays on disk and stops counting
    here. Keyed on the scenario, not the item id, so a vote cast against an
    earlier export of the same run is the same vote."""
    last: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for v in votes:
        last[(who(v), str(v.get("lane")), str(v.get("run_id")),
              str(v.get("scenario_id")))] = v
    return list(last.values())


def who(v: dict[str, Any]) -> str:
    """The reviewer a vote belongs to: the email (recorded since 2026-09-11),
    or the name for records from before then. One person, one key."""
    return str(v.get("email") or v.get("reviewer") or "").strip().lower()


def reference_model(models: list[str]) -> str:
    """The arm the margin is signed towards: the Gemini arm when there is
    exactly one, else the alphabetically first. Only the sign convention
    depends on this; agreement does not."""
    gem = [m for m in models if "gemini" in m.lower() or "omni" in m.lower()]
    if len(gem) == 1:
        return gem[0]
    return sorted(models)[0]


def _locate_run(lane: str, run_id: str, key: dict[str, Any] | None,
                overrides: dict[tuple[str, str], Path]) -> Path | None:
    """None when the run cannot be found - the caller reports it and skips
    those votes rather than refusing the whole report, because one stale
    vote (a run the panel no longer exports) must not block the number for
    every run it does."""
    if (lane, run_id) in overrides:
        return overrides[(lane, run_id)]
    if key:
        p = ((key.get("runs") or {}).get(lane) or {}).get(run_id)
        if p and Path(p).exists():
            return Path(p)
    cand = REPO_ROOT / lane / "runs" / run_id
    if cand.exists():
        return cand
    return None


def _verdict(n_ref: int, n_oth: int) -> str:
    if n_ref > n_oth:
        return "ref"
    if n_oth > n_ref:
        return "other"
    return "tie"


def _judge_verdict(s_ref: float | None, s_oth: float | None, band: float) -> str:
    if s_ref is None or s_oth is None:
        return "no score"
    d = s_ref - s_oth
    if abs(d) <= band:
        return "tie"
    return "ref" if d > 0 else "other"


def _summarise(rows: list[dict[str, Any]], votes: list[dict[str, Any]]) -> dict[str, Any]:
    decided = [r for r in rows if r["human"] in ("ref", "other")
               and r["judge_verdict"] in ("ref", "other")]
    agree_d = sum(1 for r in decided if r["human"] == r["judge_verdict"])
    threeway = [r for r in rows if r["judge_verdict"] != "no score"]
    agree_3 = sum(1 for r in threeway if r["human"] == r["judge_verdict"])
    corr_rows = [r for r in rows if r["human_margin"] is not None
                 and r["judge_delta"] is not None]
    n_votes = len(votes)
    n_tie = sum(1 for v in votes if v.get("pick") == "tie")
    # Position bias is measurable only on votes that recorded a side.
    n_left = sum(1 for v in votes if v.get("pick") == "left")
    n_side = sum(1 for v in votes if v.get("side_known") and v.get("pick") != "tie")
    consensus = [abs(r["human_margin"]) for r in rows if r["human_margin"] is not None]
    return {
        "n_scenarios": len(rows),
        "n_votes": n_votes,
        "n_reviewers": len({who(v) for v in votes}),
        "cant_tell_share": (n_tie / n_votes) if n_votes else None,
        "left_pick_share": (n_left / n_side) if n_side else None,
        "mean_consensus": (sum(consensus) / len(consensus)) if consensus else None,
        "n_decided": len(decided),
        "agree_decided": (agree_d / len(decided)) if decided else None,
        "agree_decided_ci": wilson(agree_d, len(decided)),
        "n_3way": len(threeway),
        "agree_3way": (agree_3 / len(threeway)) if threeway else None,
        "kappa_3way": cohen_kappa([r["human"] for r in threeway],
                                  [r["judge_verdict"] for r in threeway]),
        "spearman_n": len(corr_rows),
        "spearman": spearman([r["human_margin"] for r in corr_rows],
                             [r["judge_delta"] for r in corr_rows]),
    }


def correlate(votes_path: Path, key_path: Path | None = None,
              overrides: dict[tuple[str, str], Path] | None = None,
              tie_bands: dict[str, float] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    bands = {**TIE_BANDS, **(tie_bands or {})}
    votes = dedupe(load_votes(votes_path))
    key = None
    if key_path and Path(key_path).exists():
        key = json.loads(Path(key_path).read_text(encoding="utf-8"))

    by_scenario: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for v in votes:
        if v.get("pick") not in ("left", "right", "tie", "decided"):
            continue
        by_scenario[(v["lane"], v["run_id"], v["scenario_id"])].append(v)

    scores_cache: dict[tuple[str, str], dict[tuple[str, str], float | None]] = {}
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    for (lane, run_id, sid), vs in sorted(by_scenario.items()):
        if (lane, run_id) not in scores_cache:
            run_dir = _locate_run(lane, run_id, key, overrides)
            if run_dir is None:
                notes.append(f"{lane} run {run_id}: not found, {len(vs)} vote(s) on {sid} "
                             f"skipped - pass --run {lane}:{run_id}=<dir> to include them")
                continue
            scores_cache[(lane, run_id)] = read_scores(run_dir)
            if not scores_cache[(lane, run_id)]:
                notes.append(f"{lane} run {run_id}: scores.jsonl empty or missing at {run_dir}")
        scores = scores_cache[(lane, run_id)]
        models = sorted({m for v in vs for m in (v.get("left_model"), v.get("right_model")) if m})
        if len(models) != 2:
            notes.append(f"{lane}/{sid}: votes name {len(models)} models, skipped")
            continue
        ref = reference_model(models)
        oth = next(m for m in models if m != ref)
        picks = Counter(v.get("picked_model") for v in vs)
        n_ref, n_oth, n_tie = picks.get(ref, 0), picks.get(oth, 0), picks.get(None, 0)
        s_ref, s_oth = scores.get((sid, ref)), scores.get((sid, oth))
        band = bands.get(lane, DEFAULT_TIE_BAND)
        human = _verdict(n_ref, n_oth)
        judge = _judge_verdict(s_ref, s_oth, band)
        rows.append({
            "lane": lane, "run_id": run_id, "scenario_id": sid,
            "ref_model": ref, "other_model": oth,
            "votes_ref": n_ref, "votes_other": n_oth, "votes_tie": n_tie,
            "human": human,
            "human_margin": ((n_ref - n_oth) / (n_ref + n_oth)) if (n_ref + n_oth) else None,
            "judge_ref": s_ref, "judge_other": s_oth,
            "judge_delta": (s_ref - s_oth) if (s_ref is not None and s_oth is not None) else None,
            "judge_verdict": judge, "tie_band": band,
            "agree": (human == judge) if judge != "no score" else None,
        })

    lanes: dict[str, Any] = {}
    for lane in sorted({r["lane"] for r in rows}):
        lane_rows = [r for r in rows if r["lane"] == lane]
        lane_votes = [v for v in votes if v.get("lane") == lane]
        lanes[lane] = _summarise(lane_rows, lane_votes)
        lanes[lane]["tie_band"] = bands.get(lane, DEFAULT_TIE_BAND)
    return {
        "generated": _utc(),
        "votes_file": str(votes_path),
        "lanes": lanes,
        "overall": _summarise(rows, votes),
        "scenarios": rows,
        "notes": notes,
    }


# ---------------------------------------------------------------- rendering

def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _num(x: float | None, d: int = 2) -> str:
    return "—" if x is None else f"{x:+.{d}f}"


def _ci(ci: tuple[float, float] | None) -> str:
    return "" if ci is None else f" ({ci[0] * 100:.0f}–{ci[1] * 100:.0f}%)"


def render_markdown(rep: dict[str, Any]) -> str:
    L = [f"# Human panel vs LLM judge — {rep['generated']}", "",
         f"Votes: `{rep['votes_file']}`", "",
         "| Lane | Scenarios | Votes | Reviewers | Agreement (decided) | 3-way | κ | Spearman ρ | Can't tell | Left picks |",
         "|---|---|---|---|---|---|---|---|---|---|"]

    def row(name: str, s: dict[str, Any]) -> str:
        return (f"| {name} | {s['n_scenarios']} | {s['n_votes']} | {s['n_reviewers']} "
                f"| {_pct(s['agree_decided'])}{_ci(s['agree_decided_ci'])} of {s['n_decided']} "
                f"| {_pct(s['agree_3way'])} of {s['n_3way']} | {_num(s['kappa_3way'])} "
                f"| {_num(s['spearman'])} (n={s['spearman_n']}) "
                f"| {_pct(s['cant_tell_share'])} | {_pct(s['left_pick_share'])} |")

    for lane, s in rep["lanes"].items():
        L.append(row(f"**{lane}** (tie band {s['tie_band']:g})", s))
    L.append(row("**overall**", rep["overall"]))
    L += ["",
          "- **Agreement (decided)**: scenarios where both the panel majority and the judge "
          "picked an arm, and it was the same arm. Wilson 95% interval in brackets.",
          "- **3-way / κ**: agreement counting ties as a verdict, and Cohen's kappa for it.",
          "- **Spearman ρ**: human margin (share of decisive votes for the reference arm) "
          "against the judge's score gap, across scenarios.",
          "- **Left picks**: share of decisive votes for whichever side was on the left. "
          "Far from 50% means position, not quality, is driving votes.",
          "", "## Scenarios", "",
          "| Lane | Scenario | Reference arm | Votes ref / other / can't tell | Panel | Judge ref / other | Judge | Agree |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rep["scenarios"]:
        agree = "—" if r["agree"] is None else ("yes" if r["agree"] else "**no**")
        js = ("—" if r["judge_ref"] is None else f"{r['judge_ref']:.2f}") + " / " + \
             ("—" if r["judge_other"] is None else f"{r['judge_other']:.2f}")
        L.append(f"| {r['lane']} | {r['scenario_id']} | {r['ref_model']} "
                 f"| {r['votes_ref']} / {r['votes_other']} / {r['votes_tie']} | {r['human']} "
                 f"| {js} | {r['judge_verdict']} | {agree} |")
    if rep["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in rep["notes"]]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------ html render

HTML_TITLE = "GenMedia blind panel — human vs judge"


def _esc(x: Any) -> str:
    import html as _html
    return _html.escape("" if x is None else str(x))


def render_html(rep: dict[str, Any]) -> str:
    """A self-contained page of the same report, for the studies console
    (`apps/dashboard/public/reports/genmedia-blind-panel/results.html`).
    No external assets, no script: it is read inside an iframe and must
    stand on its own wherever it is copied."""
    def pct(x): return "—" if x is None else f"{x * 100:.0f}%"
    def num(x): return "—" if x is None else f"{x:+.2f}"
    def ci(c): return "" if c is None else f" <span class=dim>({c[0] * 100:.0f}–{c[1] * 100:.0f}%)</span>"

    def summary_row(name: str, s: dict[str, Any], band: float | None) -> str:
        label = _esc(name) + (f" <span class=dim>tie band {band:g}</span>" if band is not None else "")
        return ("<tr><th>" + label + "</th>"
                f"<td>{s['n_scenarios']}</td><td>{s['n_votes']}</td><td>{s['n_reviewers']}</td>"
                f"<td><b>{pct(s['agree_decided'])}</b>{ci(s['agree_decided_ci'])} <span class=dim>of {s['n_decided']}</span></td>"
                f"<td>{pct(s['agree_3way'])} <span class=dim>of {s['n_3way']}</span></td>"
                f"<td>{num(s['kappa_3way'])}</td>"
                f"<td>{num(s['spearman'])} <span class=dim>n={s['spearman_n']}</span></td>"
                f"<td>{pct(s['cant_tell_share'])}</td><td>{pct(s['left_pick_share'])}</td></tr>")

    rows = [summary_row(lane, s, s.get("tie_band")) for lane, s in rep["lanes"].items()]
    rows.append(summary_row("overall", rep["overall"], None))

    def scen_row(r: dict[str, Any]) -> str:
        agree = "—" if r["agree"] is None else ("yes" if r["agree"] else "<b class=no>no</b>")
        js = ("—" if r["judge_ref"] is None else f"{r['judge_ref']:.2f}") + " / " + \
             ("—" if r["judge_other"] is None else f"{r['judge_other']:.2f}")
        return (f"<tr><td>{_esc(r['lane'])}</td><td><code>{_esc(r['scenario_id'])}</code></td>"
                f"<td><code>{_esc(r['ref_model'])}</code> <span class=dim>vs</span> <code>{_esc(r['other_model'])}</code></td>"
                f"<td>{r['votes_ref']} / {r['votes_other']} / {r['votes_tie']}</td><td>{_esc(r['human'])}</td>"
                f"<td>{js}</td><td>{_esc(r['judge_verdict'])}</td><td>{agree}</td></tr>")

    scen = "".join(scen_row(r) for r in rep["scenarios"]) or \
        "<tr><td colspan=8 class=dim>No votes yet.</td></tr>"
    notes = "".join(f"<li>{_esc(n)}</li>" for n in rep["notes"])
    n_votes = rep["overall"]["n_votes"]
    n_rev = rep["overall"]["n_reviewers"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{HTML_TITLE}</title>
<style>
  body {{ margin: 0; padding: 28px 32px 60px; color: #17181c; background: #fff;
         font: 14.5px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif; }}
  h1 {{ font-size: 22px; letter-spacing: -.02em; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .08em; color: #6b7280; margin: 34px 0 10px; }}
  .sub {{ color: #4b4f58; margin: 0 0 18px; }}
  .kpis {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 18px 0 8px; }}
  .kpi {{ border: 1px solid #e6e8ec; border-radius: 12px; padding: 12px 16px; min-width: 150px; }}
  .kpi b {{ display: block; font-size: 24px; letter-spacing: -.02em; }}
  .kpi span {{ font-size: 12px; color: #6b7280; }}
  table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #e6e8ec; vertical-align: top; }}
  thead th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: #6b7280; font-weight: 600; }}
  tbody th {{ font-weight: 600; text-transform: capitalize; }}
  code {{ font: 12.5px ui-monospace, Menlo, monospace; background: #f4f5f7; padding: 1px 5px; border-radius: 4px; }}
  .dim {{ color: #8a8f9a; font-weight: 400; }}
  .no {{ color: #b3261e; }}
  .wrap {{ overflow-x: auto; }}
  ul.method {{ color: #4b4f58; padding-left: 18px; }}
  ul.method li {{ margin: 4px 0; }}
</style></head><body>
<h1>Blind human panel vs LLM judge</h1>
<p class=sub>Generated {_esc(rep['generated'])} from <code>{_esc(Path(rep['votes_file']).name)}</code>.
Per scenario, the panel majority is compared with the judge's verdict from that run's <code>scores.jsonl</code>;
the judge's tie band is each lane's own.</p>
<div class=kpis>
  <div class=kpi><b>{pct(rep['overall']['agree_decided'])}</b><span>agreement on decided scenarios (overall, n={rep['overall']['n_decided']})</span></div>
  <div class=kpi><b>{n_votes}</b><span>votes from {n_rev} reviewer{'' if n_rev == 1 else 's'}</span></div>
  <div class=kpi><b>{rep['overall']['n_scenarios']}</b><span>scenarios with at least one vote</span></div>
</div>
<h2>Per lane and overall</h2>
<div class=wrap><table><thead><tr><th>Lane</th><th>Scenarios</th><th>Votes</th><th>Reviewers</th>
<th>Agreement (decided)</th><th>3-way</th><th>κ</th><th>Spearman ρ</th><th>Can't tell</th><th>Left picks</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<ul class=method>
  <li><b>Agreement (decided)</b>: scenarios where both the panel majority and the judge picked an arm, and it was the same arm. Wilson 95% interval in brackets.</li>
  <li><b>3-way / κ</b>: agreement counting ties as a verdict, and Cohen's kappa for it.</li>
  <li><b>Spearman ρ</b>: human margin (share of decisive votes for the reference arm) against the judge's score gap, across scenarios.</li>
  <li><b>Left picks</b>: share of decisive votes for whichever side was on the left. Far from 50% means position, not quality, is driving votes.</li>
</ul>
<h2>Scenarios</h2>
<div class=wrap><table><thead><tr><th>Lane</th><th>Scenario</th><th>Arms (reference vs other)</th>
<th>Votes ref / other / can't tell</th><th>Panel</th><th>Judge ref / other</th><th>Judge</th><th>Agree</th></tr></thead>
<tbody>{scen}</tbody></table></div>
{('<h2>Notes</h2><ul class=method>' + notes + '</ul>') if notes else ''}
</body></html>
"""
