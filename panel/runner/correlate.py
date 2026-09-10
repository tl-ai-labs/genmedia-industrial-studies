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


def load_votes(path: Path) -> list[dict[str, Any]]:
    votes = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            votes.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return votes


def dedupe(votes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One vote per (reviewer, item): the LAST one. The page lets a reviewer
    revisit and change their mind, and the file is append-only, so the
    earlier record stays on disk and stops counting here."""
    last: dict[tuple[str, str], dict[str, Any]] = {}
    for v in votes:
        last[(str(v.get("reviewer")), str(v.get("item")))] = v
    return list(last.values())


def reference_model(models: list[str]) -> str:
    """The arm the margin is signed towards: the Gemini arm when there is
    exactly one, else the alphabetically first. Only the sign convention
    depends on this; agreement does not."""
    gem = [m for m in models if "gemini" in m.lower() or "omni" in m.lower()]
    if len(gem) == 1:
        return gem[0]
    return sorted(models)[0]


def _locate_run(lane: str, run_id: str, key: dict[str, Any] | None,
                overrides: dict[tuple[str, str], Path]) -> Path:
    if (lane, run_id) in overrides:
        return overrides[(lane, run_id)]
    if key:
        p = ((key.get("runs") or {}).get(lane) or {}).get(run_id)
        if p and Path(p).exists():
            return Path(p)
    cand = REPO_ROOT / lane / "runs" / run_id
    if cand.exists():
        return cand
    raise SystemExit(f"cannot find {lane} run {run_id}: pass --run {lane}:{run_id}=<dir>")


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
    n_left = sum(1 for v in votes if v.get("pick") == "left")
    n_side = n_votes - n_tie
    consensus = [abs(r["human_margin"]) for r in rows if r["human_margin"] is not None]
    return {
        "n_scenarios": len(rows),
        "n_votes": n_votes,
        "n_reviewers": len({v.get("reviewer") for v in votes}),
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
        if v.get("pick") not in ("left", "right", "tie"):
            continue
        by_scenario[(v["lane"], v["run_id"], v["scenario_id"])].append(v)

    scores_cache: dict[tuple[str, str], dict[tuple[str, str], float | None]] = {}
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    for (lane, run_id, sid), vs in sorted(by_scenario.items()):
        if (lane, run_id) not in scores_cache:
            run_dir = _locate_run(lane, run_id, key, overrides)
            scores_cache[(lane, run_id)] = read_scores(run_dir)
            if not scores_cache[(lane, run_id)]:
                notes.append(f"{lane} run {run_id}: scores.jsonl empty or missing at {run_dir}")
        scores = scores_cache[(lane, run_id)]
        models = sorted({m for v in vs for m in (v["left_model"], v["right_model"])})
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
