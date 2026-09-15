"""
Voice from the committed dashboard export, when the runs are not to hand.

WHY THIS EXISTS. `voice/runs/` is gitignored and lives with the voice
session; what travels with the repo is `voice/dashboard/` - the rendered
report plus the 242 MP3s it plays. The ticket names that folder as the
voice source, so this reader turns it into the same `RunData` the run
folder reader produces, and everything downstream (opaque ids, key,
votes, correlation) is unchanged.

WHAT IS READ. One `<article class="sc">` per scenario: its industry
(`data-ind`), title, script, and per model the clips with their score
pill and audio path. Clip filenames are `<sid>--<model>--[<variant>--]<pass>.mp3`;
the variant (`bare`/`nato`, `engineer`/`quartermaster`, `r01`..`r30`) is
what distinguishes two reads of one scenario, the pass is a repeat.

ONE ITEM PER SCENARIO BY DEFAULT. Some scenarios carry 30 variants x 2
passes of throughput clips; showing them all is a 100-item voice session
that nobody finishes. The default picks, per scenario, the first
(variant, pass) where BOTH models have a clip - preferring one where both
were scored - so the panel sees 17 voice items, as the ticket sized it.
`items="all"` emits every matched pair instead.

SCORES. The pill is the judge's score as a percentage. The dashboard
prints `0.0%` for a clip that failed its gates and was never judged, so a
zero pill is read as "no score", not as a score of zero - a scored voice
clip does not land on exactly 0. A synthetic `scores.jsonl` is written
beside the key so the correlation reads it the same way as a real run.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .runs import Arm, Pair, RunData

RUN_ID = "voice-dashboard"

_CARD = re.compile(
    r'<article class="sc" data-ind="([^"]*)"(.*?)</article>', re.S)
_SID = re.compile(r"<h3><code>([^<]+)</code>(.*?)</h3>", re.S)
_SCRIPT = re.compile(r'(?:<div class="vlab"><code>([^<]*)</code></div>\s*)?<p class="script">(.*?)</p>', re.S)
_CLIP = re.compile(
    r'<div class="clip">.*?<span class="rl">([^<]*)</span>\s*'
    r'(?:<span class="sc-pill">([^<]*)</span>)?.*?<audio[^>]*src="([^"]+)"', re.S)
_FNAME = re.compile(r"^(?P<sid>.+?)--(?P<model>[^-].*?)--(?:(?P<variant>[^-][^/]*?)--)?(?P<label>[^/]*)\.mp3$")


def _score(pill: str) -> float | None:
    pill = pill.strip().rstrip("%")
    if not pill:
        return None
    try:
        v = float(pill) / 100.0
    except ValueError:
        return None
    return v if v > 0 else None


def _parse_name(src: str, sid: str) -> tuple[str, str, str] | None:
    """audio/<sid>--<model>--[<variant>--]<label>.mp3 -> (model, variant, label)"""
    name = Path(src).name
    if not name.startswith(sid + "--") or not name.endswith(".mp3"):
        return None
    rest = name[len(sid) + 2:-4]
    parts = rest.split("--")
    if len(parts) == 2:
        return parts[0], "", parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return None


def load_dashboard(dashboard_dir: Path, scores_dir: Path, items: str = "scenario") -> RunData:
    dashboard_dir = Path(dashboard_dir).resolve()
    page = dashboard_dir / "index.html"
    if not page.exists():
        raise ValueError(f"{dashboard_dir}: no index.html")
    text = page.read_text(encoding="utf-8")
    scores_dir = Path(scores_dir).resolve()
    scores_dir.mkdir(parents=True, exist_ok=True)
    data = RunData(lane="voice", run_id=RUN_ID, run_dir=scores_dir)
    score_rows: list[dict] = []

    for ind, body in _CARD.findall(text):
        m = _SID.search(body)
        if not m:
            continue
        sid = m.group(1).strip()
        title = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        industry = html.unescape(ind).strip()
        # A variant scenario labels each script with its variant (`vlab`);
        # the item for one variant shows only that line, not all six.
        scripts: list[tuple[str, str]] = [
            (v.strip(), html.unescape(re.sub(r"<[^>]+>", "", t)).strip())
            for v, t in _SCRIPT.findall(body)]
        by_variant = {v: t for v, t in scripts if v}
        brief_all = "\n\n".join(dict.fromkeys(t for _, t in scripts if t))

        # (variant, label) -> {model: (path, score)}
        clips: dict[tuple[str, str], dict[str, tuple[Path, float | None]]] = {}
        for _label, pill, src in _CLIP.findall(body):
            parsed = _parse_name(src, sid)
            if parsed is None:
                continue
            model, variant, label = parsed
            path = dashboard_dir / html.unescape(src)
            if not path.exists():
                continue
            clips.setdefault((variant, label), {})[model] = (path, _score(pill))

        matched = [(k, v) for k, v in sorted(clips.items()) if len(v) == 2]
        if not matched:
            data.skipped.append((sid, "no (variant, pass) with a clip from both models"))
            continue
        if items != "all":
            scored = [(k, v) for k, v in matched
                      if all(s is not None for _, s in v.values())]
            matched = [(scored or matched)[0]]

        for (variant, label), by_model in matched:
            scenario_id = f"{sid}#{variant}" if variant else sid
            if items == "all":
                scenario_id = f"{scenario_id}@{label}"
            arms = []
            for model in sorted(by_model):
                path, score = by_model[model]
                arms.append(Arm(model_id=model, media=path, score=score,
                                status="scored" if score is not None else "unscored"))
                score_rows.append({"run_id": RUN_ID, "scenario_id": scenario_id,
                                   "model_id": model, "score": score,
                                   "status": "scored" if score is not None else "unscored",
                                   "source": str(path.relative_to(dashboard_dir))})
            data.pairs.append(Pair(
                lane="voice", run_id=RUN_ID, run_dir=scores_dir, scenario_id=scenario_id,
                task="text_to_speech", title=title,
                brief=by_variant.get(variant) or brief_all, inputs=[], arms=arms,
                industry=industry))

    with (scores_dir / "scores.jsonl").open("w", encoding="utf-8") as f:
        for row in score_rows:
            f.write(json.dumps(row) + "\n")
    (scores_dir / "manifest.json").write_text(json.dumps(
        {"run_id": RUN_ID, "modality": "voice", "state": "imported",
         "source": str(dashboard_dir), "note": "synthesised from the committed voice dashboard; "
         "scores are the pills the dashboard printed"}, indent=1), encoding="utf-8")
    return data
