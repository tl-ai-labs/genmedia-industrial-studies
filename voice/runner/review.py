"""
The human-review layer: what people heard, kept apart from what code measured.

Two record types live in ONE hand-written YAML file (`review/human-review.yaml`
beside `runs/`; schema in `review/README.md`):

  corrections   an automated result that a human found WRONG, and the right
                result. Applied to the loaded cells before either board is
                rendered, so the automated numbers reflect the corrected
                result - and every corrected cell says so, with what the
                instrument originally said, who corrected it, when and why.

  observations  what a human heard on a scenario, case by case. NEVER
                folded into a score, a gate rate, a verdict or a winner. They
                render in their own block on every card and in their own
                section of the page, beside the automated verdict, so the two
                can be read against each other without being mixed.

WHY A SEPARATE FILE AND NOT AN EDIT. The run folders are append-only
evidence (`telemetry.py`). A correction is a declared overlay on that
evidence, not a rewrite of it: `--no-review` renders the instrument's own
answer untouched, and the diff between the two boards is exactly the list of
corrections. Editing a `.jsonl` would have hidden that list.

WHAT A CORRECTION MAY TOUCH. Only things the instrument MEASURED and got
wrong: a gate verdict (a homophone in the transcript failed a correct
reading), a word error rate (the transcript, not the clip, was wrong), or a
judge score that rested on a wrong measured fact. A correction cannot invent
a score for a clip the judge never scored - that would be a human score
wearing the judge's badge, which is the mixing this module exists to prevent.
Write an observation instead.

LOUD ON MISTAKES. A correction that names a clip, a gate or a run that does
not exist raises rather than being skipped: a silently ignored correction
leaves a wrong number standing on a client-facing page while the file says it
was fixed. A correction whose `was` does not match what the run recorded
raises for the same reason - it was written against a different run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .dashboard import Cell, RunSummary

FIELDS = ("gate", "wer", "score")
PREFERENCES = ("tie", "cant_tell")


class ReviewError(ValueError):
    """A review file that cannot be applied as written."""


@dataclass
class Correction:
    scenario_id: str
    model_id: str
    field: str
    now: Any
    reason: str
    reviewer: str
    date: str
    run_label: str | None = None
    gate: str | None = None
    was: Any = None
    # Filled in by apply_corrections: the run ids the correction reached.
    applied_to: list[str] = field(default_factory=list)

    @property
    def target(self) -> str:
        where = f"{self.scenario_id} × {self.model_id}"
        if self.run_label:
            where += f" ({self.run_label})"
        return where + (f" · gate {self.gate}" if self.field == "gate" else f" · {self.field}")


@dataclass
class Observation:
    scenario_id: str
    reviewer: str
    date: str
    notes: str
    model_id: str | None = None
    run_label: str | None = None
    preference: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Review:
    path: Path | None
    reviewers: list[dict[str, str]] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.corrections and not self.observations

    def observations_for(self, scenario_id: str) -> list[Observation]:
        """Observations on a scenario card: the parent id, variants included."""
        parent = scenario_id.split("#", 1)[0]
        return [o for o in self.observations if o.scenario_id.split("#", 1)[0] == parent]

    def reviewer_name(self, rid: str) -> str:
        for r in self.reviewers:
            if r.get("id") == rid:
                return str(r.get("name") or rid)
        return rid


# ----------------------------------------------------------------- loading ---

def _req(rec: dict, key: str, where: str) -> Any:
    v = rec.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ReviewError(f"{where}: `{key}` is required")
    return v


def load_review(path: Path | None) -> Review:
    """
    Read the file, or return an empty Review when there is none.

    A MISSING file is the normal state before anyone has listened; an
    INVALID file is an error, because a half-read review is worse than none.
    """
    if path is None or not Path(path).exists():
        return Review(path=None)
    import yaml

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ReviewError(f"{path}: the file must be a mapping with `corrections` and `observations`")
    review = Review(path=Path(path))
    for r in doc.get("reviewers") or []:
        if not isinstance(r, dict) or not r.get("id"):
            raise ReviewError(f"{path}: every reviewer needs an `id`")
        review.reviewers.append({k: str(v) for k, v in r.items()})
    known = {r["id"] for r in review.reviewers}

    for i, rec in enumerate(doc.get("corrections") or []):
        where = f"{path}: corrections[{i}]"
        if not isinstance(rec, dict):
            raise ReviewError(f"{where}: must be a mapping")
        fld = str(_req(rec, "field", where))
        if fld not in FIELDS:
            raise ReviewError(f"{where}: `field` must be one of {FIELDS}, not '{fld}'")
        if fld == "gate" and not rec.get("gate"):
            raise ReviewError(f"{where}: a gate correction names the gate in `gate`")
        now = _req(rec, "now", where)
        if fld == "gate" and not isinstance(now, bool):
            raise ReviewError(f"{where}: `now` for a gate is true (passed) or false (failed)")
        if fld in ("wer", "score"):
            try:
                now = float(now)
            except (TypeError, ValueError):
                raise ReviewError(f"{where}: `now` for {fld} must be a number") from None
            lo, hi = (0.0, 10.0) if fld == "score" else (0.0, 1.0)
            if not lo <= now <= hi:
                raise ReviewError(f"{where}: `now` for {fld} must be between {lo} and {hi}")
        reviewer = str(_req(rec, "reviewer", where))
        if known and reviewer not in known:
            raise ReviewError(f"{where}: reviewer '{reviewer}' is not in `reviewers`")
        review.corrections.append(Correction(
            scenario_id=str(_req(rec, "scenario_id", where)),
            model_id=str(_req(rec, "model_id", where)),
            field=fld, now=now, gate=rec.get("gate"), was=rec.get("was"),
            reason=str(_req(rec, "reason", where)).strip(),
            reviewer=reviewer, date=str(_req(rec, "date", where)),
            run_label=(str(rec["run_label"]) if rec.get("run_label") else None),
        ))

    for i, rec in enumerate(doc.get("observations") or []):
        where = f"{path}: observations[{i}]"
        if not isinstance(rec, dict):
            raise ReviewError(f"{where}: must be a mapping")
        reviewer = str(_req(rec, "reviewer", where))
        if known and reviewer not in known:
            raise ReviewError(f"{where}: reviewer '{reviewer}' is not in `reviewers`")
        pref = rec.get("preference")
        review.observations.append(Observation(
            scenario_id=str(_req(rec, "scenario_id", where)),
            reviewer=reviewer, date=str(_req(rec, "date", where)),
            notes=str(_req(rec, "notes", where)).strip(),
            model_id=(str(rec["model_id"]) if rec.get("model_id") else None),
            run_label=(str(rec["run_label"]) if rec.get("run_label") else None),
            preference=(str(pref) if pref else None),
            tags=[str(t) for t in (rec.get("tags") or [])],
        ))
    return review


# ---------------------------------------------------------------- applying ---

def _matches(c: Correction, cell: "Cell") -> bool:
    return (cell.scenario_id == c.scenario_id and cell.model_id == c.model_id
            and (c.run_label is None or cell.run_label == c.run_label))


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a == b


def apply_corrections(runs: list["RunSummary"], review: Review) -> list[dict[str, Any]]:
    """
    Rewrite the loaded cells to the corrected result, in place.

    Returns one record per (correction, cell) pair for the page to list. The
    cell keeps every correction it received in `cell.corrections`, with what
    the instrument said, so a corrected number is never shown without its
    original beside it.
    """
    applied: list[dict[str, Any]] = []
    for c in review.corrections:
        hits = [(r, cell) for r in runs for cell in r.cells if _matches(c, cell)]
        if not hits:
            raise ReviewError(
                f"correction for {c.target} matches no clip in the loaded runs. Scenario ids "
                f"must be exact (a variant is `parent#variant`), and run_label the run's "
                f"own label, e.g. `voice-p1`.")
        for run, cell in hits:
            was = _apply_one(c, cell)
            rec = {"field": c.field, "gate": c.gate, "was": was, "now": c.now,
                   "reason": c.reason, "reviewer": c.reviewer,
                   "reviewer_name": review.reviewer_name(c.reviewer), "date": c.date,
                   "scenario_id": cell.scenario_id, "model_id": cell.model_id,
                   "run_label": cell.run_label, "run_id": run.run_id}
            cell.corrections.append(rec)
            c.applied_to.append(run.run_id)
            applied.append(rec)
    return applied


def _apply_one(c: Correction, cell: "Cell") -> Any:
    """Change one cell; return what the instrument had recorded."""
    if c.field == "gate":
        gate = next((g for g in cell.gates if g.get("gate") == c.gate), None)
        if gate is None:
            raise ReviewError(
                f"correction for {c.target}: the clip has no gate named '{c.gate}'. "
                f"It has: {[g.get('gate') for g in cell.gates]}")
        was = bool(gate.get("passed"))
        if c.was is not None and not _same(c.was, was):
            raise ReviewError(
                f"correction for {c.target}: `was: {c.was}` but the run recorded "
                f"passed={was}. The correction was written against a different run.")
        gate["passed"] = bool(c.now)
        gate["corrected"] = True
        cell.gates_passed = sum(1 for g in cell.gates if g.get("passed"))
        cell.gates_total = len(cell.gates)
        if not all(g.get("passed") for g in cell.gates):
            # A failed gate invalidates the clip, exactly as it would have at
            # run time - the judge's score for it, if any, no longer counts.
            cell.status, cell.score = "invalid", None
        elif cell.status == "invalid":
            # Cleared on review. The judge never heard it (gated clips are
            # not judged), so it has no score - and nobody here invents one.
            cell.status, cell.score = "unjudged", None
            cell.review_note = "cleared its gates on human review; not judged, so it carries no score"
        return was
    if c.field == "wer":
        was = cell.wer
        if c.was is not None and (was is None or not _same(c.was, was)):
            raise ReviewError(
                f"correction for {c.target}: `was: {c.was}` but the run recorded wer={was}.")
        cell.wer = float(c.now)
        return was
    if c.field == "score":
        if cell.status != "scored" or cell.score is None:
            raise ReviewError(
                f"correction for {c.target}: the clip has no automated score to correct "
                f"(status '{cell.status}'). A human score for an unscored clip is an "
                f"observation, not a correction - record it under `observations`.")
        was = cell.score
        if c.was is not None and not _same(c.was, was):
            raise ReviewError(
                f"correction for {c.target}: `was: {c.was}` but the run recorded score={was}.")
        cell.score = float(c.now)
        return was
    raise ReviewError(f"unknown correction field '{c.field}'")  # pragma: no cover


# --------------------------------------------------------------- rendering ---

def review_context(review: Review, applied: list[dict[str, Any]],
                   model_ids: list[str] | None = None) -> dict[str, Any]:
    """
    What the page needs to say about the review as a whole - counts, who,
    when - and the full list of corrections, because a corrected page that
    does not list its corrections is a page a reader cannot check.
    """
    dates = sorted({o.date for o in review.observations} | {c.date for c in review.corrections})
    reviewers = sorted({o.reviewer for o in review.observations}
                       | {c.reviewer for c in review.corrections})
    scen = sorted({o.scenario_id.split("#", 1)[0] for o in review.observations})
    # Where a human named a better model on a card, tally it per model. A
    # tally, never a score - it does not enter any verdict.
    prefs: dict[str, int] = {}
    for o in review.observations:
        if o.preference:
            prefs[o.preference] = prefs.get(o.preference, 0) + 1
    return {
        "present": not review.empty,
        "path": str(review.path) if review.path else None,
        "reviewers": [{"id": r, "name": review.reviewer_name(r)} for r in reviewers],
        "reviewer_names": ", ".join(review.reviewer_name(r) for r in reviewers),
        "dates": dates, "date_span": (dates[0] if len(dates) == 1 else f"{dates[0]} – {dates[-1]}") if dates else "",
        "n_observations": len(review.observations),
        "n_scenarios_reviewed": len(scen),
        "scenarios_reviewed": scen,
        "n_corrections": len(review.corrections),
        "n_cells_corrected": len({(a["run_id"], a["scenario_id"], a["model_id"]) for a in applied}),
        "corrections": sorted(applied, key=lambda a: (a["scenario_id"], a["model_id"], a["run_label"])),
        "preferences": [{"model_id": k, "n": v} for k, v in sorted(prefs.items())],
        "by_field": {f: sum(1 for c in review.corrections if c.field == f) for f in FIELDS},
    }


def observations_block(review: Review, scenario_id: str) -> list[dict[str, Any]]:
    """The human block for one card, oldest first, as plain dicts."""
    out = []
    for o in sorted(review.observations_for(scenario_id), key=lambda o: (o.date, o.reviewer)):
        out.append({
            "scenario_id": o.scenario_id,
            "variant": o.scenario_id.split("#", 1)[1] if "#" in o.scenario_id else "",
            "reviewer": o.reviewer, "reviewer_name": review.reviewer_name(o.reviewer),
            "date": o.date, "notes": o.notes, "model_id": o.model_id,
            "run_label": o.run_label, "preference": o.preference, "tags": o.tags,
        })
    return out
