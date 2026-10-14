"""Client and internal HTML reports for the October image run, in the study site's design.

One self-contained file: the GenMedia study page's look (studies.adlc.tilicho.in,
/studies/genmedia-model-comparison/models/image — fonts, colours, cards, chips,
tables, head-to-head card, scenario rows, lightbox), with the Win Map: industry,
use case, task type, difficulty, theme and target customer, with each model's wins, win %, pass
criteria met, quality mean, reliability and latency p50 per value, sortable by
any of them (default: the Google model's win rate, highest first).

Wording follows the study site (origin/main 056de8bd): labels, measure names,
measure descriptions and notes are the site's; everything else on the page is
a value read from the run files.

    python tools/oct_client_report.py --run <id> --out <file.html>
        [--audience client|internal] [--run <id> ...] [--caveats <input-caveats.json>]
        [--media-base-url URL | --link-full] [--thumb-px 480] [--frame-px 360]
        [--models-config <yaml>] [--sources <sources.jsonl>] [--bank-inputs <inputs.json>]
        [--test-run <id> ...]

Audiences (one generator, one data path):
  client    head-to-head card, Win Map, per-scenario evidence, method facts.
  internal  everything in client, plus the bank's internal categories (levers,
            run-plan edge, bank, input caveat) and a "Run details" section of
            tables read from the run files: timeline and phase durations,
            latency distribution, throughput, attempts and rate limits, refusals
            and failures with their recorded reasons, cells retried, cost
            (generation, judge, input-image generation, test runs) and the
            input-caveat list with reasons.

One run is the normal case. Several --run ids merge into one report (a resumed
run, say): each SCENARIO is reported from the latest run given in which both
models hold a score (else the latest run that has it), and everything about it
(scenario definition, images, scores, checklist answers, cost, latency) comes
from that one run, so a scenario never compares two runs. Runs whose rubric
hashes differ for the same task are refused (--allow-rubric-mix overrides, for
tests only). Nothing in a run folder is written.

Full-size links: written only when the HTML sits inside a run folder (relative
links into that run), when --media-base-url is given (<url>/<run-id>/<path in
run>), or with --link-full (relative links, for internal copies). A file sent
on its own carries no links and no local paths.

Exit status: 0 ok; 3 when the canvas check finds a scenario where the two
models did not deliver the same pixel size, or one arm ran at mixed quality
(the report is still written, with the mismatch flagged on the page).

What the numbers are
  * Winner per scenario: the two scenario scores compared at two decimals
    (runner/scoring.py SCORE_DECIMALS); only an identical score is a tie.
  * Win % = wins / scenarios both models completed in that row (ties count for
    neither), rounded to whole numbers, half to even.
  * Quality means are taken over the unrounded scenario scores.
  * Pass: the bank's pass rule as scoring.py applied it (scores.jsonl `pass`);
    scenarios without a rule (the v2 bank's) count in scores and wins only.
  * Cost per image: list price from each provider's reported usage, summed over
    the successful generation calls of the compared scenarios, per image made.
    Judge cost is not included. Latency p50: median of telemetry latency_ms over
    the same calls (one per image; a set counts each image). latency_ms times the
    provider call only: the rpm pacer wait and retry backoff are outside it
    (runner/generate.py).
  * Reliability: the lowest scenario score over the compared scenarios.

Presentation data travels as one JSON blob, images as a second (JPEG data URIs,
deduplicated by content); a small vanilla script renders both. The data is keyed
by media type (MEDIA), so video and voice lanes can be added beside "image"
later: the page's figure() already branches on the media key.
Read-only on the run folders; no network, no paid calls.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import statistics
import sys
import urllib.parse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]                 # image/
RUNS = ROOT / "runs"
BANK = ROOT / "scenarios" / "bank-oct"
DEFAULT_CAVEATS = BANK / "_source" / "input-caveats.json"
DEFAULT_SOURCES = ROOT / "assets" / "bank-oct" / "sources.jsonl"
DEFAULT_BANK_INPUTS = ROOT / "assets" / "bank-oct" / "inputs.json"
DEFAULT_LOGO = ROOT.parents[1] / "ai-sdlc-multi-model-orchestration" / "dashboard" / "public" / "tilicho-brand-logo.webp"

SCORE_DECIMALS = 2          # runner/scoring.py: compared at the two decimals the report shows
CONFIDENT_MARGIN_PP = 5     # the site's "high confidence wins" switch (lib/genmedia.ts)

# Names on the page: the site's rule is "the models alone, never their makers".
DISPLAY = {
    "gemini-nano-banana-2-1-vertex": {"name": "Nano Banana 2.1", "short": "Nano Banana 2.1"},
    "gpt-image-2-5-sunburst-high": {"name": "GPT Image 2.5 (high)", "short": "GPT Image 2.5"},
}
SIZE_WARN_BYTES = 20_000_000

# Per media type: the words the page uses. Only "image" is built today; a video
# or voice builder adds its own entry and the page follows (figure() branches
# on the key). {a} / {b} are the two models' names.
MEDIA = {
    "image": {
        "label": "Image", "desc": "Text-to-image and image editing", "unit": "image", "units": "images",
    },
}

# The v2 bank spells industries its own way; the v3 bank has six. Folded to the
# six where the meaning carries over; the rest have no current home and are
# grouped (decision 2026-10-09; the internal report lists the mapping).
OTHER_INDUSTRY = "Cross-industry & other"
V3_INDUSTRIES = ["Film, OTT & micro-drama", "Advertising & agencies", "E-commerce, D2C & retail",
                 "Gaming", "Media, publishing & events", "Real estate, interiors & architecture"]
INDUSTRY_V3 = {
    "Ads": "Advertising & agencies",
    "Ecommerce & Retail": "E-commerce, D2C & retail",
    "Fashion & Beauty": "E-commerce, D2C & retail",
    "Micro-drama & entertainment": "Film, OTT & micro-drama",
    "Architecture & Interiors": "Real estate, interiors & architecture",
    "Gaming": "Gaming",
    "Food & Beverage": OTHER_INDUSTRY,
    "Travel & Hospitality": OTHER_INDUSTRY,
    "Education & Explainers": OTHER_INDUSTRY,
    "Cross-industry capability probe": OTHER_INDUSTRY,
}
USE_CASES = ["Multi-angle coverage", "Typography and layout", "Character continuity",
             "Composition and aesthetic", "Product and brand", "Storyboards",
             "Depth, light and physics", "Production sheets", "Precision editing"]
THEMES = ["Storytelling & drama", "Product showcase", "Sales & offers", "People & lifestyle",
          "Places & spaces", "Characters & worlds", "Brand, text & graphics", "Art & illustration",
          "Explainer & how-to", "Live events & moments"]
TASKS = {"text_to_image": "Generate from a text prompt", "image_edit": "Edit an existing image"}  # the bank site's words
EDGES = ["Even contest", "Gemini-lean"]
# v3 lever tags, as the bank writes them; order = how often the bank uses them
LEVER_ORDER = ["identity", "locale", "exact-text", "count", "layout", "set", "continuity", "light-lock",
               "camera", "script", "keep-rest", "long-brief", "small-text", "physics", "multi-step",
               "multi-ref", "unseen", "exclude", "time-code"]
NO_LEVERS = "none (v2 bank)"
# set modes in the bank's own words (scenario-bank/v3/image.json set.mode)
OUTPUTS = {None: "Single image", "each": "Each output is its own request, from the same inputs",
           "panels": "One image, every output a panel in it"}
CAVEAT_LEVELS = {"no": "not usable", "partial": "partial"}
# Google's prompt-block codes, with the API reference's own definition of each
# (ai.google.dev/api/generate-content, PromptFeedback.BlockReason, read 2026-10-12).
# A blocked prompt carries no other message, so the definition is all there is.
GOOGLE_BLOCK_REASONS = {
    "SAFETY": "Prompt was blocked due to safety reasons.",
    "OTHER": "Prompt was blocked due to unknown reasons.",
    "BLOCKLIST": "Prompt was blocked due to the terms which are included from the terminology blocklist.",
    "PROHIBITED_CONTENT": "Prompt was blocked due to prohibited content.",
    "IMAGE_SAFETY": "Candidates blocked due to unsafe image generation content.",
}
DIFF_RANK = {("v2", "Straightforward"): 0, ("v2", "Moderate"): 1, ("v3", "Moderate"): 1.5,
             ("v2", "High"): 2, ("v3", "Hard"): 2.5, ("v3", "Extreme"): 3}
# rubric category names as the bank writes them (scenario-bank/v3/image.json rubric[].name);
# a criterion the bank does not name is shown with spaces, as the site does
CRITERIA = {"set_consistency": "Set consistency", "reference_fidelity": "Reference fidelity",
            "prompt_adherence": "Prompt adherence", "text_accuracy_judged": "Text accuracy",
            "visual_quality": "Visual quality", "physics_plausibility": "Physics plausibility",
            "realism_style": "Style", "composition": "Composition", "preservation_judged": "Preservation"}


def crit_name(name: str) -> str:
    return CRITERIA.get(name) or name.replace("_", " ").capitalize()


# --------------------------------------------------------------------------
# Reading runs (read-only)
# --------------------------------------------------------------------------

def jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue        # a half-written last line
    return rows


class Run:
    def __init__(self, ref: str):
        p = Path(ref)
        self.dir = p if p.is_dir() else RUNS / ref
        if not (self.dir / "manifest.json").exists():
            raise SystemExit(f"not a run folder: {self.dir}")
        self.id = self.dir.name
        self.manifest = json.loads((self.dir / "manifest.json").read_text())
        self.scenarios = {}
        for f in sorted((self.dir / "scenarios").glob("*.yaml")):
            d = yaml.safe_load(f.read_text())
            self.scenarios[d["id"]] = d
        key = lambda r: (r["scenario_id"], r["model_id"])   # noqa: E731
        self.score_rows = jsonl(self.dir / "scores.jsonl")
        self.scores = {}
        for r in self.score_rows:                           # latest row per cell wins
            self.scores[key(r)] = r
        self.judge_rows = jsonl(self.dir / "judge.jsonl")
        self.judge = {}
        for r in self.judge_rows:
            if r.get("status") == "judged":
                self.judge[key(r)] = r
        self.check_rows = jsonl(self.dir / "checks.jsonl")
        self.checks = {}
        for r in self.check_rows:
            self.checks[key(r)] = r
        self.tel = jsonl(self.dir / "telemetry.jsonl")
        self.calls: dict = {}
        self.failed_calls: dict = {}
        for r in self.tel:
            if r.get("status") == "ok":
                self.calls.setdefault(key(r), []).append(r)
            else:
                self.failed_calls.setdefault(key(r), []).append(r)
        self.cells = {(c["scenario_id"], c["model_id"]): c
                      for c in self.manifest.get("cells", {}).values()}
        self.judge_model = next((r.get("judge", {}).get("provider_model")
                                 for r in self.judge.values() if r.get("judge")), None)


def numeric(srow: dict | None) -> bool:
    return bool(srow) and srow.get("status") in ("scored", "invalid") and srow.get("score") is not None


class Pick:
    """The run a (scenario, model) cell is reported from."""
    def __init__(self, run: Run, cell: dict, srow: dict | None):
        self.run, self.cell, self.srow = run, cell, srow
        self.numeric = numeric(srow)


def check_rubrics(runs: list[Run], allow_mix: bool) -> list[str]:
    """Runs merged into one report must have scored each task with the same rubric."""
    seen: dict = {}
    clashes = []
    for run in runs:
        for task, h in (run.manifest.get("rubric_hashes") or {}).items():
            if task in seen and seen[task][1] != h:
                clashes.append(f"{task}: {seen[task][0]} vs {run.id}")
            seen.setdefault(task, (run.id, h))
    if clashes and not allow_mix:
        raise SystemExit("refusing to merge runs scored with different rubrics (" + "; ".join(clashes) +
                         "). Report one run, or pass --allow-rubric-mix for a test render.")
    return clashes


def merge(runs: list[Run], arm_ids: tuple[str, str]) -> tuple[dict, dict]:
    """Pick ONE run per scenario: the latest run given in which both models hold a
    score, else the latest run that has the scenario at all. Every cell, image and
    the scenario definition then come from that run, so no scenario compares two
    runs and no checklist is shown against another run's answers.
    Returns ({(sid, mid): Pick}, {sid: scenario definition})."""
    chosen: dict = {}
    for run in runs:                                        # later runs win ties
        sids = {sid for (sid, mid), cell in run.cells.items()
                if mid in arm_ids and cell.get("state") != "skipped" and sid in run.scenarios}
        for sid in sids:
            both = all(numeric(run.scores.get((sid, m))) for m in arm_ids)
            prev = chosen.get(sid)
            if prev is None or both or not prev[1]:
                chosen[sid] = (run, both)
    picks: dict = {}
    scen: dict = {}
    for sid, (run, _) in chosen.items():
        scen[sid] = run.scenarios[sid]
        for mid in arm_ids:
            cell = run.cells.get((sid, mid))
            if cell and cell.get("state") != "skipped":
                picks[(sid, mid)] = Pick(run, cell, run.scores.get((sid, mid)))
    return picks, scen


# --------------------------------------------------------------------------
# Images: compressed JPEG data, deduplicated by content
# --------------------------------------------------------------------------

class Linker:
    """Full-size links, written only where they will work and leak nothing:
    under --media-base-url, or (internal audience only) when the HTML sits inside
    a run folder or with --link-full. Otherwise None, and the page shows no link."""
    def __init__(self, out_dir: Path, runs: list[Run], base_url: str | None, force: bool, relative_ok: bool = True):
        self.out_dir = out_dir.resolve()
        self.runs = [(r, r.dir.resolve()) for r in runs]
        self.base = (base_url or "").rstrip("/")
        self.inside = any(self.out_dir == d or d in self.out_dir.parents for _, d in self.runs)
        self.relative = relative_ok and (self.inside or force)

    @property
    def mode(self) -> str:
        return "base-url" if self.base else ("relative" if self.relative else "none")

    def __call__(self, path: Path | None) -> str | None:
        if not path:
            return None
        p = path.resolve()
        run = next(((r, d) for r, d in self.runs if d in p.parents), None)
        if run is None:
            return None
        if self.base:
            return self.base + "/" + urllib.parse.quote(f"{run[0].id}/{p.relative_to(run[1]).as_posix()}")
        if self.relative:
            return urllib.parse.quote(os.path.relpath(p, self.out_dir).replace(os.sep, "/"))
        return None


class Images:
    def __init__(self, link: Linker, quality: int, frame_quality: int):
        self.link = link
        self.q, self.fq = quality, frame_quality
        self.data: dict[str, str] = {}
        self.bytes_by_kind: dict[str, list[int]] = {}

    def add(self, path: Path | None, px: int, kind: str) -> dict | None:
        if not path or not path.exists():
            return None
        from PIL import Image
        raw = path.read_bytes()
        q = self.fq if kind == "frame" else self.q
        iid = hashlib.sha1(raw + f"|{px}|{q}".encode()).hexdigest()[:12]
        if iid not in self.data:
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")
                im.thumbnail((px, px), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
            b = buf.getvalue()
            self.data[iid] = base64.b64encode(b).decode()
            self.bytes_by_kind.setdefault(kind, []).append(len(b))
        else:
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
        out = {"img": iid, "w": w, "h": h}
        href = self.link(path)
        if href:
            out["href"] = href
        return out


MEDIA_KEY = "image"      # the lane this builder reads (outputs/<media>/...)


def find_output(run: Run, sid: str, mid: str) -> Path | None:
    d = run.dir / "outputs" / MEDIA_KEY / sid
    for f in sorted(d.glob(f"{mid}.*")):
        if ".invalid" not in f.name and f.suffix != ".json":
            return f
    return None


def find_frames(run: Run, sid: str, mid: str) -> dict[int, Path]:
    found: dict[int, Path] = {}
    base = run.dir / "outputs" / MEDIA_KEY / sid
    for d in (base, base / "frames"):           # older runs kept frames beside the sheet
        for f in sorted(d.glob(f"{mid}__f*.*")):
            if ".invalid" in f.name or f.suffix == ".json":
                continue
            try:
                found[int(f.stem.rsplit("__f", 1)[1])] = f
            except ValueError:
                continue
    return found


# --------------------------------------------------------------------------
# Scenario payload
# --------------------------------------------------------------------------

def dims_of(scen: dict) -> dict:
    m = scen.get("meta") or {}
    bank = m.get("bank") or "v3"
    ind_raw = m.get("industry") or "Unlabelled"
    industry = ind_raw if ind_raw in V3_INDUSTRIES else INDUSTRY_V3.get(ind_raw, OTHER_INDUSTRY)
    mode = (scen.get("output_set") or {}).get("mode")
    # use case: the run plan's `kind` puts both banks on the current bank's nine
    # use cases; for the earlier bank that is a mapping, so its own label is kept
    usecase = m.get("kind") or m.get("use_case") or "Unlabelled"
    return {
        "industry": industry,
        "usecase": usecase,
        "task": TASKS.get(scen.get("task"), scen.get("task")),
        "difficulty": m.get("difficulty") or "Unrated",
        "output": OUTPUTS.get(mode, OUTPUTS[None]),
        "theme": list(m.get("theme") or []),
        "customer": list(m.get("customers") or []),
        # internal only (dropped from the client file)
        "lever": [str(x) for x in (m.get("levers") or [])] or ([NO_LEVERS] if bank == "v2" else []),
        "edge": m.get("edge") or "not stated",
        "bank": bank,
        "_bank": bank, "_industry_raw": ind_raw,
        "_usecase_raw": m.get("use_case") if m.get("use_case") and m.get("use_case") != usecase else None,
    }


INTERNAL_DIMS = ("lever", "edge", "bank", "caveat")

# The site's rule (lib/genmedia.ts withoutRequestId): the vendor's request id is
# never shown. OpenAI writes it inside a sentence ("… include the request ID
# req_…."), so that sentence goes with it.
_REQ_SENTENCE = re.compile(r"\s*[A-Z][^.'\"]*?(?:\.[a-z][^.'\"]*?)*\brequest[ _-]?id\b\s*[:=]?\s*[A-Za-z0-9_-]+\.?", re.I)
_REQ_ID = re.compile(r"\s*\brequest[ _-]?id\s*[:=]\s*[A-Za-z0-9_-]+", re.I)


def without_request_id(text: str) -> str:
    return _REQ_ID.sub("", _REQ_SENTENCE.sub("", text or "")).strip()


def parse_reason(raw: str | None) -> dict | None:
    """A recorded failure, read the way the site reads one (lib/genmedia.ts
    parseRunError): what kind, the provider's own sentence, the codes, and the
    attempts the runner made. Nothing is reworded; the text as recorded (request
    id removed) travels with it."""
    if not raw:
        return None
    text = without_request_id(raw)
    out: dict = {"raw": text}
    rest = text
    mm = re.match(r"member (\d+) \(([^)]*)\):\s*", rest)
    if mm:
        out["member"] = f"{mm.group(1)} ({mm.group(2)})" if not mm.group(2).startswith(mm.group(1)) else mm.group(2)
        rest = rest[mm.end():]
    hm = re.match(r"(refused|rate_limited|failed|timeout|ProviderError|RateLimited|Timeout)\b(?: after (\d+) attempt\(s\))?:?\s*", rest)
    head = hm.group(1) if hm else ""
    if hm and hm.group(2):
        out["attempts"] = int(hm.group(2))
    detail = rest[hm.end():] if hm else rest
    http = re.search(r"\b([45]\d\d)\b", detail)
    status = (re.search(r"'code':\s*'([A-Za-z_]+)'", detail) or re.search(r"'status':\s*'([A-Z_]+)'", detail)
              or re.search(r"\b[45]\d\d ([A-Z][A-Z_]{3,})\b", detail))
    msg = re.search(r"'message':\s*(['\"])(.*?)\1\s*[,}]", detail)
    codes = [x for x in (http.group(1) if http else None, status.group(1) if status else None) if x]
    if head == "refused":
        kind = "Refused"
    elif head in ("rate_limited", "RateLimited") or "429" in codes or "RESOURCE_EXHAUSTED" in codes:
        kind = "Rate limited"
    elif head == "Timeout" or "504" in codes or "408" in codes or "DEADLINE_EXCEEDED" in codes:
        kind = "Timed out"
    else:
        kind = "Provider error"
    out["kind"] = kind
    out["msg"] = (msg.group(2).strip() if msg else (detail.strip() if not http else "")) or None
    out["codes"] = codes
    # "prompt blocked: BlockedReason.OTHER" is the SDK's enum repr; show the code and
    # Google's definition of it instead, and say that nothing else came back
    gb = re.match(r"(prompt|judge prompt) blocked:\s*(?:BlockedReason\.)?([A-Z_]+)\s*$", detail.strip())
    if gb and gb.group(2) in GOOGLE_BLOCK_REASONS:
        code = gb.group(2)
        out["msg"] = (f"Blocked by Google before generation. Reason code {code}: "
                      f"\u201c{GOOGLE_BLOCK_REASONS[code]}\u201d No other message is returned for a blocked prompt.")
        out["codes"] = [code]
        out["kind"] = "Refused"
    return out


def call_kind(row: dict) -> str:
    st = row.get("status")
    if st == "rate_limited":
        return "Rate limited"
    if st == "refused":
        return "Refused"
    p = parse_reason(row.get("error"))
    return p["kind"] if p else (st or "failed")


def arm_payload(p: Pick | None, scen: dict, mid: str, imgs: Images, thumb_px: int, frame_px: int,
                internal: bool) -> dict:
    if p is None:
        return {"st": "missing"}
    run, srow = p.run, p.srow
    sid = scen["id"]
    out: dict = {"st": (srow or {}).get("status") or p.cell.get("state")}
    judged = p.numeric and srow["status"] == "scored"   # an invalid cell earned 0 unjudged
    if p.numeric:
        out["score"] = round(float(srow["score"]), 4)
        out["pct"] = round(float(srow["score"]) * 10, 1)
        out["pass"] = srow.get("pass")
        if judged:
            out["cy"], out["cn"] = srow.get("checks_yes"), srow.get("checks_n")
    if not judged:
        # the final record of the cell: manifest reason, else the score row's
        err = parse_reason(p.cell.get("reason") or (srow or {}).get("reason"))
        if err:
            if not internal:
                err.pop("raw", None)
            out["err"] = err
    if internal and p.numeric:
        earlier = collections.Counter(call_kind(r) for r in run.failed_calls.get((sid, mid), []))
        if earlier:
            out["earlier"] = sorted(earlier.items(), key=lambda kv: -kv[1])

    mode = (scen.get("output_set") or {}).get("mode")
    members = (scen.get("output_set") or {}).get("members") or []
    dims: dict = {}                      # delivered pixel size, for the canvas check
    if mode == "each":
        frames = find_frames(run, sid, mid)
        out["frames"] = []
        for k in sorted(frames):
            f = imgs.add(frames[k], frame_px, "frame")
            if f:
                f["name"] = members[k - 1]["name"] if k - 1 < len(members) else f"Frame {k}"
                f["k"] = k
                out["frames"].append(f)
                dims[k] = (f["w"], f["h"])
        sheet_href = imgs.link(find_output(run, sid, mid))
        if sheet_href:     # the full contact sheet is linked, not embedded: its frames are
            out["sheetHref"] = sheet_href
    else:
        o = imgs.add(find_output(run, sid, mid), thumb_px, "output")
        if o:
            out["main"] = o
            dims[0] = (o["w"], o["h"])
    out["_dims"] = dims

    calls = p.run.calls.get((sid, mid), [])
    out["calls"] = [[int(c.get("latency_ms") or 0), int((c.get("cost") or {}).get("micro_usd") or 0)]
                    for c in calls]
    if any((c.get("cost") or {}).get("usage_source") == "estimated" for c in calls):
        out["costEst"] = True
    applied = [c.get("applied_params") or {} for c in calls]
    out["_sizes"] = sorted(m.group(0) for m in (re.match(r"\d+x\d+", str(a.get("size") or "")) for a in applied) if m)
    out["_quality"] = sorted({str(a["quality"]) for a in applied if a.get("quality") is not None})

    crit = []
    weights = (srow or {}).get("weights") or {}
    for name, c in (((srow or {}).get("criteria") or {}) if judged else {}).items():
        crit.append({"n": crit_name(name), "pct": round(float(c.get("score", 0)) * 10, 1),
                     "w": weights.get(name), "src": c.get("source") or "judge",
                     "why": c.get("reasoning") or ""})
    if crit:
        out["crit"] = crit
    j = run.judge.get((sid, mid)) if judged else None
    if j:
        if j.get("overall_note"):
            out["note"] = j["overall_note"]
        n = len(scen.get("checklist") or [])
        ans, ev = [None] * n, [""] * n
        for c in j.get("checks") or []:
            i = c.get("i")
            if isinstance(i, int) and 0 <= i < n:
                ans[i] = 1 if c.get("answer") else 0
                ev[i] = c.get("evidence") or ""
        if n:
            out["ans"], out["ev"] = ans, ev
    ch = run.checks.get((sid, mid))
    if ch:
        failed = [f"{g.get('gate')}: {g.get('detail')}" for g in ch.get("gates") or [] if not g.get("passed")]
        out["gates"] = {"ok": bool(ch.get("passed")) and not failed, "fail": failed}
    return out


def _scope(scope: str | None, mode: str | None) -> str | None:
    if not scope or scope == "image":
        return None
    if mode == "panels":
        return scope.replace("frame ", "panel ").replace("whole set", "whole sheet")
    return scope


def canvas_check(a: dict, b: dict) -> dict | None:
    """Same canvas for both models: delivered width x height per image (frame by
    frame for sets) and the size each provider reports it applied. None when the
    scenario has no output from one side (nothing to compare)."""
    da, db = a.pop("_dims", {}), b.pop("_dims", {})
    sa, sb = a.pop("_sizes", []), b.pop("_sizes", [])
    qa, qb = a.pop("_quality", []), b.pop("_quality", [])
    common = sorted(set(da) & set(db))
    if not common:
        return None
    problems = []
    for k in common:
        if da[k] != db[k]:
            where = "image" if k == 0 else f"frame {k}"
            problems.append(f"{where}: {da[k][0]}×{da[k][1]} vs {db[k][0]}×{db[k][1]}")
    if sa and sb and sorted(set(sa)) != sorted(set(sb)):
        problems.append(f"requested size {', '.join(sorted(set(sa)))} vs {', '.join(sorted(set(sb)))}")
    size = da[common[0]]
    return {"ok": not problems, "size": f"{size[0]}×{size[1]}", "why": "; ".join(problems),
            "qa": qa, "qb": qb}


def scenario_payload(sid: str, scen: dict, picks: dict, arms: dict, caveats: dict,
                     imgs: Images, thumb_px: int, frame_px: int, internal: bool) -> dict:
    d = dims_of(scen)
    cv = caveats.get(sid)
    d["caveat"] = CAVEAT_LEVELS[cv["level"]] if cv else "none"
    pa, pb = picks.get((sid, arms["a"])), picks.get((sid, arms["b"]))
    s: dict = {
        "id": sid, "title": scen.get("title") or sid, "task": scen.get("task"),
        "v": {k: v for k, v in d.items() if not k.startswith("_") and (internal or k not in INTERNAL_DIMS)},
        "bankKey": d["_bank"],
        "brief": scen.get("prompt") or "",
        "expected": scen.get("expected") or "",
        "required": scen.get("required_text"),
        "passRule": scen.get("pass_rule") if (scen.get("gate_criteria") or scen.get("pass_min_score") is not None) else None,
        "checklist": [{"q": c.get("q"), "dim": crit_name(c["dim"]) if c.get("dim") else None,
                       "scope": _scope(c.get("scope"), (scen.get("output_set") or {}).get("mode"))}
                      for c in scen.get("checklist") or []],
        "_industryRaw": d["_industry_raw"],
    }
    if d["_industry_raw"] != d["industry"]:
        s["industryBank"] = d["_industry_raw"]
    if d["_usecase_raw"]:
        s["usecaseBank"] = d["_usecase_raw"]
    os_ = scen.get("output_set")
    if os_:
        s["set"] = {"mode": os_.get("mode"), "keep": os_.get("keep") or [],
                    "members": [{"name": m.get("name"), "brief": m.get("brief")} for m in os_.get("members") or []]}
    if cv:
        # the reasons are the input review's findings: internal record only;
        # the client sees the flag and the level, nothing written about it
        s["caveat"] = {"level": CAVEAT_LEVELS[cv["level"]]}
        if internal:
            s["caveat"]["why"] = cv["why"]
            if cv.get("still"):
                s["caveat"]["still"] = cv["still"]
    # sources: the run that the scenario's cells come from
    src_run = (pa or pb).run if (pa or pb) else None
    s["sources"] = []
    if src_run:
        for inp in (src_run.manifest.get("inputs") or {}).get(sid, []):
            f = imgs.add(src_run.dir / inp["path"], thumb_px, "input")
            if f:
                f["role"] = inp.get("role") or "source"
                s["sources"].append(f)
    s["a"] = arm_payload(pa, scen, arms["a"], imgs, thumb_px, frame_px, internal)
    s["b"] = arm_payload(pb, scen, arms["b"], imgs, thumb_px, frame_px, internal)
    s["_canvas"] = canvas_check(s["a"], s["b"])
    if "score" in s["a"] and "score" in s["b"]:
        diff = round(s["a"]["score"] - s["b"]["score"], SCORE_DECIMALS)
        s["win"] = "tie" if diff == 0 else ("a" if diff > 0 else "b")
        s["margin"] = round(abs(s["a"]["score"] - s["b"]["score"]) * 10, 1)
    else:
        s["win"] = None
    return s


# --------------------------------------------------------------------------
# Dimensions (the categorisations the win map and filters offer)
# --------------------------------------------------------------------------

def dims_meta(scens: list[dict], internal: bool) -> list[dict]:
    def present(key):
        vals = set()
        for s in scens:
            v = s["v"][key]
            vals.update(v if isinstance(v, list) else [v])
        return vals

    def ordered(key, order):
        vals = present(key)
        return [v for v in order if v in vals] + sorted(vals - set(order))

    # difficulty words differ by bank; a word both banks use gets its bank named
    by_word: dict = {}
    for s in scens:
        by_word.setdefault(s["v"]["difficulty"], set()).add(s["bankKey"])
    for s in scens:
        w = s["v"]["difficulty"]
        if len(by_word.get(w, ())) > 1:
            s["v"]["difficulty"] = f"{w} ({s['bankKey']} scale)"
    diff_vals = {}
    for s in scens:
        raw = s["v"]["difficulty"].split(" (")[0]
        diff_vals[s["v"]["difficulty"]] = DIFF_RANK.get((s["bankKey"], raw), 9)
    diff_order = sorted(diff_vals, key=lambda v: (diff_vals[v], v))

    # `map: False` = an evidence filter only, not a Win Map grouping
    dims = [
        {"key": "industry", "label": "Industry", "multi": False,
         "order": ordered("industry", V3_INDUSTRIES + [OTHER_INDUSTRY])},
        {"key": "usecase", "label": "Use case", "multi": False,
         "order": ordered("usecase", USE_CASES)},
        {"key": "task", "label": "Task type", "multi": False,
         "order": ordered("task", list(TASKS.values()))},
        {"key": "difficulty", "label": "Difficulty", "multi": False,
         "order": diff_order},
        {"key": "output", "label": "Output", "multi": False, "map": False,
         "order": ordered("output", list(OUTPUTS.values()))},
        {"key": "theme", "label": "Theme", "multi": True,
         "order": ordered("theme", THEMES)},
        {"key": "customer", "label": "Target customer", "multi": True,
         "order": ordered("customer", [])},
    ]
    if internal:
        dims += [
            {"key": "lever", "label": "Levers (v3 tags)", "multi": True,
             "order": ordered("lever", LEVER_ORDER + [NO_LEVERS])},
            {"key": "edge", "label": "Run-plan edge", "multi": False, "order": ordered("edge", EDGES)},
            {"key": "bank", "label": "Bank", "multi": False, "order": ordered("bank", ["v3", "v2"])},
            {"key": "caveat", "label": "Input caveat", "multi": False,
             "order": ordered("caveat", ["none", "partial", "not usable"])},
        ]
    return dims


def load_caveats(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    d = json.loads(path.read_text())
    out = {}
    for level, ids in (("no", d.get("not_usable") or []), ("partial", d.get("partial") or [])):
        for sid in ids:
            det = (d.get("detail") or {}).get(sid, [])
            reasons = [x.get("reason") for x in det if x.get("reason")]
            still = [w for x in det for w in (x.get("still_wrong") or [])]
            out[sid] = {"level": level, "why": " ".join(reasons), "still": still}
    return out


def logo_data(path: Path) -> str | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGBA")
            h = 60
            im = im.resize((round(im.width * h / im.height), h), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


# --------------------------------------------------------------------------
# Size projection for the full bank
# --------------------------------------------------------------------------

def projection(imgs: Images, data_bytes: int, n_scen: int) -> dict | None:
    files = sorted(BANK.glob("*.yaml"))
    if not files or not n_scen:
        return None
    n_in = n_out = n_frame = 0
    srcs = set()
    for f in files:
        d = yaml.safe_load(f.read_text())
        for v in (d.get("inputs") or {}).values():
            srcs.add(v)
        mode = (d.get("output_set") or {}).get("mode")
        if mode == "each":
            n_frame += 2 * len(d["output_set"].get("members") or [])
        else:
            n_out += 2
    n_in = len(srcs)
    avg = {k: (sum(v) / len(v)) for k, v in imgs.bytes_by_kind.items() if v}
    fallback = sum(avg.values()) / len(avg) if avg else 20000
    est_img = (n_in * avg.get("input", fallback) + n_out * avg.get("output", fallback)
               + n_frame * avg.get("frame", avg.get("output", fallback))) * 4 / 3
    est_data = data_bytes / n_scen * len(files)
    return {"scenarios": len(files), "inputs": n_in, "outputs": n_out, "frames": n_frame,
            "avg_kb": {k: round(v / 1024, 1) for k, v in avg.items()},
            "est_mb": round((est_img + est_data + 120_000) / 1e6, 1)}


# --------------------------------------------------------------------------
# Run details (internal audience): tables read from the run files, no prose
# --------------------------------------------------------------------------

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LATENCY_NOTE = ("latency_ms times the provider call only; the rpm pacer wait and the retry backoff "
                "are outside it (runner/generate.py). telemetry ts is written when a call returns; "
                "judge ts when a call starts.")


def load_models_config(path) -> dict:
    if not path:
        return {}
    p = Path(path)
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def _ts(s) -> dt.datetime:
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _utc(s) -> str:
    return _ts(s).astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ist(s) -> str:
    return _ts(s).astimezone(IST).strftime("%d %b %H:%M:%S")


def _sec(a, b) -> float:
    return (_ts(b) - _ts(a)).total_seconds()


def _dur(sec) -> str:
    if sec is None:
        return "—"
    if sec < 120:
        return f"{sec:.1f} s"
    m = sec / 60
    return f"{m:.1f} min" if m < 120 else f"{m:.1f} min ({int(m // 60)} h {int(round(m % 60))} min)"


def _usd(micro, nd: int = 4) -> str:
    return "—" if micro is None else f"${micro / 1e6:,.{nd}f}"


def _s1(x) -> str:
    return "—" if x is None else f"{x:.1f} s"


def _pnr(v, q):
    x = sorted(v)
    return x[max(0, math.ceil(q * len(x)) - 1)]


def _lat_cells(secs: list) -> list:
    if not secs:
        return ["0", "—", "—", "—", "—", "—"]
    return [str(len(secs)), _s1(statistics.median(secs)), _s1(_pnr(secs, .9)), _s1(statistics.mean(secs)),
            _s1(min(secs)), _s1(max(secs))]


def _cost(r: dict) -> int:
    return int(((r.get("cost") or {}).get("micro_usd")) or 0)


def _tbl(title, cols, rows, src=None, head=None) -> dict:
    t = {"title": title, "cols": cols, "rows": rows}
    if src:
        t["src"] = src
    if head:
        t["head"] = head
    return t


def _passes(man: dict):
    """Events split into passes: a 'planned' event after the first starts a new one
    (a resume of the same run folder)."""
    ev = man.get("events") or []
    starts = [_ts(e["ts"]) for e in ev if e.get("state") == "planned"][1:]
    return ev, (lambda ts: 1 + sum(1 for b in starts if _ts(ts) >= b))


def _member(r: dict) -> str:
    if "member" not in r:
        return "—"
    name = str(r.get("member_name") or "")
    return name if name.startswith(str(r["member"])) else f"{r['member']} ({name})"


def _err_short(text: str | None) -> str:
    p = parse_reason(text)
    if not p:
        return "—"
    return " · ".join(x for x in [" ".join(p["codes"]) or None, p.get("msg")] if x) or p["raw"]


def _limits(cfg: dict, mid: str) -> str | None:
    for m in (cfg or {}).get(MEDIA_KEY) or []:
        if m.get("id") == mid and m.get("limits"):
            return ", ".join(f"{k} {v}" for k, v in m["limits"].items())
    return None


def _provider(name: str | None) -> str:
    n = (name or "").lower()
    return "Google" if n.startswith("google") or n.startswith("gemini") else ("OpenAI" if n.startswith("openai") or n.startswith("gpt") else (name or "—"))


def run_details(runs: list, arms: dict, scens: list, caveats: dict, args, cfg: dict, names: dict) -> dict:
    A, B = arms["a"], arms["b"]
    mids = (A, B)
    short = {A: names["a"]["short"], B: names["b"]["short"]}
    groups: list = []
    cost_rows: list = []
    by_provider: collections.Counter = collections.Counter()
    grand = 0
    for run in runs:
        tag = f" · {run.id}" if len(runs) > 1 else ""
        man = run.manifest
        ev, which = _passes(man)
        T, J, S, C = run.tel, run.judge_rows, run.score_rows, run.check_rows
        npass = max([which(e["ts"]) for e in ev] or [1])
        cellmap = {(c["scenario_id"], c["model_id"]): c for c in (man.get("cells") or {}).values()}
        compared = {sid for (sid, mid) in cellmap
                    if all((cellmap.get((sid, m)) or {}).get("state") == "scored" for m in mids)}
        ok = {m: [r for r in T if r.get("model_id") == m and r.get("status") == "ok"] for m in mids}

        # ---- run
        jr = next((r.get("judge") for r in J if r.get("judge")), {}) or {}
        jcfg = ((cfg or {}).get("judge") or {}).get(MEDIA_KEY) or {}
        rows = [["Run id", run.id], ["Git sha", man.get("git_sha") or "—"],
                ["Created (UTC)", _utc(man["created"]) if man.get("created") else "—"]]
        for m in man.get("models") or []:
            if m["id"] in mids:
                lim = _limits(cfg, m["id"])
                rows.append([short[m["id"]], " · ".join(x for x in [m.get("provider_model"), m.get("provider"),
                             f"price as of {m.get('price_as_of')}", f"limits: {lim}" if lim else None] if x)])
        rows.append(["Judge", " · ".join(x for x in [
            jr.get("provider_model"), jcfg.get("provider"),
            f"location {jcfg['vertex']['location']}" if (jcfg.get("vertex") or {}).get("location") else None,
            f"temperature {jr.get('temperature')}" if jr.get("temperature") is not None else None,
            f"thinking_level {jr.get('thinking_level')}" if jr.get("thinking_level") else None] if x)])
        rows.append(["Budget", f"${man.get('budget_usd')}" + "".join(
            f" · {k} ${v}" for k, v in (man.get("budget_by_provider") or {}).items())])
        for task, h in (man.get("rubric_hashes") or {}).items():
            rows.append([f"Rubric sha256 · {task}", h])
        rows.append(["Scenario set sha256", man.get("scenario_set_hash") or "—"])
        cst = collections.Counter(c.get("state") for c in cellmap.values())
        sst = collections.Counter((man.get("scenario_status") or {}).values())
        rows.append(["Cells", " · ".join(f"{k} {v}" for k, v in cst.most_common())])
        rows.append(["Scenarios", " · ".join(f"{k} {v}" for k, v in sst.most_common()) + f" · compared {len(compared)}"])
        groups.append({"title": "Run" + tag, "tables": [
            _tbl("Run", ["Field", "Value"], rows,
                 "manifest.json · judge.jsonl" + (f" · {args.models_config}" if cfg else ""))]})

        # ---- timeline
        trows, prev = [], None
        for e in ev:
            trows.append([str(which(e["ts"])), e["state"], _utc(e["ts"]), _ist(e["ts"]),
                          _dur(_sec(prev, e["ts"])) if prev else "—"])
            prev = e["ts"]
        prows = []
        for k in range(1, npass + 1):
            pe = [e for e in ev if which(e["ts"]) == k]
            if pe:
                prows.append([str(k), f"{pe[0]['state']} → {pe[-1]['state']}", _utc(pe[0]["ts"]), _utc(pe[-1]["ts"]),
                              _dur(_sec(pe[0]["ts"], pe[-1]["ts"]))])
        if ev:
            prows.append(["all", f"{ev[0]['state']} → {ev[-1]['state']}", _utc(ev[0]["ts"]), _utc(ev[-1]["ts"]),
                          _dur(_sec(ev[0]["ts"], ev[-1]["ts"]))])
        srows = []
        for fname, rr0 in (("telemetry.jsonl", T), ("checks.jsonl", C), ("judge.jsonl", J), ("scores.jsonl", S)):
            for k in range(1, npass + 1):
                rr = sorted((r for r in rr0 if which(r["ts"]) == k), key=lambda r: _ts(r["ts"]))
                if rr:
                    srows.append([fname, str(k), str(len(rr)), _utc(rr[0]["ts"]), _utc(rr[-1]["ts"]),
                                  _dur(_sec(rr[0]["ts"], rr[-1]["ts"]))])
        groups.append({"title": "Timeline" + tag, "tables": [
            _tbl("Events", ["Pass", "State", "UTC", "IST", "Since previous event|r"], trows, "manifest.json events"),
            _tbl("Passes", ["Pass", "Events", "From (UTC)", "To (UTC)", "Duration|r"], prows, "manifest.json events"),
            _tbl("Record spans", ["File", "Pass", "Rows|r", "First (UTC)", "Last (UTC)", "Span|r"], srows,
                 "telemetry.jsonl · checks.jsonl · judge.jsonl · scores.jsonl ts"),
        ]})

        # ---- latency
        def secs_of(rr):
            return [r["latency_ms"] / 1000 for r in rr if r.get("latency_ms") is not None]
        subsets = [("All successful calls", lambda r: True),
                   ("Single images", lambda r: "member" not in r),
                   ("Images in a set", lambda r: "member" in r),
                   (TASKS["text_to_image"], lambda r: r.get("task") == "text_to_image"),
                   (TASKS["image_edit"], lambda r: r.get("task") == "image_edit"),
                   (f"Compared scenarios ({len(compared)})", lambda r: r["scenario_id"] in compared)]
        lrows = []
        for label, f in subsets:
            lrows.append([label] + _lat_cells(secs_of([r for r in ok[A] if f(r)])) + _lat_cells(secs_of([r for r in ok[B] if f(r)])))
        for st_, label in (("rate_limited", "Rate-limited calls"), ("refused", "Refused calls")):
            lrows.append([label] + sum((_lat_cells(secs_of([r for r in T if r.get("model_id") == m and r.get("status") == st_]))
                                        for m in mids), []))
        grp = collections.defaultdict(list)
        for r in T:
            if r.get("model_id") in mids:
                grp[(r["model_id"], r["scenario_id"], r.get("member"), which(r["ts"]))].append(r)
        e2e = {m: [] for m in mids}
        wait = {m: [] for m in mids}
        multi, images = collections.Counter(), collections.Counter()
        for (m, *_), rr in grp.items():
            rr.sort(key=lambda r: r.get("attempt") or 0)
            images[m] += 1
            if len(rr) > 1:
                multi[m] += 1
            start = _ts(rr[0]["ts"]) - dt.timedelta(milliseconds=rr[0].get("latency_ms") or 0)
            okr = [r for r in rr if r.get("status") == "ok"]
            if okr:
                e2e[m].append((_ts(okr[0]["ts"]) - start).total_seconds())
            if len(rr) > 1 and rr[0].get("status") == "rate_limited" and rr[1].get("attempt") == 2:
                s2 = _ts(rr[1]["ts"]) - dt.timedelta(milliseconds=rr[1].get("latency_ms") or 0)
                wait[m].append((s2 - _ts(rr[0]["ts"])).total_seconds())
        lrows.append(["First attempt start → image, waits and retries included"] + _lat_cells(e2e[A]) + _lat_cells(e2e[B]))
        lrows.append(["Rate-limited attempt 1 → start of attempt 2"] + _lat_cells(wait[A]) + _lat_cells(wait[B]))
        lat_cols = ["Calls"] + ["n|r", "p50|r", "p90|r", "mean|r", "min|r", "max|r"] * 2
        groups.append({"title": "Latency" + tag, "tables": [
            _tbl("Generation latency (seconds)", lat_cols, lrows, "telemetry.jsonl latency_ms, ts · " + LATENCY_NOTE,
                 head=[["", 1], [short[A], 6], [short[B], 6]])]})

        # ---- throughput (pass 1)
        def ev_ts(state, k=1):
            return next((e["ts"] for e in ev if e["state"] == state and which(e["ts"]) == k), None)
        thr = []
        for m in mids:
            rr = sorted((r for r in ok[m] if which(r["ts"]) == 1), key=lambda r: _ts(r["ts"]))
            if not rr:
                continue
            span = _sec(rr[0]["ts"], rr[-1]["ts"])
            thr.append([short[m] + " · first to last row", str(len(rr)), _utc(rr[0]["ts"]), _utc(rr[-1]["ts"]), _dur(span),
                        f"{len(rr) / (span / 60):.2f}" if span else "—", _dur(sum(r["latency_ms"] for r in rr) / 1000)])
        g0, g1, g2 = ev_ts("running"), ev_ts("generated"), ev_ts("judged")
        if g0 and g1:
            both = [r for m in mids for r in ok[m] if which(r["ts"]) == 1]
            span = _sec(g0, g1)
            thr.append(["Both models · running → generated", str(len(both)), _utc(g0), _utc(g1), _dur(span),
                        f"{len(both) / (span / 60):.2f}", _dur(sum(r["latency_ms"] for r in both) / 1000)])
        if g1 and g2:
            j1 = [r for r in J if which(r["ts"]) == 1]
            span = _sec(g1, g2)
            thr.append(["Judge calls · generated → judged", str(len(j1)), _utc(g1), _utc(g2), _dur(span),
                        f"{len(j1) / (span / 60):.2f}", "—"])
        groups.append({"title": "Throughput, pass 1" + tag, "tables": [
            _tbl("Throughput", ["", "Images or calls|r", "From (UTC)", "To (UTC)", "Span|r", "Per minute|r", "Summed call time|r"],
                 thr, "telemetry.jsonl · judge.jsonl · manifest.json events")]})

        # ---- attempts and rate limits
        arows = []
        for m in mids:
            rr = [r for r in T if r.get("model_id") == m]
            stc = collections.Counter(r.get("status") for r in rr)
            att = collections.Counter(r.get("attempt") for r in rr if r.get("status") == "ok")
            other = len(rr) - stc.get("ok", 0) - stc.get("rate_limited", 0) - stc.get("refused", 0)
            arows.append([short[m], str(len(rr)), str(stc.get("ok", 0)), str(stc.get("rate_limited", 0)),
                          str(stc.get("refused", 0)), str(other)] + [str(att.get(k, 0)) for k in (1, 2, 3)] +
                         [f"{multi[m]} of {images[m]}", _limits(cfg, m) or "—"])
        erows = []
        errs = collections.Counter()
        for r in T:
            if r.get("status") != "ok" and r.get("model_id") in mids:
                errs[(short[r["model_id"]], r["status"], _err_short(r.get("error")), which(r["ts"]))] += 1
        for (mname, stt, e, k), n in sorted(errs.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][3])):
            erows.append([mname, stt, e, str(k), str(n)])
        groups.append({"title": "Attempts and rate limits" + tag, "tables": [
            _tbl("Generation calls", ["Model", "Calls|r", "ok|r", "rate_limited|r", "refused|r", "other|r",
                                      "ok on attempt 1|r", "2|r", "3|r", "Images with more than one attempt|r", "Limits"],
                 arows, "telemetry.jsonl status, attempt" + (f" · limits: {args.models_config}" if cfg else "")),
            _tbl("Errors on calls that were not ok", ["Model", "Status", "Error (codes · message)", "Pass", "Rows|r"],
                 erows, "telemetry.jsonl error, request id removed"),
        ]})

        # ---- refusals, failures, retries
        rrows = [[str(which(r["ts"])), _utc(r["ts"]), r["scenario_id"], short.get(r["model_id"], r["model_id"]),
                  _member(r), str(r.get("attempt")), _s1((r.get("latency_ms") or 0) / 1000),
                  without_request_id(r.get("error") or "")]
                 for r in T if r.get("status") == "refused"]
        p1s: dict = {}
        for r in S:
            if which(r["ts"]) == 1:
                p1s[(r["scenario_id"], r["model_id"])] = r
        later_judge: dict = {}
        for r in J:
            if which(r["ts"]) >= 2:
                later_judge[(r["scenario_id"], r["model_id"])] = r
        crows = []
        keys = sorted({k for k, r in p1s.items() if r.get("status") != "scored"} |
                      {k for k, c in cellmap.items() if c.get("state") != "scored"})
        for k in keys:
            r1 = p1s.get(k) or {}
            fin = cellmap.get(k) or {}
            lj = later_judge.get(k)
            crows.append([k[0], short.get(k[1], k[1]), r1.get("status") or "—", _err_short(r1.get("reason")) if r1.get("reason") else "—",
                          (f"{lj['status']} {_utc(lj['ts'])}" if lj else "—"), fin.get("state") or "—",
                          without_request_id(fin.get("reason") or "") if fin.get("state") != "scored" else "—"])
        g2rows = [[_utc(r["ts"]), r["scenario_id"], short.get(r["model_id"], r["model_id"]), _member(r), str(r.get("attempt")),
                   r.get("status"), _s1((r.get("latency_ms") or 0) / 1000), _usd(_cost(r)) if _cost(r) else "—"]
                  for r in T if which(r["ts"]) >= 2]
        groups.append({"title": "Refusals, failures and retries" + tag, "tables": [
            _tbl("Refused calls", ["Pass", "UTC", "Scenario", "Model", "Image", "Attempt|r", "Time|r", "Error as recorded"],
                 rrows, "telemetry.jsonl status refused, request id removed"),
            _tbl("Cells not scored after pass 1", ["Scenario", "Model", "Pass 1", "Pass 1 reason (codes · message)",
                                                  "Judge call in a later pass", "Final state", "Final reason as recorded"],
                 crows, "scores.jsonl · judge.jsonl · manifest.json cells"),
            _tbl("Generation calls after pass 1", ["UTC", "Scenario", "Model", "Image", "Attempt|r", "Status", "Time|r", "Cost|r"],
                 g2rows, "telemetry.jsonl"),
        ]})

        # ---- judge
        jrows = []
        for k in range(1, npass + 1):
            rr = [r for r in J if which(r["ts"]) == k]
            if not rr:
                continue
            u = collections.Counter()
            for r in rr:
                for kk, v in (r.get("usage") or {}).items():
                    if isinstance(v, (int, float)):
                        u[kk] += v
            stc = collections.Counter(r.get("status") for r in rr)
            jrows.append([str(k), str(len(rr)), str(stc.get("judged", 0)), str(len(rr) - stc.get("judged", 0)),
                          f"{u.get('prompt_token_count', 0):,}", f"{u.get('candidates_token_count', 0):,}",
                          f"{u.get('thoughts_token_count', 0):,}", _usd(sum(_cost(r) for r in rr))])
        mc = collections.Counter(r.get("media_count") for r in J if r.get("status") == "judged")
        jrows.append(["Images per judged call", " · ".join(f"{k}: {v}" for k, v in sorted(mc.items())), "", "", "", "", "", ""])
        groups.append({"title": "Judge" + tag, "tables": [
            _tbl("Judge calls", ["Pass", "Calls|r", "judged|r", "not judged|r", "Prompt tokens|r", "Output tokens|r",
                                 "Thinking tokens|r", "Cost|r"], jrows, "judge.jsonl status, usage, cost, media_count")]})

        # ---- cost
        pcols = [f"Pass {k}|r" for k in range(1, npass + 1)]
        gen_rows, per_rows, tok_rows = [], [], []
        run_total = 0

        def basis_of(rows):
            return "; ".join(sorted({" · ".join(str(x) for x in ((r.get("cost") or {}).get("basis"), (r.get("cost") or {}).get("price_as_of"),
                                                          (r.get("cost") or {}).get("usage_source"))) for r in rows if r.get("cost")}))
        for m in mids:
            rr = [r for r in T if r.get("model_id") == m]
            okm = ok[m]
            costs = [_cost(r) for r in okm]
            pa = [r for r in okm if r["scenario_id"] in compared]
            tot = sum(_cost(r) for r in rr)
            run_total += tot
            by_provider[_provider(rr[0].get("provider") if rr else m)] += tot
            gen_rows.append([f"Generation · {short[m]}"] + [_usd(sum(_cost(r) for r in rr if which(r["ts"]) == k)) for k in range(1, npass + 1)] +
                            [_usd(tot), basis_of(okm)])
            per_rows.append([short[m], str(len(okm)), _usd(sum(_cost(r) for r in okm if "member" not in r)),
                             _usd(sum(_cost(r) for r in okm if "member" in r)),
                             _usd(statistics.median(costs)) if costs else "—", f"{_usd(min(costs))}–{_usd(max(costs))}" if costs else "—",
                             str(len(pa)), _usd(sum(_cost(r) for r in pa) / len(pa)) if pa else "—"])
            u = collections.Counter()
            nz = collections.Counter()
            for r in okm:
                for kk, v in (r.get("usage") or {}).items():
                    if isinstance(v, (int, float)) and kk != "images":
                        u[kk] += v
                        nz[kk] += 1 if v else 0
            for kk in u:
                tok_rows.append([short[m], kk, f"{u[kk]:,}", str(nz[kk])])
        jtot = sum(_cost(r) for r in J)
        run_total += jtot
        by_provider[_provider(jr.get("provider_model"))] += jtot
        gen_rows.append([f"Judge · {jr.get('provider_model') or 'judge'}"] +
                        [_usd(sum(_cost(r) for r in J if which(r["ts"]) == k)) for k in range(1, npass + 1)] + [_usd(jtot), basis_of(J)])
        gen_rows.append(["This run"] + [_usd(sum(_cost(r) for r in list(T) + list(J) if which(r["ts"]) == k))
                                        for k in range(1, npass + 1)] + [_usd(run_total), ""])
        cost_rows.append([f"Run {run.id}", _usd(run_total)])
        grand += run_total
        groups.append({"title": "Cost" + tag, "tables": [
            _tbl("Cost by pass", ["Item"] + pcols + ["Total|r", "Basis · price as of · usage source"], gen_rows,
                 "telemetry.jsonl cost · judge.jsonl cost"),
            _tbl("Generation cost per image (successful calls)", ["Model", "Images|r", "Single images|r", "Images in sets|r",
                                                                 "Per image p50|r", "Per image min–max|r", "Compared scenarios: images|r",
                                                                 "Compared scenarios: per image|r"], per_rows, "telemetry.jsonl cost"),
            _tbl("Generation usage (successful calls)", ["Model", "Usage field", "Total|r", "Rows with a value|r"], tok_rows,
                 "telemetry.jsonl usage"),
        ]})

    # ---- input images
    src_path = Path(args.sources) if args.sources else None
    src = jsonl(src_path) if src_path and src_path.exists() else []
    if src:
        gen = [r for r in src if "mirror_of" not in r]
        mirrors = [r for r in src if "mirror_of" in r]
        lat = [r["latency_s"] for r in gen if r.get("latency_s") is not None]
        icost = sum(_cost(r) for r in src)
        cb = sorted({" · ".join(str(x) for x in ((r.get("cost") or {}).get("basis"), (r.get("cost") or {}).get("price_as_of"),
                                                (r.get("cost") or {}).get("usage_source"))) for r in gen})
        att = collections.Counter(r.get("attempt") for r in gen)
        th = sum(((r.get("usage") or {}).get("thoughts_token_count") or 0) for r in gen)
        kv = [["Rows", str(len(src))],
              ["Generated", " · ".join(f"{k} {v}" for k, v in collections.Counter(r.get("model") for r in gen).most_common())],
              ["Mirrors (no generation)", str(len(mirrors))],
              ["Replacements (fixed_from)", str(sum(1 for r in src if r.get("fixed_from")))],
              ["Cost", _usd(icost, 3)], ["Cost basis · price as of · usage source", "; ".join(cb)],
              ["Time per image (latency_s)", f"p50 {_s1(statistics.median(lat))} · p90 {_s1(_pnr(lat, .9))} · mean {_s1(statistics.mean(lat))} · "
                                             f"min {_s1(min(lat))} · max {_s1(max(lat))} · summed {_dur(sum(lat))}" if lat else "—"],
              ["Attempt at success", " · ".join(f"{k}: {v}" for k, v in sorted(att.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)))],
              ["Thinking tokens", f"{th:,}"]]
        # sessions: a gap of more than 10 minutes between rows starts a new one
        rows_ts = sorted(src, key=lambda r: _ts(r["ts"]))
        sess, cur = [], [rows_ts[0]]
        for r in rows_ts[1:]:
            if _sec(cur[-1]["ts"], r["ts"]) > 600:
                sess.append(cur)
                cur = [r]
            else:
                cur.append(r)
        sess.append(cur)
        srows = []
        for ss in sess:
            g = [r for r in ss if "mirror_of" not in r]
            srows.append([_utc(ss[0]["ts"]), _utc(ss[-1]["ts"]), _dur(_sec(ss[0]["ts"], ss[-1]["ts"])), str(len(g)),
                          str(sum(1 for r in ss if r.get("fixed_from"))), str(sum(1 for r in ss if "mirror_of" in r)),
                          _usd(sum(_cost(r) for r in ss), 3)])
        # used vs not used (the bank's scenario -> input file map)
        bi_path = Path(args.bank_inputs) if args.bank_inputs else None
        used_by: dict = collections.defaultdict(list)
        if bi_path and bi_path.exists():
            for sid, lst in json.loads(bi_path.read_text()).items():
                for x in lst or []:
                    used_by[Path(x.get("file") or "").stem].append(sid)
        urows = []
        if used_by:
            fixed_by = {r["fixed_from"]: r["key"] for r in src if r.get("fixed_from")}
            mirror_by = {r["mirror_of"]: r["key"] for r in src if r.get("mirror_of")}
            for r in gen:
                if r["key"] in used_by:
                    continue
                inst = sorted({k for sid in r.get("scenarios") or [] for k, v in used_by.items() if sid in v})
                urows.append([r["key"], ", ".join(r.get("scenarios") or []), _utc(r["ts"]), _usd(_cost(r), 3),
                              fixed_by.get(r["key"], "—"), mirror_by.get(r["key"], "—"), ", ".join(inst) or "—"])
            used_gen = [r for r in gen if r["key"] in used_by]
            kv.append(["Used by a scenario (inputs.json)", f"{len(used_by)} files for "
                       f"{len({s for v in used_by.values() for s in v})} scenarios · generated images used {len(used_gen)}, "
                       f"cost {_usd(sum(_cost(r) for r in used_gen), 3)}"])
            kv.append(["Generated, used by no scenario", f"{len(urows)}, cost {_usd(sum(_cost(r) for r in gen if r['key'] not in used_by), 3)}"])
        tables = [_tbl("Input-image generation", ["Field", "Value"], kv, str(src_path)),
                  _tbl("Sessions (a gap over 10 min starts a new one)", ["First (UTC)", "Last (UTC)", "Wall time|r", "Generated|r",
                                                                         "Replacements|r", "Mirrors|r", "Cost|r"], srows, str(src_path))]
        if urows:
            tables.append(_tbl("Generated images used by no scenario", ["Key", "Generated for", "UTC", "Cost|r", "Replaced by (fixed_from)",
                                                                        "Mirrored as", "File the scenario uses (inputs.json)"],
                               urows, f"{src_path} · {bi_path}"))
        groups.append({"title": "Input images", "tables": tables})
        by_provider[_provider(next((r.get("model") for r in gen if r.get("model")), "google"))] += icost
        cost_rows.append(["Input images", _usd(icost)])
        grand += icost

    # ---- test runs of the same study
    trows = []
    for t in args.test_run or []:
        tr = Run(t)
        tev = tr.manifest.get("events") or []
        okc = {m: [r for r in tr.tel if r.get("model_id") == m and r.get("status") == "ok"] for m in mids}
        rl = {m: sum(1 for r in tr.tel if r.get("model_id") == m and r.get("status") == "rate_limited") for m in mids}
        gc = {m: sum(_cost(r) for r in tr.tel if r.get("model_id") == m) for m in mids}
        jc = sum(_cost(r) for r in tr.judge_rows)
        tot = sum(gc.values()) + jc
        for m in mids:
            by_provider[_provider((okc[m][0].get("provider") if okc[m] else m))] += gc[m]
        by_provider[_provider(next((r.get("judge", {}).get("provider_model") for r in tr.judge_rows if r.get("judge")), "gemini"))] += jc
        cells = collections.Counter(c.get("state") for c in (tr.manifest.get("cells") or {}).values())
        trows.append([tr.id, ", ".join(sorted({c["scenario_id"] for c in (tr.manifest.get("cells") or {}).values()})),
                      " · ".join(f"{k} {v}" for k, v in cells.items()),
                      f"{_utc(tev[0]['ts'])} → {_utc(tev[-1]['ts'])[11:]} ({_dur(_sec(tev[0]['ts'], tev[-1]['ts']))})" if tev else "—"] +
                     [f"{_usd(gc[m])} ({len(okc[m])} ok, {rl[m]} rate-limited) · p50 {_s1(statistics.median([r['latency_ms'] / 1000 for r in okc[m]])) if okc[m] else '—'}"
                      for m in mids] + [_usd(jc), _usd(tot)])
        cost_rows.append([f"Test run {tr.id}", _usd(tot)])
        grand += tot
    if trows:
        groups.append({"title": "Test runs", "tables": [
            _tbl("Test runs", ["Run", "Scenarios", "Cells", "Events (UTC)", f"{short[A]} generation", f"{short[B]} generation",
                               "Judge|r", "Total|r"], trows, "telemetry.jsonl · judge.jsonl · manifest.json of each run")]})
    cost_rows.append(["All of the above", _usd(grand)])
    for k, v in sorted(by_provider.items()):
        cost_rows.append([f"of which {k}", _usd(v)])
    groups.append({"title": "Cost, all items", "tables": [_tbl("Cost, all items", ["Item", "Cost|r"], cost_rows)]})

    # ---- input caveats
    cav_path = Path(args.caveats) if args.caveats else DEFAULT_CAVEATS
    if cav_path.exists():
        cav = json.loads(cav_path.read_text())
        state = {s["id"]: ("compared" if s.get("win") else "no score") for s in scens}
        crows = []
        for level, key in (("not usable", "not_usable"), ("partial", "partial")):
            for sid in cav.get(key) or []:
                for d in (cav.get("detail") or {}).get(sid, []) or [{}]:
                    crows.append([sid, level, state.get(sid, "not in this report"), d.get("input") or "—", d.get("reason") or "—",
                                  " • ".join(d.get("still_wrong") or []) or "—"])
        frows = [[k, v] for k, v in (cav.get("fixed_after_review") or {}).items()]
        tables = [_tbl("Note (verbatim)", ["note"], [[cav.get("note") or "—"]], str(cav_path)),
                  _tbl("Scenarios with an input caveat", ["Scenario", "Level", "In this report", "Input", "Reason", "Still wrong"],
                       crows, str(cav_path))]
        if frows:
            tables.append(_tbl("Fixed after review", ["Scenario", "Record"], frows, str(cav_path)))
        groups.append({"title": "Input caveats", "tables": tables})

    # ---- categories: how the report groups the two banks
    ind = collections.Counter((s.get("_industryRaw"), s["v"]["industry"], s["bankKey"]) for s in scens)
    irows = [[raw, grp, bank, str(n)] for (raw, grp, bank), n in sorted(ind.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0] or ""))]
    uc = collections.Counter((s.get("usecaseBank"), s["v"]["usecase"]) for s in scens if s.get("usecaseBank"))
    urows = [[raw, grp, str(n)] for (raw, grp), n in sorted(uc.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    dc = collections.Counter((s["bankKey"], s["v"]["difficulty"]) for s in scens)
    drows = [[b, d, str(n)] for (b, d), n in sorted(dc.items())]
    groups.append({"title": "Categories", "tables": [
        _tbl("Industry: bank label → Win Map row (fold decided 2026-10-09)", ["Bank label", "Win Map row", "Bank", "Scenarios|r"], irows,
             "scenario meta.industry · this report's INDUSTRY_V3"),
        _tbl("Use case, v2 scenarios: bank label → run-plan use case", ["Bank label (meta.use_case)", "Run-plan use case (meta.kind)",
                                                                         "Scenarios|r"], urows, "scenario meta"),
        _tbl("Difficulty by bank", ["Bank", "Difficulty", "Scenarios|r"], drows, "scenario meta.difficulty"),
    ]})
    return {"groups": groups}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Voice lane: read from the voice study's own dashboard (oct-voice-200); its
# report-data JSON and per-clip audio are taken as they are. Human review and
# reviewer notes in that file are not carried over.
# --------------------------------------------------------------------------
VOICE_ARMS = {"a": {"name": "Gemini 3.8 Flash TTS", "short": "Gemini 3.8"},
              "b": {"name": "ElevenLabs Eleven v4", "short": "ElevenLabs v4"}}
VOICE_MID = {"a": "gemini-3-8-flash-tts", "b": "elevenlabs-v4"}
VOICE_CRIT = {"text_accuracy": "Text accuracy", "pronunciation": "Pronunciation", "naturalness": "Naturalness",
              "clarity": "Clarity", "audio_quality": "Audio quality", "style_adherence": "Style adherence"}
VOICE_FIXED = {"language": ["English", "Hinglish", "Hindi", "Telugu", "Tenglish"], "difficulty": ["Easy", "Medium", "Hard"],
               "task": ["Plain read", "With a delivery direction", "Two-voice scene"],
               "speaker": ["Female voice", "Male voice", "Several characters"]}


def load_voice(path: Path) -> tuple[dict, dict]:
    text = path.read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', text, re.S)
    if not m:
        raise SystemExit(f"no report-data in {path}")
    D = json.loads(m.group(1))
    audio = {}
    for k, b in re.findall(r'<script type="text/plain" id="au-(\d+)">(.*?)</script>', text, re.S):
        b = b.strip()
        if re.fullmatch(r"[A-Za-z0-9+/=]*", b):      # base64 only; anything else is dropped
            audio[k] = b
    rounds = sorted({r for sc in D["scenarios"] for r in (sc.get("rounds") or {})})

    def vals(sc, key):
        v = sc["v"][key]
        return v if isinstance(v, list) else ([v] if v else [])

    scens = []
    for sc in D["scenarios"]:
        bank = sc.get("bank") or {}
        sp = bank.get("speaker") or ""
        v = {"language": sc.get("language"), "industry": bank.get("industry"), "usecase": bank.get("useCase"),
             "customer": bank.get("customers") or [], "skill": bank.get("skills") or [],
             "difficulty": sc.get("difficulty"),
             "speaker": "Several characters" if re.match(r"(A:|Narrator)", sp) else
                        "Female voice" if sp.startswith("Female") else "Male voice",
             "task": "Two-voice scene" if sc.get("speakers") else
                     "With a delivery direction" if sc.get("task") == "styled_tts" else "Plain read"}
        direction = "\n".join(f"{x.get('label')}: {x.get('style') or 'no direction'}" for x in sc.get("speakers") or []) \
            or (sc.get("style") or "")
        x = {"id": sc["id"], "title": sc.get("title") or sc["id"], "task": sc.get("task"), "v": v,
             "lang": sc.get("language"), "brief": sc.get("script") or "", "langCode": sc.get("langCode") or "",
             "direction": direction, "expectedList": sc.get("expected") or [],
             "mustSay": sc.get("mustSay") or [], "mustSayAny": sc.get("mustSayAny") or [],
             "mustNotSay": sc.get("mustNotSay") or [],
             "maxWer": sc.get("maxWer") if sc.get("asrScored") else None,
             "sources": [], "checklist": []}
        for side in ("a", "b"):
            clips = [(i, ((sc.get("rounds") or {}).get(r) or {}).get(VOICE_MID[side])) for i, r in enumerate(rounds)]
            clips = [(i, c) for i, c in clips if c]
            mean = (sc.get("mean") or {}).get(side)
            arm = {"st": "scored" if any(c.get("status") == "scored" for _, c in clips)
                   else (clips[0][1].get("status") if clips else "missing")}
            if mean is not None:
                arm["score"] = round(float(mean), 4)
                arm["pct"] = round(float(mean) * 10, 1)
            if clips:
                passed = [bool(c.get("passed")) for _, c in clips]
                arm["pass"], arm["gp"], arm["gn"] = all(passed), sum(passed), len(passed)
                ok = [float(c["score"]) for _, c in clips
                      if c.get("passed") and c.get("status") == "scored" and c.get("score") is not None]
                arm["qs"], arm["qn"] = sum(ok), len(ok)
            arm["calls"] = [[int(c.get("latency") or 0), int(c.get("cost") or 0)] for _, c in clips]
            arm["ttfa"] = [c["ttfa"] for _, c in clips if c.get("ttfa") is not None]
            arm["takes"] = [{
                "r": f"Round {i + 1}", "st": c.get("status"), "score": c.get("score"), "passed": c.get("passed"),
                "failed": c.get("failed") or [], "wer": c.get("wer"), "dur": c.get("duration"), "ttfa": c.get("ttfa"),
                "lat": c.get("latency"), "cost": c.get("cost"),
                "au": str(c.get("au")) if str(c.get("au")) in audio else None,
                "crit": [[VOICE_CRIT.get(k, k), val] for k, val in (c.get("crit") or {}).items()] if c.get("judgeScores") else [],
                "why": [[VOICE_CRIT.get(k, k), (c.get("judgeScores") or {}).get(k), w] for k, w in (c.get("reasoning") or {}).items()],
                "note": c.get("judgeNote") or ""} for i, c in clips]
            x[side] = arm
        x["win"] = sc.get("winner") if sc.get("winner") in ("a", "b", "tie") else None
        x["margin"] = sc.get("margin")
        scens.append(x)

    def order(key):
        if key in VOICE_FIXED:
            present = {v for sc in scens for v in vals(sc, key)}
            return [v for v in VOICE_FIXED[key] if v in present]
        cnt = collections.Counter(v for sc in scens for v in vals(sc, key))
        return sorted(cnt, key=lambda v: (-cnt[v], v))

    dims = [{"key": "language", "label": "Language", "multi": False, "order": order("language")},
            {"key": "industry", "label": "Industry", "multi": False, "order": order("industry")},
            {"key": "customer", "label": "Target customer", "multi": True, "order": order("customer")},
            {"key": "usecase", "label": "Use case", "multi": False, "order": order("usecase")},
            {"key": "task", "label": "Delivery", "multi": False, "order": order("task")},
            {"key": "difficulty", "label": "Difficulty", "multi": False, "order": order("difficulty")},
            {"key": "skill", "label": "Speech skill", "multi": True, "map": False, "order": order("skill")},
            {"key": "speaker", "label": "Speaker", "multi": False, "map": False, "order": order("speaker")}]
    used = {t["au"] for sc in scens for side in ("a", "b") for t in sc[side]["takes"]} - {None}
    media = {
        "key": "voice", "kind": "voice", "label": "Voice (TTS)", "desc": "Read a script aloud",
        "title": f"{VOICE_ARMS['a']['name']} vs {VOICE_ARMS['b']['name']}",
        "arms": VOICE_ARMS, "judge": {"model": D.get("judge")}, "unit": "clip",
        "words": {"unit": "clip", "units": "clips"}, "dims": dims, "scenarios": scens,
        "pricesAsOf": D.get("prices") or {}, "tieBandPP": round(float(D.get("tieBand") or 0) * 10, 2),
        "rounds": len(rounds), "links": False,
        "sections": ["language", "industry", "customer", "usecase", "task", "difficulty"],
        "fam": {"win": "Win %", "pass": "Gates passed", "quality": "Quality mean", "rel": "Reliability", "lat": "Latency p50"},
        "famGloss": {"win": "wins out of the scenarios both models completed",
                     "pass": "share of clips that passed every automatic check · higher is better",
                     "quality": "mean rubric rating on clips that passed · higher is better",
                     "rel": "lowest two-round mean on any single scenario · higher is better",
                     "lat": "whole call, median · lower is better"},
    }
    return media, {k: audio[k] for k in sorted(used, key=int)}


def build(args) -> dict:
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    runs = [Run(r) for r in args.run]
    rubric_mix = check_rubrics(runs, args.allow_rubric_mix)

    models: dict = {}
    for run in runs:
        for m in run.manifest.get("models") or []:
            models.setdefault(m["id"], m)
    if args.arms:
        a_id, b_id = args.arms.split(",", 1)
    else:
        google = [m for m in models if (models[m].get("provider") or "").startswith("google") or "gemini" in m]
        rival = [m for m in models if m not in google]
        if not google or not rival:
            raise SystemExit(f"need one Google and one rival model; found {sorted(models)}")
        a_id, b_id = google[0], rival[0]
    arms = {"a": a_id, "b": b_id}
    picks, sids = merge(runs, (a_id, b_id))

    internal = args.audience == "internal"
    caveats = load_caveats(Path(args.caveats) if args.caveats else DEFAULT_CAVEATS)
    # a client file carries no full-size links unless a shared base URL is given
    link = Linker(out.parent, runs, args.media_base_url, args.link_full, relative_ok=internal)
    imgs = Images(link, args.quality, args.frame_quality)
    scens = [scenario_payload(sid, sids[sid], picks, arms, caveats, imgs, args.thumb_px, args.frame_px, internal)
             for sid in sorted(sids)]
    dmeta = dims_meta(scens, internal)

    # canvas: same delivered size per scenario, and one quality setting per model
    checked = [s for s in scens if s["_canvas"]]
    mismatched = {s["id"]: s["_canvas"]["why"] for s in checked if not s["_canvas"]["ok"]}
    quality = {}
    for side in ("a", "b"):
        vals = {}
        for s in checked:
            q = s["_canvas"]["q" + side]
            if q:
                vals.setdefault("/".join(q), []).append(s["id"])
        quality[side] = sorted(vals)
        if len(vals) > 1:                      # mixed quality inside one model's arm
            main_q = max(vals, key=lambda k: len(vals[k]))
            for q, ids in vals.items():
                if q != main_q:
                    for sid in ids:
                        mismatched[sid] = "; ".join(filter(None, [mismatched.get(sid), f"quality {q} (rest of the run: {main_q})"]))
    for s in scens:
        cv = s.pop("_canvas")
        if s["id"] in mismatched:
            s["canvasIssue"] = mismatched[s["id"]]
        elif cv:
            s["canvas"] = cv["size"]

    def disp(mid):
        return dict(DISPLAY.get(mid) or {"name": mid, "short": mid})

    judge_cfg = next((r.get("judge") for run in reversed(runs) for r in run.judge_rows if r.get("judge")), None) or {}
    created = [r.manifest.get("created") for r in runs if r.manifest.get("created")]
    prices = {k: models.get(mid, {}).get("price_as_of") for k, mid in arms.items()}
    da, db = disp(a_id), disp(b_id)
    words = dict(MEDIA[MEDIA_KEY])
    # gates as the check records hold them, over the cells this report shows
    gate_names, gated, gate_ok = [], 0, 0
    for (sid, mid), p in picks.items():
        ch = p.run.checks.get((sid, mid))
        if not ch:
            continue
        gated += 1
        gate_ok += 1 if ch.get("passed") else 0
        for g in ch.get("gates") or []:
            if g.get("gate") not in gate_names:
                gate_names.append(g.get("gate"))
    pass_example = next((s["passRule"] for s in scens if s.get("passRule")), None)
    cav_note = None
    cav_path = Path(args.caveats) if args.caveats else DEFAULT_CAVEATS
    if cav_path.exists():
        note = (json.loads(cav_path.read_text()).get("note") or "").strip()
        # the note's last sentence ("Report headline results with and without
        # these.") is an instruction to the report's author, not a fact
        cav_note = " ".join(x for x in re.split(r"(?<=\.)\s+(?=[A-Z])", note) if not x.startswith("Report ")) or None
    model_cfg = load_models_config(args.models_config)
    judge_loc = ((model_cfg.get("judge") or {}).get(MEDIA_KEY) or {}).get("vertex", {}).get("location") if model_cfg else None
    media = {
        "key": MEDIA_KEY, "kind": "image", "label": words.pop("label"), "desc": words.pop("desc"),
        "title": f"{da['name']} vs {db['name']}",
        "arms": {"a": da, "b": db},
        "judge": {"model": judge_cfg.get("provider_model"), "temperature": judge_cfg.get("temperature"),
                  "thinking": judge_cfg.get("thinking_level"), "location": judge_loc,
                  "provider": ((model_cfg.get("judge") or {}).get(MEDIA_KEY) or {}).get("provider") if model_cfg else None},
        "unit": words["unit"],
        "words": words,
        "dims": dmeta,
        "canvas": {"checked": len(checked), "mismatched": sorted(mismatched), "quality": quality},
        "gates": {"names": gate_names, "cells": gated, "passed": gate_ok},
        "passExample": pass_example,
        "caveatNote": cav_note if internal else None,
        "links": link.mode != "none",
        "scenarios": scens,
        "pricesAsOf": prices,
    }
    if internal:
        media["details"] = run_details(runs, arms, scens, caveats, args, model_cfg, {"a": da, "b": db})
    for s in scens:
        s.pop("_industryRaw", None)
    data = {
        "schema": "genmedia-client-report/3",
        "audience": args.audience,
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "runs": [r.id for r in runs] if internal else [],
        "runDates": sorted(c[:10] for c in created),
        "study": {"name": "GenMedia Study", "href": "https://studies.adlc.tilicho.in/studies/genmedia-model-comparison"},
        "defaultMedia": MEDIA_KEY,
        "media": {MEDIA_KEY: media},
        "confidentPP": CONFIDENT_MARGIN_PP,
    }
    audio_html = ""
    if getattr(args, "voice_dashboard", None):
        vmedia, vaudio = load_voice(Path(args.voice_dashboard))
        data["media"]["voice"] = vmedia
        if getattr(args, "audio_out", None):
            adir = Path(args.audio_out)
            adir.mkdir(parents=True, exist_ok=True)
            for k, b in vaudio.items():
                (adir / f"{k}.mp3").write_bytes(base64.b64decode(b))
            data["audioBase"] = "audio/"
        else:
            audio_html = "".join(f'<script type="text/plain" id="au-{k}">{b}</script>' for k, b in vaudio.items())
    data["embed"] = bool(getattr(args, "embed", False))
    if getattr(args, "site_out", None):
        sdir = Path(args.site_out)
        (sdir / "img").mkdir(parents=True, exist_ok=True)
        for iid, b in imgs.data.items():
            (sdir / "img" / f"{iid}.jpg").write_bytes(base64.b64decode(b))
        site = dict(data, imgBase="img/", audioBase="audio/", embed=False)
        if data.get("media", {}).get("voice") and not getattr(args, "audio_out", None):
            (sdir / "audio").mkdir(parents=True, exist_ok=True)
            for k, b in vaudio.items():
                (sdir / "audio" / f"{k}.mp3").write_bytes(base64.b64decode(b))
        (sdir / "data.json").write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logo = logo_data(Path(args.logo)) if args.logo else logo_data(DEFAULT_LOGO)

    def blob(obj) -> str:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return s.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    data_s, img_s = blob(data), blob(imgs.data)
    title = f"{media['title']} · GenMedia Study"
    html = (TEMPLATE
            .replace("__TITLE__", _esc(title))
            .replace("__LOGO__", logo or "")
            .replace("__CSS__", CSS)
            .replace("__JS__", JS)
            .replace("__DATA__", data_s)
            .replace("__IMAGES__", img_s)
            .replace("__AUDIO__", audio_html))
    out.write_text(html, encoding="utf-8")
    size = out.stat().st_size
    proj = projection(imgs, len(data_s.encode()), len(scens))
    warnings = []
    if size > SIZE_WARN_BYTES:
        warnings.append(f"report is {size / 1e6:.1f} MB, over {SIZE_WARN_BYTES / 1e6:.0f} MB: "
                        "lower --quality/--frame-quality or --thumb-px/--frame-px")
    if mismatched:
        warnings.append(f"canvas mismatch in {len(mismatched)} of {len(checked)} scenarios: "
                        + "; ".join(f"{k} ({v})" for k, v in sorted(mismatched.items())))
    if rubric_mix:
        warnings.append("merged runs scored with different rubrics (--allow-rubric-mix): " + "; ".join(rubric_mix))
    return {"out": str(out), "bytes": size, "scenarios": len(scens), "images": len(imgs.data),
            "compared": sum(1 for s in scens if s["win"]),
            "wins": {k: sum(1 for s in scens if s["win"] == k) for k in ("a", "b", "tie")},
            "canvas": {"checked": len(checked), "mismatched": sorted(mismatched)},
            "links": link.mode, "projection": proj, "warnings": warnings,
            "_exit": 3 if mismatched else 0}


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", action="append", required=True,
                    help="run id under image/runs (or a run folder path); normally ONE run. Repeat to merge: "
                         "each scenario comes from the latest run in which both models scored it")
    ap.add_argument("--out", required=True, help="HTML file to write")
    ap.add_argument("--caveats", help=f"input-caveats JSON (default {DEFAULT_CAVEATS.relative_to(ROOT)})")
    ap.add_argument("--arms", help="GOOGLE_ID,RIVAL_ID (default: the Google model vs the other one)")
    ap.add_argument("--media-base-url", help="link full-size files as <URL>/<run-id>/<path in run> (e.g. a shared folder)")
    ap.add_argument("--link-full", action="store_true",
                    help="write relative full-size links even when the HTML is outside the run folder (internal copies)")
    ap.add_argument("--allow-rubric-mix", action="store_true",
                    help="merge runs scored with different rubrics (test renders only)")
    ap.add_argument("--thumb-px", type=int, default=480, help="longest side of embedded inputs/outputs")
    ap.add_argument("--frame-px", type=int, default=360, help="longest side of embedded set frames")
    ap.add_argument("--quality", type=int, default=66, help="JPEG quality for inputs/outputs")
    ap.add_argument("--frame-quality", type=int, default=60, help="JPEG quality for set frames")
    ap.add_argument("--logo", help="brand lockup image (default: the site repo's tilicho-brand-logo.webp)")
    ap.add_argument("--audience", choices=("client", "internal"), default="client",
                    help="client: what a client needs; internal: adds the internal categories and Run details")
    ap.add_argument("--models-config", help="the models YAML the run used (internal: rate limits, judge endpoint)")
    ap.add_argument("--sources", default=str(DEFAULT_SOURCES), help="input-image generation log (internal)")
    ap.add_argument("--bank-inputs", default=str(DEFAULT_BANK_INPUTS), help="scenario -> input file map (internal)")
    ap.add_argument("--test-run", action="append", default=[], help="a test run of the same study, for its cost (internal)")
    ap.add_argument("--site-out", help="also write the report's data for the study site: <dir>/data.json, <dir>/img/<id>.jpg, <dir>/audio/<id>.mp3")
    ap.add_argument("--embed", action="store_true", help="for a host page's iframe: no own header or footer; ?media=<lane> opens that lane")
    ap.add_argument("--audio-out", help="write the voice clips as <id>.mp3 into this folder and load them by URL (audio/<id>.mp3 next to the page)")
    ap.add_argument("--voice-dashboard", help="the voice study's dashboard HTML (report-data + audio): adds the Voice (TTS) lane")
    args = ap.parse_args(argv)
    res = build(args)
    code = res.pop("_exit")
    print(json.dumps(res, indent=1, ensure_ascii=False))
    for w in res["warnings"]:
        print("WARNING:", w, file=sys.stderr)
    return code


# --------------------------------------------------------------------------
# Page: production CSS (studies.adlc.tilicho.in, origin/main 056de8bd), then JS
# --------------------------------------------------------------------------

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+Devanagari:wght@400;500&family=Noto+Sans+Tamil:wght@400;500&family=Noto+Sans+Telugu:wght@400;500&family=Noto+Sans+Bengali:wght@400;500&display=swap" rel="stylesheet">
<style>__CSS__</style>
</head>
<body>
<header class="tl-nav">
  <div class="tl-wrap tl-nav-in">
    <a class="lock" href="https://studies.adlc.tilicho.in/" aria-label="Tilicho Labs studies">
      <img class="lock-img" src="__LOGO__" alt="Tilicho Labs" height="30" onerror="this.replaceWith(Object.assign(document.createElement('b'),{className:'lock-txt',textContent:'tilicho labs'}))">
      <span class="lock-div" aria-hidden="true"></span>
      <span class="lock-sub">Multi-Model AI SDLC Orchestrator</span>
    </a>
  </div>
</header>
<div class="strip" id="strip"></div>
<main>
  <section class="tl-sec tl-sec--data"><div class="tl-wrap" id="lane"></div></section>
</main>
<footer class="tl-foot">
  <div class="tl-wrap foot-top">
    <div class="foot-brand">
      <a href="https://tilicho.in?utm_source=studies&amp;utm_medium=report&amp;utm_campaign=footer-brand" target="_blank" rel="noopener noreferrer" aria-label="Tilicho Labs"><img class="lock-img foot-logo" src="__LOGO__" alt="Tilicho Labs" height="52" onerror="this.remove()"></a>
      <p>AI-native enterprise engineering. We modernize enterprise systems and embed intelligence into the workflows businesses depend on.</p>
    </div>
    <div class="foot-meta" id="foot-meta"></div>
  </div>
  <div class="tl-wrap foot-bot"><span>&copy; 2015&ndash;2026 Tilicho Labs. All rights reserved.</span></div>
  <div class="tl-wrap foot-legal">Independent study by Tilicho Labs. Claude Code, Gemini, and other product names and logos referenced here are trademarks of their respective owners; their appearance does not imply endorsement or affiliation.</div>
</footer>
<div id="lb"></div>
<script type="application/json" id="report-data">__DATA__</script>
__AUDIO__
<script type="application/json" id="img-data">__IMAGES__</script>
<script>__JS__</script>
</body>
</html>
"""

CSS = r"""
:root{--ink:#0A0A0A;--body:#1F1F1F;--mt:#6B6B6B;--canvas:#fff;--muted:#F5F5F5;--line:#E5E5E5;
--b50:#FFF6F7;--b100:#F9D2D6;--b200:#F2A6AD;--b500:#E00917;--b600:#C50714;--b700:#A50611;--b800:#7C040D;
--dark:#0A0E16;--dark-ink:#EBEEF3;--dark-mt:#8A96A6;--light:#F2F3F1;--paper:#FBFBFA;
--a:#4285F4;--a-ink:#1A56B8;--b:#B85C38;--b-ink:#8F4227;--c:#7C5CC4;--c-ink:#5B3E9E;--tie:#6B6B6B;
--sh-card:0 1px 2px 0 rgba(15,23,42,.04),0 1px 3px 0 rgba(15,23,42,.06);
--sh-hover:0 4px 12px -2px rgba(15,23,42,.10),0 2px 6px -2px rgba(15,23,42,.06);--sh-pop:0 12px 32px -8px rgba(15,23,42,.18);
--fd:"Space Grotesk",ui-sans-serif,system-ui,sans-serif;--fs:"IBM Plex Sans","IBM Plex Sans Devanagari","Noto Sans Tamil","Noto Sans Telugu","Noto Sans Bengali",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
--fm:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;--chrome:190px}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-padding-top:calc(var(--chrome) + 12px)}
body{margin:0;background:#fff;color:var(--body);font:15px/1.55 var(--fs);-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;overflow-x:hidden}
img{max-width:100%}
a{color:var(--b600);text-decoration:none}a:hover{text-decoration:underline;text-underline-offset:2px}
button{font:inherit;color:inherit}
p{margin:0}
.tnum,.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1,"lnum" 1}
.text-h2,.text-h3,.text-h4,.stat{font-family:var(--fd);font-weight:600;color:var(--ink);margin:0}
.text-h2{font-size:1.5rem;line-height:1.2;letter-spacing:-.02em}
.text-h3{font-size:1.125rem;line-height:1.3;letter-spacing:-.015em}
.text-h4{font-size:1rem;line-height:1.35;letter-spacing:-.01em}
.label{font:500 11px/1.4 var(--fm);letter-spacing:.12em;text-transform:uppercase;color:var(--mt)}
.eyebrow{display:inline-flex;align-items:center;gap:10px;font:500 11.5px/1.55 var(--fm);letter-spacing:.22em;text-transform:uppercase;color:var(--b700)}
.eyebrow::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--b500);animation:tl-pulse 2.4s infinite}
@keyframes tl-pulse{0%{box-shadow:0 0 0 0 rgba(224,9,23,.5)}70%{box-shadow:0 0 0 7px rgba(224,9,23,0)}100%{box-shadow:0 0 0 0 rgba(224,9,23,0)}}
@media (prefers-reduced-motion:reduce){.eyebrow::before{animation:none}}
.mt{color:var(--mt)}.ink{color:var(--ink)}.sm{font-size:14px;line-height:20px}.xs{font-size:12px;line-height:16px}
.mono{font-family:var(--fm)}

/* nav */
.tl-sec{position:relative;width:100%}.tl-sec--data{background:#fff;color:var(--ink);padding:40px 0 72px;overflow:clip}
.tl-wrap{max-width:80rem;margin:0 auto;padding:0 20px}@media(min-width:640px){.tl-wrap{padding:0 32px}}
.embed .tl-nav,.embed .tl-foot{display:none}.embed .strip{top:0}.embed .strip-row{display:none}.embed #compare .lanehead{display:none}.embed #compare .h2h{margin-top:4px}.embed .strip-in{padding-top:4px}
.tl-nav{position:sticky;top:0;z-index:30;background:var(--dark);border-bottom:1px solid rgba(255,255,255,.06)}
.tl-nav-in{height:64px;display:flex;align-items:center;gap:16px}
.lock{display:flex;align-items:center;gap:16px;flex-shrink:0;text-decoration:none!important}
.lock-img{height:30px;width:auto;display:block}.lock-txt{font:600 18px var(--fd);color:#fff}
.lock-div{display:none;height:24px;width:1px;background:rgba(255,255,255,.15)}
.lock-sub{display:none;font-size:14px;font-weight:500;color:rgba(255,255,255,.85);white-space:nowrap}
@media(min-width:640px){.lock-div,.lock-sub{display:inline-block}}
@media(min-width:768px) and (max-width:1023px){.lock-div,.lock-sub{display:none}}
/* study strip */
.strip{position:sticky;top:64px;background:#fff;border-bottom:1px solid var(--line);box-shadow:0 1px 0 rgba(15,23,42,.04);z-index:20}
.strip-in{padding-top:16px}
.strip-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px 40px}
.strip-row>div:first-child{min-height:62px;padding-bottom:8px;transition:height .3s ease-out}.strip-row>div.home{min-height:0;padding-bottom:14px}
.strip .crumbs{transition:opacity .2s}
.strip.compact .crumbs{opacity:0;visibility:hidden}
.strip.compact .strip-row>div:first-child{min-height:0;height:36px;overflow:hidden}
.strip.compact .picker .label{display:none}
@media(prefers-reduced-motion:reduce){.strip-row>div:first-child,.strip .crumbs{transition:none}}
.strip h1{margin:0;font:600 1.5rem/1.25 var(--fd);letter-spacing:-.02em;color:var(--ink);text-wrap:balance}
@media(max-width:639px){.strip h1{font-size:1.25rem}}
.crumbs{margin-top:4px;font-size:14px;line-height:20px;display:flex;gap:6px;flex-wrap:wrap}
.crumbs a{color:var(--mt)}.crumbs .sep{color:var(--mt)}.crumbs .cur{color:var(--ink);font-weight:500}
.picker{position:relative;display:flex;flex-direction:column;flex:none;padding-bottom:6px}
.picker .label{display:block;margin-bottom:6px;font-size:13px;font-weight:600;color:var(--ink)}
.picker-btn{display:flex;align-items:center;gap:10px;min-width:260px;border-radius:8px;border:1px solid var(--line);background:#fff;padding:4px 10px 4px 4px;font:600 14px/1.55 var(--fs);color:var(--ink);box-shadow:0 1px 2px rgba(17,17,17,.06);cursor:pointer;text-align:left;transition:background-color .15s}
.picker-btn:hover{background:rgba(245,245,245,.6)}
.picker-btn .cnt{font-size:11.5px;font-weight:400;color:var(--mt);font-variant-numeric:tabular-nums}
.picker-btn svg.chev{margin-left:auto;color:var(--mt);transition:transform .15s}.picker-btn[aria-expanded=true] svg.chev{transform:rotate(180deg)}
.glyph{flex:none;width:40px;height:24px;border-radius:6px;background:var(--muted);display:flex;align-items:center;justify-content:center}
.glyph.off{background:#fff;box-shadow:inset 0 0 0 1px var(--line)}
.glyph svg{width:30px;height:16px;display:block}
.picker-menu{position:absolute;right:0;top:100%;margin-top:-4px;min-width:320px;background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:var(--sh-hover);padding:4px;z-index:40}
.picker-menu button{display:flex;align-items:center;gap:12px;width:100%;border:0;background:none;padding:8px;border-radius:8px;cursor:pointer;text-align:left;color:var(--body)}
.picker-menu button:hover,.picker-menu button:focus-visible{background:var(--muted)}
.picker-menu button[aria-current]{color:var(--ink)}
.picker-menu .pm-t{min-width:0;flex:1}.picker-menu .pm-n{display:block;font-size:14px;line-height:1.25;font-weight:500}.picker-menu [aria-current] .pm-n{font-weight:600}
.picker-menu .pm-k{display:block;margin-top:2px;font-size:12px;line-height:16px;color:var(--mt)}
.picker-menu .pm-c{font-size:12px;line-height:16px;color:var(--mt);font-variant-numeric:tabular-nums}
.picker-menu .pm-ok{width:16px;height:16px;flex:none;color:var(--ink);visibility:hidden}.picker-menu [aria-current] .pm-ok{visibility:visible}
@media(max-width:1023px){.strip-row{display:block}.picker{padding-bottom:8px}.picker-menu{left:0;right:auto}.strip.compact .strip-row>div:first-child{display:none}}
@media(max-width:639px){.picker-btn{width:100%;min-width:0}.picker-menu{width:100%;min-width:0}}
.tabs{display:flex;gap:24px;overflow-x:auto;scrollbar-width:none;margin:0 -20px;padding:0 20px}.tabs::-webkit-scrollbar{display:none}
@media(min-width:640px){.tabs{margin:0 -32px;padding:0 32px}}
.tab{margin-bottom:-1px;padding:10px 0;font-size:14px;line-height:20px;font-weight:500;color:var(--mt);border-bottom:2px solid transparent;display:inline-flex;gap:6px;align-items:center;white-space:nowrap;text-decoration:none!important}
.tab svg{width:16px;height:16px;flex-shrink:0}
.tab:hover{color:var(--ink);border-color:var(--line)}.tab[aria-current]{color:var(--b600);font-weight:600;border-color:var(--b500)}

/* surfaces, pills, buttons */
.card{background:#fff;border:1px solid var(--line);border-radius:1.125rem;box-shadow:var(--sh-card)}.card-pad{padding:24px}
.card-hover{transition:box-shadow .15s,border-color .15s}.card-hover:hover{box-shadow:var(--sh-hover);border-color:#CBD5E1}
.pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;font-size:12px;line-height:16px;font-weight:500;white-space:nowrap}
.pill-success{background:#ECFDF5;color:#047857;box-shadow:inset 0 0 0 1px #A7F3D0}.pill-danger{background:#FFF1F2;color:#BE123C;box-shadow:inset 0 0 0 1px #FECDD3}
.pill-warning{background:#FFFBEB;color:#92400E;box-shadow:inset 0 0 0 1px #FDE68A}.pill-info{background:#EFF6FF;color:#1D4ED8;box-shadow:inset 0 0 0 1px #BFDBFE}
.pill-neutral{background:#F1F5F9;color:#475569;box-shadow:inset 0 0 0 1px #E2E8F0}.pill-ink{background:var(--ink);color:#fff}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 14px;border-radius:8px;font:500 14px/20px var(--fs);border:1px solid transparent;cursor:pointer;text-decoration:none!important}
.btn-sm{padding:4px 10px;border-radius:6px;font-size:12px;line-height:16px}.btn-secondary{background:#fff;color:#334155;border-color:var(--line)}
.btn-secondary:hover{background:var(--muted)}.btn-primary{background:var(--ink);color:#fff}
:focus-visible{outline:none;box-shadow:0 0 0 1px #fff,0 0 0 3px rgba(224,9,23,.4);border-radius:6px}
.linkbtn{border:0;background:none;padding:0;font:500 12px/16px var(--fs);color:var(--b600);cursor:pointer}.linkbtn:hover{text-decoration:underline;text-underline-offset:2px}

/* lane head */
.lanehead{max-width:48rem}.lanehead>.eyebrow{display:flex;width:max-content}.lanehead .sub{margin-top:6px;font-size:14px;line-height:20px;color:var(--mt)}

/* head-to-head */
.h2h{max-width:42rem;margin-top:24px;padding:0 20px 16px}
.h2h-title{padding-top:16px;text-align:center}.h2h-title div:first-child{font-size:14px;line-height:20px;font-weight:600;color:var(--ink)}
.arms{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:center;margin-top:8px;padding-bottom:4px;font:500 11px/1.55 var(--fm)}
.arms .ra{text-align:right}
.mrow{padding:8px 0;border-bottom:1px solid var(--line)}.mrow:last-of-type{border-bottom:0}
.mrow .mh{text-align:center}.mrow .mh .label{color:var(--ink)}.mrow .gloss{display:block;font-size:10px;line-height:1.55;color:var(--mt);margin-top:2px}
.mgrid{display:grid;grid-template-columns:72px 1fr 1fr 72px;gap:8px;align-items:center;margin-top:6px}
.mgrid>span:last-child{text-align:right}
.track{position:relative;display:block;height:8px;border-radius:3px;background:var(--muted);overflow:hidden}
.fill{position:absolute;top:0;bottom:0;border-radius:3px}.fill.a{right:0;background:var(--a)}.fill.b{left:0;background:var(--b)}.fill.trail{opacity:.34}
.lead{display:inline-block;border:1px solid;border-radius:999px;padding:2px 8px;font-size:12px;line-height:16px;font-weight:600;font-variant-numeric:tabular-nums}
.lead.a{border-color:var(--a);color:var(--a-ink)}.lead.b{border-color:var(--b);color:var(--b-ink)}.trailv{font-size:12px;line-height:16px;color:var(--mt);font-variant-numeric:tabular-nums}
.tally{padding:10px 0;border-top:1px solid var(--line)}.tally .th{text-align:center;font-size:12px;line-height:16px;font-weight:500;color:var(--ink)}
.tally .th small{display:block;font-size:10px;font-weight:400;color:var(--mt);margin-top:2px}
.tallybar{display:flex;height:24px;border-radius:4px;overflow:hidden;margin-top:8px;background:var(--muted)}
.tallybar span{display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-variant-numeric:tabular-nums;min-width:0;overflow:hidden}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:8px;font-size:11px;line-height:1.55;color:var(--mt)}
.sw{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;vertical-align:0}
.notes{margin:12px 0 0;padding-left:20px;font-size:14px;line-height:20px;color:var(--mt);display:flex;flex-direction:column;gap:4px;max-width:56rem}
.notes b{color:var(--ink);font-weight:500}
#compare .notes{max-width:42rem}

/* blocks */
.block{margin-top:32px}
.bhead{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:8px 16px}
.bhead .hint{margin-top:2px;font-size:12px;line-height:16px;color:var(--mt);max-width:52rem}
.ctrl{margin-top:12px;padding:16px 20px}
.ctrl-row{display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px}
.ctrl-row+.ctrl-row{margin-top:14px}
.toggle{display:inline-flex;border:1px solid var(--line);border-radius:8px;background:#fff;padding:2px;flex-wrap:wrap}
.toggle button{border:0;background:none;border-radius:6px;padding:4px 12px;font:500 12px/16px var(--fs);color:var(--body);cursor:pointer;white-space:nowrap}
.toggle button:hover{background:var(--muted)}.toggle [aria-selected=true],.toggle [aria-selected=true]:hover{background:var(--ink);color:#fff}
.sort{display:inline-flex;align-items:stretch;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff;max-width:100%}
.sort:focus-within{border-color:var(--ink)}
.sort .label,.ctrl-lbl{font:600 14px/20px var(--fd);letter-spacing:-.01em;text-transform:none;color:var(--ink)}
.sort .label{display:inline-flex;align-items:center;padding:0 12px;border-right:1px solid var(--line);background:rgba(245,245,245,.6);white-space:nowrap}
.ctrl-g{display:inline-flex;align-items:center;gap:10px}
.li{width:15px;height:15px;flex:none;color:var(--mt);vertical-align:-2px;margin-right:6px}.ctrl-lbl,.sort .label{display:inline-flex;align-items:center}.sg-sub .li{width:13px;height:13px;margin-right:5px}
.sort .sel{position:relative;display:inline-flex;align-items:center;min-width:0}
.sort select{appearance:none;-webkit-appearance:none;border:0;background:transparent;padding:8px 36px 8px 12px;font:500 14px/20px var(--fs);color:var(--ink);cursor:pointer;max-width:100%;text-overflow:ellipsis}
.sort select:focus{outline:none;box-shadow:none}
.sort .sel svg{position:absolute;right:12px;width:14px;height:14px;color:var(--mt);pointer-events:none}
.fgroup{min-width:200px}.fgroup>.label{font-weight:600;margin-bottom:8px;display:block}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chipq{display:block;width:min(100%,260px);margin:0 0 8px;border:1px solid var(--line);border-radius:8px;padding:6px 10px;font:500 12px/16px var(--fs);color:var(--ink);background:#fff}.chipq:focus{outline:none;border-color:var(--ink)}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:6px 12px;font:500 12px/16px var(--fs);background:#fff;color:var(--body);cursor:pointer;transition:border-color .15s,background .15s;text-align:left}
.chip:hover{border-color:var(--ink)}.chip[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:#fff}
.chip.a{border-color:var(--a);color:var(--a-ink)}.chip.a[aria-pressed=true]{background:var(--a);color:#fff}
.chip.b{border-color:var(--b);color:var(--b-ink)}.chip.b[aria-pressed=true]{background:var(--b);color:#fff}.chip.zero:not([aria-pressed=true]){opacity:.6}
.chip .n{display:inline-flex;justify-content:center;min-width:1.75rem;border-radius:999px;padding:0 6px;font-size:10.5px;line-height:16px;background:var(--muted);color:var(--mt);font-variant-numeric:tabular-nums}
.chip[aria-pressed=true] .n{background:rgba(255,255,255,.15);color:rgba(255,255,255,.85)}
.switchrow{display:flex;height:30px;align-items:center;gap:10px}
.switch{position:relative;display:inline-block;width:36px;height:20px;border-radius:999px;background:var(--line);border:0;cursor:pointer;flex-shrink:0;padding:0}
.switch[aria-checked=true]{background:var(--a)}
.switch::after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.05);transition:left .15s}
.switch[aria-checked=true]::after{left:18px}
.nbadge{display:inline-flex;justify-content:center;min-width:1.75rem;border-radius:999px;padding:0 6px;font-size:10.5px;line-height:16px;background:var(--muted);color:var(--mt);font-variant-numeric:tabular-nums}
.more{margin-top:4px}
.fold>summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;gap:10px;color:var(--b600)}.fold>summary::-webkit-details-marker{display:none}.fold>summary svg{width:14px;height:14px;transition:transform .15s}.fold[open]>summary svg{transform:rotate(180deg)}
.more>summary{list-style:none;cursor:pointer;font:500 12px/16px var(--fs);color:var(--b600);display:inline-flex;align-items:center;gap:6px}
.more>summary::-webkit-details-marker{display:none}
.more>summary svg{width:14px;height:14px;transition:transform .15s}.more[open]>summary svg{transform:rotate(180deg)}
.fstack{display:flex;flex-direction:column;align-items:flex-start;gap:16px;margin-top:16px}

/* win map */
.wm-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px;margin-top:14px}
.wt-name{font-size:14px;line-height:20px;font-weight:500;color:var(--ink);overflow-wrap:anywhere}
.wt-nums{display:flex;align-items:flex-end;justify-content:space-between;gap:10px}.wt-nums>span{min-width:0}
.wt-nums .v{font:600 17px/1.1 var(--fd);letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.wt-nums .k{display:block;margin-top:3px;font:500 10.5px/1.3 var(--fm);letter-spacing:.04em;color:var(--mt);overflow-wrap:anywhere}
.wt-nums .r{text-align:right}.wt-nums .c{text-align:center;flex:none;white-space:nowrap}.wt-nums .c .k{white-space:nowrap}.wt-nums .tie{display:block;font:500 13px/1.3 var(--fs);color:var(--mt);font-variant-numeric:tabular-nums}
.wt-k2{margin-top:4px;padding-top:8px;border-top:1px dashed var(--line)}.wt-nums2 .v{font-size:14px}
.wt-x{display:grid;grid-template-columns:minmax(0,1fr) auto auto;column-gap:12px;row-gap:3px;margin-top:8px;padding-top:6px;border-top:1px dashed var(--line);font-size:12px;line-height:18px}
.wt-xr{display:contents}
.wt-x .xl{color:var(--mt);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.wt-x .xv{text-align:right;font-variant-numeric:tabular-nums;font-weight:500;justify-self:end;min-width:56px;padding:0 7px;border-radius:999px;box-sizing:border-box}.wt-x .xv.a{color:#1A56B8}.wt-x .xv.b{color:#8F4227}
.wt-x .xv.lead{}.wt-x .xv.lead.a{background:#EAF1FE;box-shadow:inset 0 0 0 1px #BFD3F8}.wt-x .xv.lead.b{background:#FBF0EA;box-shadow:inset 0 0 0 1px #EBC6B4}
.wt-bar{display:flex;height:8px;border-radius:3px;overflow:hidden;background:var(--muted)}
.wt-bar i{display:block;height:100%}
.wt-foot{display:flex;flex-wrap:wrap;justify-content:space-between;gap:2px 10px;font-size:11px;line-height:16px;color:var(--mt);font-variant-numeric:tabular-nums}
.steps{align-items:center}.step{background:none;border-top:0;border-left:0;border-right:0;cursor:pointer;font-family:inherit}
.step .sn{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:999px;border:1.5px solid currentColor;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums}
.step .sn.on{background:var(--b500);border-color:var(--b500);color:#fff}.step .sn.done{background:var(--ink);border-color:var(--ink);color:#fff}
.step-gap{flex:1}
.medgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin-top:16px;margin-bottom:32px}
.mcard{display:flex;flex-direction:column;gap:10px;text-align:left;font:inherit;color:inherit;background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;cursor:pointer;transition:border-color .15s,transform .15s}
.mcard:hover{border-color:#CBD5E1;transform:translateY(-1px)}.mcard.on{border-color:var(--b500);box-shadow:0 0 0 1px var(--b500)}
.mc-top{display:flex;align-items:center;gap:10px}.mc-n{font:600 17px/24px var(--fd);color:var(--ink)}
.mc-c{font:500 11px/16px var(--fm);letter-spacing:.12em;text-transform:uppercase;color:var(--mt)}.mc-c b{font:600 30px/1 var(--fd);letter-spacing:-.02em;color:var(--ink);margin-right:8px;vertical-align:-4px;text-transform:none}
.mc-d{font-size:13px;line-height:19px;color:var(--body)}.mc-arms{font-size:12.5px;color:var(--mt)}
.mc-go{font:500 11.5px/16px var(--fm);letter-spacing:.12em;text-transform:uppercase;color:var(--b600)}
.ev-hero{padding:4px 0 16px;border-bottom:1px solid var(--line);margin-bottom:12px}.ev-h{margin:8px 0 6px;font:600 32px/1.15 var(--fd);letter-spacing:-.02em;color:var(--ink)}
.lbar{position:sticky;top:var(--chrome,190px);z-index:7;display:flex;flex-wrap:wrap;align-items:center;gap:10px 12px;padding:10px 0;background:rgba(255,255,255,.96);backdrop-filter:blur(6px);border-bottom:1px solid var(--line)}
.fbtn{height:40px}.fbtn svg{width:16px;height:16px}.fbadge{margin-left:2px;min-width:20px;height:20px;padding:0 6px;border-radius:999px;background:var(--b500);color:#fff;font-size:11px;font-weight:600;display:inline-flex;align-items:center;justify-content:center}
.sbox{position:relative;flex:1;min-width:200px;max-width:360px}.sbox svg{position:absolute;left:10px;top:50%;width:16px;height:16px;transform:translateY(-50%);color:var(--mt)}
.sbox input{width:100%;height:40px;border:1px solid var(--line);border-radius:8px;padding:0 12px 0 34px;font:500 14px/20px var(--fs);color:var(--ink);background:#fff}.sbox input:focus{outline:none;border-color:var(--ink)}
.lcount{margin-left:auto;font-size:13px;color:var(--mt)}.lcount b{color:var(--ink);font-weight:600}
.achips{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:12px}
.ev-tools{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}
.sh{position:fixed;inset:0;z-index:60}.sh-back{position:absolute;inset:0;background:rgba(10,14,22,.55);backdrop-filter:blur(2px)}
.sh-panel{position:absolute;left:0;top:0;height:100%;width:min(420px,100%);background:#fff;box-shadow:0 10px 40px rgba(15,23,42,.25);display:flex;flex-direction:column}.sh-panel.anim{animation:shin .25s ease-out}
@keyframes shin{from{transform:translateX(-100%)}to{transform:none}}
.sh-head{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line)}.sh-n{margin:2px 0 0;font-size:13px;color:var(--mt)}.sh-n b{color:var(--ink)}
.sh-clear{margin-left:auto;font-size:13px}.sh-x{width:32px;height:32px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--mt);font-size:18px;line-height:1;cursor:pointer}.sh-x:hover{color:var(--ink);background:var(--muted)}
.sh-body{flex:1;overflow-y:auto;overscroll-behavior:contain;padding:4px 20px 12px}
.sg{border-top:1px solid var(--line);padding:10px 0}.sg:first-child{border-top:0}
.sg-h{width:100%;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 0;background:none;border:0;cursor:pointer;text-align:left}.sg-h svg{width:14px;height:14px;color:var(--mt);transition:transform .15s}.sg-h[aria-expanded=false] svg{transform:rotate(-90deg)}
.sg-b{margin-top:4px}
.srow-opt{width:100%;display:flex;align-items:center;gap:10px;padding:6px 6px;border:0;border-radius:4px;background:none;cursor:pointer;text-align:left;font:400 14px/20px var(--fs);color:var(--body)}.srow-opt:hover{background:var(--muted)}.srow-opt.off{opacity:.4;cursor:not-allowed}
.srow-opt .cb{width:16px;height:16px;flex:none;border:1.5px solid #BDBDBD;border-radius:3px;background:#fff;display:inline-flex;align-items:center;justify-content:center}.srow-opt .cb.on{background:var(--b500);border-color:var(--b500)}.srow-opt .cb svg{width:10px;height:10px}
.srow-opt .ol{flex:1;min-width:0}.srow-opt[aria-checked=true] .ol{color:var(--ink);font-weight:500}
.srow-opt .wp{font-size:11px;color:var(--mt);font-variant-numeric:tabular-nums;white-space:nowrap}.srow-opt .oc{font-size:12px;color:var(--mt);font-variant-numeric:tabular-nums;min-width:22px;text-align:right}
.sg-sub{padding:8px 6px 2px;font:500 10.5px/14px var(--fm);letter-spacing:.1em;text-transform:uppercase;color:var(--mt)}
.srow-opt .rb{width:16px;height:16px;flex:none;border:1.5px solid #BDBDBD;border-radius:999px;background:#fff;position:relative}.srow-opt .rb.on{border-color:var(--b500)}.srow-opt .rb.on::after{content:'';position:absolute;inset:3px;border-radius:999px;background:var(--b500)}
.sh-foot{padding:12px 20px;border-top:1px solid var(--line)}
.wtile{position:relative;display:flex;flex-direction:column;gap:12px;text-align:left;background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 18px 14px;cursor:pointer;overflow:hidden;font:inherit;color:inherit;box-shadow:0 1px 2px rgba(15,23,42,.04);transition:transform .15s,box-shadow .15s,border-color .15s}
.wtile::before{content:'';position:absolute;left:0;right:0;top:0;height:3px;background:var(--tc,#CBD5E1)}
.wtile:hover{transform:translateY(-2px);box-shadow:0 10px 24px rgba(15,23,42,.08);border-color:#D5DBE3}
.wtile[aria-pressed=true]{border-color:var(--b500);box-shadow:0 0 0 1px var(--b500),0 10px 24px rgba(224,9,23,.08);background:linear-gradient(180deg,#FFF8F8 0,#fff 60%)}
.wt-head{display:flex;align-items:center;gap:10px;min-width:0}
.wt-n{margin-left:auto;flex:none;min-width:28px;height:22px;padding:0 8px;border-radius:999px;background:var(--muted);color:var(--mt);font:600 11.5px/22px var(--fs);text-align:center;font-variant-numeric:tabular-nums}
.wt-check{margin-left:auto;flex:none;width:22px;height:22px;border-radius:999px;background:var(--b500);display:inline-flex;align-items:center;justify-content:center}.wt-check svg{width:12px;height:12px}
.wt-body{display:flex;flex-direction:column;gap:10px;padding:12px 12px 10px;margin:0 -2px;border-radius:10px;background:#F8FAFC}
.wt-nums .v.lead{display:inline-block;border-radius:999px;padding:1px 9px;margin-left:-4px}
.wt-nums .v.lead.a{color:#1A56B8;background:#EAF1FE;box-shadow:inset 0 0 0 1px #BFD3F8}.wt-nums .v.lead.b{color:#8F4227;background:#FBF0EA;box-shadow:inset 0 0 0 1px #EBC6B4}
.wt-nums .r .v.lead{margin-left:0;margin-right:-4px}
.wtile .wt-bar{height:6px;border-radius:999px}
.wtile .wt-foot{font-size:11.5px}.wtile .wt-foot b{font-weight:600}
.wtile.zero{align-self:start;opacity:.5;cursor:not-allowed;padding:14px 18px;box-shadow:none;background:#FAFAFA}.wtile.zero::before{display:none}.wtile.zero:hover{transform:none;box-shadow:none}
.oi{width:22px;height:22px;flex:none;border-radius:6px;display:inline-flex;align-items:center;justify-content:center}.oi svg{width:13px;height:13px}
.oi.lg{width:34px;height:34px;border-radius:9px}.oi.lg svg{width:18px;height:18px}
.oi.meter{gap:2px;align-items:flex-end;padding-bottom:6px;background:#F1F5F9}.oi.meter i{display:block;width:3px;border-radius:1px}.oi.lg.meter{padding-bottom:9px}.oi.lg.meter i{width:4px}
.wm-hn{font:500 12px/16px var(--fs);color:var(--mt)}
.dd{position:relative;display:inline-flex;align-items:center;gap:10px}
.dd-btn{display:inline-flex;align-items:center;gap:10px;height:40px;padding:0 12px 0 14px;border:1px solid var(--line);border-radius:8px;background:#fff;font:500 14px/20px var(--fs);color:var(--ink);cursor:pointer}
.dd-btn:hover{border-color:#CBD5E1}.dd-btn[aria-expanded=true]{border-color:var(--ink)}.dd-btn svg{width:14px;height:14px;color:var(--mt);transition:transform .15s}.dd-btn[aria-expanded=true] svg{transform:rotate(180deg)}
.dd-menu{position:absolute;z-index:20;top:calc(100% + 6px);left:0;width:340px;max-width:calc(100vw - 32px);background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 32px rgba(15,23,42,.14);padding:8px}
.dd-h{padding:6px 8px 8px;font:500 11px/16px var(--fm);letter-spacing:.12em;text-transform:uppercase;color:var(--mt);display:flex;align-items:center}.dd-h .li{width:13px;height:13px}
.dd-menu.sortm{width:320px;max-height:360px;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin}.dd-menu.sortm .dd-h{padding-top:10px;border-top:1px solid var(--line);margin-top:4px}.dd-menu.sortm .dd-h:first-child{border-top:0;margin-top:0;padding-top:6px}.dd-menu.sortm .dd-opt{padding:7px 8px;align-items:center}.dd-menu.sortm .dd-opt .rb{flex:none;width:16px;height:16px;border-radius:50%;border:1.5px solid #BDBDBD;background:#fff;display:inline-block;box-sizing:border-box}.dd-menu.sortm .dd-opt .rb.on{border:5px solid var(--b500)}.dd-menu.sortm .dd-t b{font-weight:500}.dd-menu.sortm .dd-opt[aria-selected=true] .dd-t b{font-weight:600;color:var(--ink)}
.dd-opt{width:100%;display:flex;align-items:flex-start;gap:10px;padding:8px;border:0;border-radius:8px;background:none;text-align:left;cursor:pointer;font:inherit}.dd-opt:hover{background:var(--muted)}.dd-opt[disabled]{cursor:default}.dd-opt[disabled]:hover{background:none}
.dd-opt .cb{margin-top:2px;width:16px;height:16px;flex:none;border:1.5px solid #BDBDBD;border-radius:3px;background:#fff;display:inline-flex;align-items:center;justify-content:center}.dd-opt .cb.on{background:var(--b500);border-color:var(--b500)}.dd-opt .cb svg{width:10px;height:10px}.dd-opt[disabled] .cb.on{background:#94A3B8;border-color:#94A3B8}
.dd-t{display:flex;flex-direction:column;gap:2px;min-width:0}.dd-t b{font-size:14px;line-height:20px;font-weight:500;color:var(--ink)}.dd-t span{font-size:12px;line-height:17px;color:var(--mt)}.dd-t em{font-style:normal;font-size:11.5px;line-height:16px;color:var(--b600)}
.bk-hero{position:relative;margin:-40px calc(50% - 50vw) -72px;padding:56px 0 72px;background:var(--dark);color:var(--dark-ink);background-image:radial-gradient(rgba(255,255,255,.05) 1px,transparent 1px);background-size:22px 22px}
.bk-in{max-width:80rem;margin:0 auto;padding:0 20px}@media(min-width:640px){.bk-in{padding:0 32px}}
.bk-eye{color:rgba(255,255,255,.72)!important}.bk-eye::before{background:var(--b500)}
.bk-h{margin:18px 0 0;font:600 clamp(36px,5.2vw,64px)/1.05 var(--fd);letter-spacing:-.03em;color:#fff}
.bk-sub{margin:16px 0 0;max-width:42rem;font-size:17px;line-height:27px;color:rgba(255,255,255,.75)}
.bk-grid{margin-top:32px;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.bk{position:relative;display:flex;flex-direction:column;gap:12px;text-align:left;font:inherit;color:inherit;border:1px solid rgba(255,255,255,.10);border-radius:6px;background:rgba(255,255,255,.04);padding:22px;cursor:pointer;transition:border-color .2s,transform .2s;overflow:hidden}
.bk:hover{border-color:rgba(255,255,255,.28);transform:translateY(-2px)}.bk::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:2px;background:var(--b500);transform:scaleX(0);transform-origin:left;transition:transform .3s}.bk:hover::after{transform:scaleX(1)}
.bk.off{cursor:default;opacity:.45}.bk.off:hover{transform:none;border-color:rgba(255,255,255,.10)}.bk.off::after{display:none}
.bk-ic{width:44px;height:44px;border-radius:6px;background:rgba(255,255,255,.08);display:inline-flex;align-items:center;justify-content:center;color:#fff}.bk-ic svg{width:22px;height:22px}
.bk-n{margin:4px 0 0;font:600 19px/26px var(--fs);color:#fff}
.bk-c{font:500 11px/16px var(--fm);letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.55)}.bk-c b{font:600 34px/1 var(--fd);letter-spacing:-.02em;color:#fff;margin-right:10px;vertical-align:-5px}
.bk-l{margin:0;font-size:15px;line-height:24px;color:rgba(255,255,255,.68)}
.bk-k{font-size:13px;line-height:18px;color:rgba(255,255,255,.55)}
.bk-mix{display:flex;gap:2px;height:4px;border-radius:999px;overflow:hidden;margin-top:auto}.bk-mix i{display:block;height:100%}
.bk-go{display:inline-flex;align-items:center;gap:8px;font:500 12px/16px var(--fm);letter-spacing:.14em;text-transform:uppercase;color:#fff;transition:gap .2s}.bk:hover .bk-go{gap:12px}.bk-go svg{width:15px;height:15px}
.mbgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,420px));gap:16px;margin-top:16px}
.mb{position:relative;display:flex;flex-direction:column;gap:12px;text-align:left;font:inherit;color:inherit;background:#fff;border:1px solid var(--line);border-top:3px solid var(--b500);border-radius:14px;padding:18px 20px 20px;cursor:pointer;overflow:hidden;box-shadow:0 1px 2px rgba(15,23,42,.04);transition:transform .15s,box-shadow .15s,border-color .15s}
.mb:hover{transform:translateY(-2px);box-shadow:0 10px 24px rgba(15,23,42,.08);border-color:#D5DBE3;border-top-color:var(--b500)}
.mb-top{display:flex;align-items:center;gap:14px}.mb-n{display:block;font:600 18px/24px var(--fd);color:var(--ink)}.mb-l{display:block;margin-top:2px;font-size:13.5px;line-height:20px;color:var(--body)}
.mb-c{display:flex;align-items:baseline;gap:6px;font-size:13px;color:var(--mt)}.mb-c b{font:600 28px/1 var(--fd);letter-spacing:-.02em;color:var(--ink)}
.mb-k,.mb-arms{font-size:12.5px;line-height:18px;color:var(--mt)}
.mb-mix{display:flex;gap:2px;height:6px;border-radius:999px;overflow:hidden}.mb-mix i{display:block;height:100%}
.mb-leg{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11.5px;color:var(--mt)}.mb-leg .sw{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px}
.mb-go{font:500 11.5px/16px var(--fm);letter-spacing:.12em;text-transform:uppercase;color:var(--b600)}
.wm-sec{margin-top:28px}.wm-h{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0;font:600 18px/26px var(--fd);letter-spacing:-.01em;color:var(--ink)}
.wm-more{margin-top:10px;font-size:13px}
.wtile.zero{opacity:.45;cursor:not-allowed;padding-bottom:14px;background:#FAFAFA}
.wm-bar{position:sticky;bottom:12px;z-index:6;margin-top:24px;display:flex;flex-wrap:nowrap;align-items:center;gap:10px 18px;padding:12px 16px;background:rgba(255,255,255,.97);border:1px solid var(--line);border-radius:12px;box-shadow:0 8px 24px rgba(15,23,42,.10)}
.wm-count{font-size:13px;color:var(--mt);white-space:nowrap}.wm-count b{font:600 26px/1 var(--fd);color:var(--ink);margin-right:8px;vertical-align:-3px}
.wm-mini{flex:none;white-space:nowrap;font-size:12px;line-height:16px;color:var(--mt);font-variant-numeric:tabular-nums}@media(max-width:1180px){.wm-mini{display:none}}@media(max-width:760px){.wm-bar{flex-wrap:wrap}}
.wm-bar .btn{flex:none;white-space:nowrap}.wm-mini b{font-weight:600}
.wm-picks{flex:1;min-width:0;display:flex;flex-wrap:nowrap;align-items:center;gap:6px;overflow:hidden;-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent);mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent)}
.wm-picks .pk{flex:none;max-width:190px}.pk-t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pk-more{flex:none;display:inline-flex;align-items:center;gap:4px;height:26px;padding:0 8px 0 12px;border-radius:999px;border:1px solid var(--line);background:#fff;font:600 12px/22px var(--fs);color:var(--ink);cursor:pointer}.pk-more:hover,.pk-more[aria-expanded=true]{border-color:var(--ink)}.pk-more svg{width:12px;height:12px;transform:rotate(180deg);transition:transform .15s}.pk-more[aria-expanded=true] svg{transform:none}
.pk-pop{position:absolute;left:16px;right:16px;bottom:calc(100% + 8px);max-height:min(55vh,460px);overflow-y:auto;background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 -12px 32px rgba(15,23,42,.14);padding:12px 16px 14px}
.pk-pop-h{display:flex;align-items:center;justify-content:space-between;padding-bottom:8px;border-bottom:1px solid var(--line);font-size:14px;color:var(--ink)}
.pk-g{padding-top:10px}.pk-g .label{display:block;margin-bottom:6px}.pk-gc{display:flex;flex-wrap:wrap;gap:6px}
.pk{display:inline-flex;align-items:center;min-width:0;gap:2px;border-radius:999px;background:var(--muted);box-shadow:inset 0 0 0 1px var(--line);padding:2px 3px 2px 10px;font-size:12px;line-height:18px;color:var(--ink)}
.pk button{border:0;background:none;cursor:pointer;width:20px;height:20px;border-radius:999px;color:var(--mt);font-size:14px;line-height:1}.pk button:hover{background:#fff;color:var(--ink)}
.wm-go{background:var(--b500);color:#fff}.wm-go:hover{background:var(--b600)}.wm-go[disabled]{opacity:.5;cursor:not-allowed}
.chip .wp{font-size:10.5px;line-height:16px;color:var(--mt);font-variant-numeric:tabular-nums}.chip[aria-pressed=true] .wp b{color:#fff!important}
.wm-legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:10px;font-size:11px;line-height:16px;color:var(--mt)}

.wt-k{font:500 10.5px/1.45 var(--fm);letter-spacing:.04em;color:var(--mt);text-transform:uppercase}
.wt-lead{margin-top:-6px;font-size:12px;line-height:16px;color:var(--mt);font-variant-numeric:tabular-nums}.wt-lead b{color:var(--ink);font-weight:600}
.wmt .rowlink .pill{display:none}.wmt .rowlink[aria-pressed=true] .pill{display:inline-flex}
.wt-nums .v{color:var(--ink)}
.wt-nums .vg{display:inline-block;border-radius:999px;padding:1px 8px;background:#ECFDF5;color:#047857;box-shadow:inset 0 0 0 1px #A7F3D0}
.wm-legend .label{margin-right:2px}

/* tables */
.tblwrap{margin-top:12px;overflow-x:auto}
.tbl{width:100%;border-collapse:collapse;font-size:14px;line-height:20px}
.tbl thead th{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);background:rgba(245,245,245,.6);color:var(--mt);font-size:11px;line-height:20px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;white-space:nowrap}
.tbl thead tr:first-child th{border-bottom:1px solid var(--line)}
.tbl th.r,.tbl td.r{text-align:right}.tbl th.c,.tbl td.c{text-align:center}.tbl .bl{border-left:1px solid var(--line)}.tbl th.bot{vertical-align:bottom}
.tbl tbody td{padding:10px 12px;border-bottom:1px solid rgba(229,229,229,.7);vertical-align:middle}.tbl tbody tr:last-child td{border-bottom:0}
.tbl tbody tr:hover{background:rgba(245,245,245,.5)}
.tbl td.r{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1,"lnum" 1}.tbl tr.on,.tbl tr.on:hover{background:var(--muted)}
.rowlink{display:inline-flex;align-items:center;gap:8px;background:none;border:0;padding:0;font:500 14px/20px var(--fs);color:var(--b600);cursor:pointer;text-align:left}
.rowlink:hover{text-decoration:underline;text-underline-offset:2px}
.rowlink .pill{visibility:hidden}.rowlink[aria-pressed=true] .pill{visibility:visible}
.op6{opacity:.6}.strong{color:var(--ink);font-weight:400}
.wonmore{display:flex;align-items:center;gap:8px;font-size:12px;line-height:16px;color:var(--mt);min-width:120px}
.wbar{flex:1;min-width:54px;height:6px;border-radius:3px;background:var(--muted);overflow:hidden;display:flex}.wbar i{display:block;height:100%}

/* evidence */
.ev-top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px 24px}
.ev-count{display:flex;align-items:baseline;gap:12px}.ev-count b{font-size:14px;line-height:20px;font-weight:600;color:var(--ink)}
.rows{margin-top:16px;display:flex;flex-direction:column;gap:10px}
.srow{overflow:hidden}
.srow>summary{list-style:none;cursor:pointer;padding:14px 20px;display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}
.srow>summary::-webkit-details-marker{display:none}
.srow .sid{font:400 11px/16px var(--fm);color:var(--mt);letter-spacing:.025em}
.srow .stitle{flex:1;min-width:200px;font-size:14px;line-height:1.375;font-weight:600;color:var(--ink)}
.srow .scores{font-size:14px;line-height:20px;white-space:nowrap;font-variant-numeric:tabular-nums}.srow .scores b{font-weight:500}
.srow .thumbs{display:flex;gap:4px}
.thumb{width:40px;height:40px;border-radius:4px;object-fit:cover;border:1px solid var(--line);background:var(--muted);display:block}
.thumb.src{border:2px solid var(--a)}
.thumb.none{display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--mt)}
.chev{width:16px;height:16px;color:var(--mt);transition:transform .15s;flex-shrink:0}.srow[open]>summary .chev{transform:rotate(180deg)}
.sbody{padding:0 20px 20px;border-top:1px dashed var(--line)}
.briefs{margin-top:16px;display:grid;grid-template-columns:1fr;gap:12px}@media(min-width:768px){.briefs{grid-template-columns:1fr 1fr}}
.brief{border-radius:8px;background:var(--muted);padding:12px 16px;font-size:14px;line-height:20px;color:var(--body);white-space:pre-line;overflow-wrap:anywhere}
.brief b{color:var(--ink);font-weight:600}
.brief ol{margin:6px 0 0;padding-left:20px;white-space:normal}
.cv{margin-top:12px;border-left:3px solid #F59E0B;background:#FFFBEB;border-radius:0 8px 8px 0;padding:10px 14px;font-size:13px;line-height:19px}
.cv b{color:var(--ink);font-weight:600}
.takes{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}@media(max-width:900px){.takes{grid-template-columns:1fr}}
.arm{border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}.arm-h{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line);font-size:14px}
.arm.a .arm-h{background:rgba(66,133,244,.07)}.arm.b .arm-h{background:rgba(184,92,56,.07)}.arm-h .fn{font-weight:600}.arm-h .sm{font-size:12.5px;color:var(--mt)}.arm-h .sm b{color:var(--ink)}
.take{padding:12px 14px;border-bottom:1px solid var(--line)}.take:last-child{border-bottom:0}.take-h{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.take audio{width:100%;height:36px;margin-top:8px}.facts{display:flex;flex-wrap:wrap;gap:2px 14px;margin-top:6px;font-size:12px;line-height:18px;color:var(--mt)}.facts b{color:var(--ink);font-weight:500;font-variant-numeric:tabular-nums}
.failbox{margin-top:8px;font-size:12.5px;line-height:18px;color:#9F1239;background:#FFF1F2;border-radius:6px;padding:6px 10px}
.tcrits{margin-top:10px;display:grid;gap:4px}.tcrit{display:grid;grid-template-columns:120px 1fr 34px;gap:8px;align-items:center;font-size:12px;color:var(--mt)}.tcrit .tb{height:6px;border-radius:4px;background:var(--muted);overflow:hidden}.tcrit .tb i{display:block;height:100%;border-radius:4px}.tcrit span:last-child{text-align:right;color:var(--ink)}
.sindic{font-family:"IBM Plex Sans","IBM Plex Sans Devanagari","Noto Sans Telugu",sans-serif}
.pf{font-size:12px;margin-left:3px}.pf.ok{color:#15803D}.pf.no{color:#B91C1C}
.figs{display:grid;gap:14px;margin-top:16px;grid-template-columns:1fr}
@media(min-width:640px){.figs{grid-template-columns:1fr 1fr}}@media(min-width:1024px){.figs.three{grid-template-columns:repeat(3,1fr)}}
figure{margin:0;min-width:0}
.figbox{position:relative;height:260px;border-radius:8px;background:var(--muted);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;overflow:hidden}
.figbox.src{border-style:dashed;border-color:var(--a)}
.figbox>img{width:100%;height:100%;object-fit:contain;cursor:zoom-in;display:block}
.figbox .score{position:absolute;top:10px;right:10px;background:rgba(255,255,255,.92);box-shadow:inset 0 0 0 1px var(--line);color:var(--ink)}
.figbox .score.win{background:var(--ink);color:#fff;box-shadow:none}
.figbox .empty{font-size:12px;color:var(--mt);padding:0 20px;text-align:center}
.extras{position:absolute;left:8px;bottom:8px;display:flex;gap:6px}
.extras button{width:44px;height:44px;border-radius:6px;overflow:hidden;border:2px solid #fff;box-shadow:0 1px 2px rgba(0,0,0,.08);background:#fff;padding:0;cursor:zoom-in}
.extras img{width:100%;height:100%;object-fit:cover;display:block}
.fgrid{display:grid;gap:4px;width:100%;height:100%;padding:6px}
.figbox:has(.score) .fgrid{padding-top:36px}
.fgrid button{position:relative;border:0;padding:0;background:#fff;border-radius:4px;overflow:hidden;cursor:zoom-in;min-height:0}
.fgrid img{width:100%;height:100%;object-fit:contain;display:block;background:var(--muted)}
.fgrid .fk{position:absolute;left:4px;top:4px;font:500 10px/1 var(--fm);background:rgba(10,10,10,.72);color:#fff;border-radius:999px;padding:3px 6px}
figcaption{margin-top:6px;display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:4px 8px}
figcaption .fn{font:500 11.5px/16px var(--fm);color:var(--ink)}figcaption .fn.src{color:var(--a-ink)}
figcaption a{font-size:11.5px}
.diags{display:grid;grid-template-columns:1fr;gap:14px;margin-top:16px}@media(min-width:1024px){.diags{grid-template-columns:1fr 1fr}}
.diag{border:1px solid var(--line);border-radius:8px;padding:14px;background:#fff;min-width:0}
.diag .dn{font:400 11.5px/16px var(--fm);color:var(--mt);margin-bottom:8px}
.diag .pills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.crits{display:flex;flex-direction:column;gap:6px}
.crit{display:grid;grid-template-columns:140px 1fr 46px;gap:8px;align-items:center;font-size:11.5px;line-height:16px}
.crit .cn{font-weight:500;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.crit .bar{height:6px;border-radius:4px;background:var(--muted);overflow:hidden}.crit .bar i{display:block;height:100%;border-radius:4px;background:var(--ink)}
.crit .cv2{text-align:right;font-variant-numeric:tabular-nums}
.why{margin-top:10px;padding-top:8px;border-top:1px dashed var(--line)}
.why>summary{list-style:none;cursor:pointer;font-size:12px;line-height:16px;font-weight:500;color:var(--b600)}.why>summary::-webkit-details-marker{display:none}
.why .wb{margin-top:8px;display:flex;flex-direction:column;gap:8px;font-size:12px;line-height:18px;color:var(--body)}
.why .wb b{color:var(--ink);font-weight:600}.why .wb .src{font:500 10px var(--fm);text-transform:uppercase;letter-spacing:.06em;color:var(--mt)}
.cl{margin-top:16px}
.cl .tbl{font-size:13px;line-height:18px}
.cl .tbl tbody td{padding:7px 12px;vertical-align:top}
.cl .q{color:var(--ink)}.cl .meta{font-size:11px;line-height:15px;color:var(--mt);white-space:nowrap}
.cl tr.split td{background:rgba(255,251,235,.55)}
.yn{display:inline-flex;align-items:center;justify-content:center;min-width:44px}
.cltbl th.ans{width:132px;white-space:normal}
@media(max-width:639px){.cltbl th:first-child,.cltbl td:first-child{display:none}.cltbl th,.cltbl td{padding-left:8px!important;padding-right:8px!important}
.cltbl th.ans{width:74px;white-space:normal;letter-spacing:.04em}.cltbl .yn{min-width:0}.cl .meta{white-space:normal}}
.cl .evd{display:none;margin-top:4px;font-size:11.5px;line-height:16px;color:var(--mt)}.clt.ev .evd{display:block}
.cl .evd b{font-weight:500;color:var(--body)}
.cl-tools{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.clt.folded tr.extra{display:none}.cl-more{padding:8px 12px;border-top:1px solid var(--line)}

/* method */
.method{margin-top:12px;padding:8px 24px}
.mitem{display:grid;grid-template-columns:1fr;gap:4px 24px;padding:14px 0;border-bottom:1px solid rgba(229,229,229,.7)}
.mitem:last-child{border-bottom:0}
@media(min-width:768px){.mitem{grid-template-columns:220px 1fr}}
.mitem .label{padding-top:3px}
.mitem p{font-size:14px;line-height:21px;color:var(--body);max-width:72ch}.mitem p+p{margin-top:6px}
.mitem b{color:var(--ink);font-weight:600}

/* lightbox */
.lb{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;background:#0A0A0A;color:rgba(255,255,255,.9)}
.lb-top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 24px}
.lb-top .cnt{font-size:14px;font-weight:500}
.lb-top a{color:rgba(255,255,255,.85);font:500 12px var(--fm);margin-left:auto}
.lb-x{width:36px;height:36px;border-radius:50%;border:0;background:rgba(255,255,255,.1);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer}
.lb-x:hover{background:rgba(255,255,255,.2)}
.lb-mid{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:0 16px 16px}
@media(min-width:640px){.lb-mid{padding:0 48px 16px}}
.lb figure{max-width:72rem;width:100%}
.lb img{display:block;margin:0 auto;max-width:100%;max-height:70vh;border-radius:.875rem;background:#fff}
.lb figcaption{justify-content:center;margin-top:12px;font-size:14px;font-weight:500;color:rgba(255,255,255,.9)}
.lb-chips{flex-shrink:0;padding:0 16px 24px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap;max-height:30vh;overflow:auto}
.lbchip{font:400 12px var(--fm);border-radius:999px;padding:6px 12px;border:1px solid rgba(255,255,255,.3);color:rgba(255,255,255,.85);background:none;cursor:pointer}
.lbchip:hover{border-color:rgba(255,255,255,.7)}.lbchip.on{background:#fff;color:var(--ink);border-color:#fff}

/* footer */
.tl-foot{background:var(--dark);color:var(--dark-ink);padding:70px 0 34px;border-top:1px solid rgba(255,255,255,.09)}
.foot-top{display:flex;flex-wrap:wrap;justify-content:space-between;gap:34px}
.foot-brand{max-width:26rem}.foot-brand>a{display:inline-block;margin-bottom:16px}.foot-logo{height:52px}
.foot-brand p{font:400 13.5px/1.6 var(--fs);color:#8A96A6;max-width:40ch}
.foot-meta{font:400 13.5px/1.6 var(--fs);color:#8A96A6;max-width:32rem}
.foot-meta .label{display:block;font:500 10.5px var(--fm);letter-spacing:.16em;color:#5E6A7A;margin-bottom:16px}
.foot-meta a{color:#8A96A6;transition:color .2s}.foot-meta a:hover{color:#fff;text-decoration:none}
.foot-bot{margin-top:48px;padding-top:22px;border-top:1px solid rgba(255,255,255,.09);font:400 11.5px var(--fm);color:#5E6A7A}
.wmt{min-width:1180px}.wmt tbody td:first-child{min-width:190px}
.wmt thead th.sub{text-transform:none;letter-spacing:0;font-size:11px;line-height:14px;white-space:normal;min-width:82px;vertical-align:bottom}
.wmt td.sorted{background:rgba(66,133,244,.05)}.wmt thead th.sorted{background:#EEF3FD}
.nowrap{white-space:nowrap}
.mlist{margin:6px 0 0;padding-left:18px;font-size:14px;line-height:21px;color:var(--body);display:flex;flex-direction:column;gap:2px;max-width:72ch}
.mlist b{font-weight:500}
.err{margin:0 0 10px;font-size:12px;line-height:18px;color:var(--body);display:flex;flex-wrap:wrap;gap:6px;align-items:baseline}
.cv .still{margin:6px 0 0;padding-left:18px}
.dgroup{margin-top:28px}.dgl{margin-bottom:4px}
.dt{margin-top:12px}.dt-h{margin:0 0 6px}.dt-t{font:600 12.5px/16px var(--fs);color:var(--ink)}
.dt-s{margin-top:4px;font:400 11px/15px var(--fm);color:var(--mt);overflow-wrap:anywhere}
.dtbl{font-size:12.5px;line-height:18px}.dtbl tbody td{padding:6px 10px;vertical-align:top}.dtbl thead th{padding:6px 10px;white-space:nowrap}
.dtbl td{max-width:560px;overflow-wrap:anywhere}.dtbl td.r{white-space:nowrap}
.tl-foot .foot-legal{max-width:72ch;margin:14px auto 0;padding-top:14px;border-top:1px solid rgba(255,255,255,.05);text-align:left;font-size:11px;line-height:1.55;color:#4E5763}
"""

JS = r"""
(function(){
'use strict';
var D = JSON.parse(document.getElementById('report-data').textContent);
var IMG = null;
function imgSrc(id){
  if(!id) return '';
  if(!IMG){ try{ IMG = JSON.parse(document.getElementById('img-data').textContent); }catch(e){ IMG = {}; } }
  return IMG[id] ? 'data:image/jpeg;base64,' + IMG[id] : '';
}
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
var CHEV = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
var LI = function(d){ return '<svg class="li" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>'; };
var LICON = { fields: LI('<path d="M4 6h16M4 12h10M4 18h6"/>'), view: LI('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'), sort: LI('<path d="M3 6h12M3 12h8M3 18h4M17 8l3-3 3 3M20 5v14"/>'), order: LI('<path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3"/>'), won: LI('<path d="M8 21h8M12 17v4M5 4h14v4a7 7 0 0 1-14 0V4zM5 6H3v2a3 3 0 0 0 3 3M19 6h2v2a3 3 0 0 1-3 3"/>'), score: LI('<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>'), speed: LI('<path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/>'), scen: LI('<path d="M4 6h16M4 12h16M4 18h16"/>'), pass: LI('<path d="M20 6L9 17l-5-5"/>'), rel: LI('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/>') };
var GROUP_ICON = { 'Win %': 'won', 'Pass criteria met': 'pass', 'Quality mean': 'score', 'Reliability': 'rel', 'Latency p50': 'speed', 'Rows': 'scen', 'Who won': 'won', 'Score': 'score', 'Speed': 'speed', 'Scenario': 'scen' };
// a sort menu in the page's own dropdown style: grouped, with icons and a radio on the current choice
function sortMenu(act, val, groups, open, label){
  var cur = '';
  groups.forEach(function(g){ g[1].forEach(function(o){ if(o[0] === val) cur = o[1]; }); });
  return '<div class="dd"><span class="ctrl-lbl">' + LICON.sort + esc(label || 'Sort by') + '</span>' +
    '<button type="button" class="dd-btn" data-act="' + act + '-open" aria-haspopup="listbox" aria-expanded="' + open + '"><span>' + esc(cur) + '</span>' + CHEV + '</button>' +
    (open ? '<div class="dd-menu sortm" role="listbox" aria-label="' + esc(label || 'Sort by') + '">' + groups.map(function(g){
      return '<div class="dd-h">' + (LICON[GROUP_ICON[g[0]]] || '') + esc(g[0]) + '</div>' + g[1].map(function(o){ var on = o[0] === val;
        return '<button type="button" class="dd-opt" role="option" aria-selected="' + on + '" data-act="' + (act === 'ev-sort' ? 'ev-sort-pick2' : act + '-pick') + '" data-v="' + o[0] + '"><span class="rb' + (on ? ' on' : '') + '" aria-hidden="true"></span><span class="dd-t"><b>' + esc(o[1]) + '</b></span></button>'; }).join('');
    }).join('') + '</div>' : '') + '</div>';
}
var A_FILL = '#4285F4', B_FILL = '#B85C38', TIE = '#CDD1D7', A_INK = '#1A56B8', B_INK = '#8F4227';

var st = {
  media: D.defaultMedia,
  wm: { dim: 'industry', sort: 'win:a', dir: 'desc', view: 'map', fields: {}, more: {} },
  ev: { f: {}, result: 'all', confident: false, sort: 'ga', more: false, optq: {}, q: '' },
  step: 'media', sheet: false, shut: {}, sheetMore: {},
  open: {}, lb: null, menu: false
};
var INTERNAL = D.audience === 'internal';
// embedded in a host page (the study site): no header or footer of our own, no bank step; ?media= names the lane
var EMBED = !!D.embed;
if(EMBED) document.documentElement.classList.add('embed');
var STEPS = [['media', 'Choose a scenario bank'], ['narrow', 'Narrow it down'], ['scenarios', 'Scenarios']].filter(function(x){ return !EMBED || x[0] !== 'media'; });
var STEP_IDS = (INTERNAL ? ['media', 'narrow', 'scenarios', 'method'] : ['media', 'narrow', 'scenarios']).filter(function(x){ return !EMBED || x !== 'media'; });
(function(){ var mm = /[?&]media=([a-z]+)/.exec(location.search); if(mm && D.media[mm[1]]) st.media = mm[1]; if(EMBED) st.step = 'narrow'; })();
// URL hooks for screenshots and deep links: ?open=all|ID1,ID2 &wm=table &dim=usecase &sort=lat:a &dir=asc &res=none &f=industry:Gaming &only=details
(function(){
  var q = function(k){ var m = new RegExp('[?&]' + k + '=([^&#]+)').exec(location.search); return m ? decodeURIComponent(m[1]) : null; };
  var o = q('open'); if(o){ o.split(',').forEach(function(id){ st.open[id] = true; }); }
  if(q('wm')) st.wm.view = q('wm');
  if(q('dim')) st.wm.dim = q('dim');
  if(q('sort')){ st.wm.sort = q('sort'); st.wm.dir = defaultDir(st.wm.sort); }
  if(q('dir')) st.wm.dir = q('dir');
  if(q('res')) st.ev.result = q('res');
  if(q('only')){ var o = q('only'); st.step = { compare: 'media', winmap: 'narrow', evidence: 'scenarios', method: 'method', details: 'method' }[o] || st.step; if(o === 'details') st.only = 'details'; }
  var hs = location.hash.slice(1); if(STEP_IDS.indexOf(hs) >= 0) st.step = hs;
  if(q('sheet')) st.sheet = true;
  if(q('fields')) st.wm.fieldsOpen = true;
  if(q('q')) st.ev.q = q('q');
  var f = q('f'); if(f){ f.split(';').forEach(function(x){ var i = x.indexOf(':'); if(i > 0){ var k = x.slice(0, i); (st.ev.f[k] = st.ev.f[k] || []).push(x.slice(i + 1)); } }); }
  if(q('picks')) st.wm.picksOpen = true;
})();

function M(){ return D.media[st.media]; }
function arms(){ return M().arms; }
function all(){ return M().scenarios; }
function cur(){ return all(); }

// ---------- numbers ----------
function median(v){ if(!v.length) return null; var x = v.slice().sort(function(a,b){return a-b;}); var n = x.length, h = n >> 1; return n % 2 ? x[h] : (x[h-1] + x[h]) / 2; }
function tally(list){
  var r = { n: list.length, cmp: 0, a: 0, b: 0, tie: 0, none: 0, sa: 0, sb: 0, minA: null, minB: null,
            passN: 0, passA: 0, passB: 0, latA: [], latB: [], costA: 0, costB: 0, imgA: 0, imgB: 0, conf: 0, est: false,
            qsA: 0, qnA: 0, qsB: 0, qnB: 0, vq: false, clips: false, ttA: [], ttB: [] };
  list.forEach(function(s){
    if(!s.win){ r.none++; return; }
    r.cmp++; r[s.win]++;
    var xa = s.a.score * 10, xb = s.b.score * 10;      // unrounded; .pct is for display only
    r.sa += xa; r.sb += xb;
    r.minA = r.minA == null ? xa : Math.min(r.minA, xa);
    r.minB = r.minB == null ? xb : Math.min(r.minB, xb);
    if(s.a.gn != null && s.b.gn != null){ r.clips = true; r.passN += s.a.gn; r.passA += s.a.gp; r.passB += s.b.gp; }   // voice: per clip
    else if(s.a.pass != null && s.b.pass != null){ r.passN++; r.passA += s.a.pass ? 1 : 0; r.passB += s.b.pass ? 1 : 0; }
    if(s.a.qn != null){ r.vq = true; r.qsA += s.a.qs; r.qnA += s.a.qn; r.qsB += s.b.qs || 0; r.qnB += s.b.qn || 0; }
    (s.a.ttfa || []).forEach(function(x){ r.ttA.push(x); }); (s.b.ttfa || []).forEach(function(x){ r.ttB.push(x); });
    (s.a.calls || []).forEach(function(c){ r.latA.push(c[0]); r.costA += c[1]; r.imgA++; });
    (s.b.calls || []).forEach(function(c){ r.latB.push(c[0]); r.costB += c[1]; r.imgB++; });
    if(s.a.costEst || s.b.costEst) r.est = true;
    if(s.win === 'a' && s.margin >= D.confidentPP) r.conf++;
  });
  r.meanA = r.cmp ? r.sa / r.cmp : null; r.meanB = r.cmp ? r.sb / r.cmp : null;
  if(r.vq){ r.meanA = r.qnA ? r.qsA / r.qnA * 10 : null; r.meanB = r.qnB ? r.qsB / r.qnB * 10 : null; }   // voice: clips that passed
  r.f50A = median(r.ttA); r.f50B = median(r.ttB);
  r.passRateA = r.passN ? 100 * r.passA / r.passN : null; r.passRateB = r.passN ? 100 * r.passB / r.passN : null;
  r.cpiA = r.imgA ? r.costA / 1e6 / r.imgA : null; r.cpiB = r.imgB ? r.costB / 1e6 / r.imgB : null;
  r.p50A = median(r.latA); r.p50B = median(r.latB);
  return r;
}
// wins / scenarios in the row both models completed, half to even (the study page's rule)
function winPctNum(w, n){ if(!n) return null; var x = w / n * 100, f = Math.floor(x); return Math.abs(x - f - 0.5) < 1e-9 ? (f % 2 === 0 ? f : f + 1) : Math.round(x); }
function winPct(w, n){ var v = winPctNum(w, n); return v == null ? '—' : v + '%'; }
// one decimal, half to even, after clearing float noise (94.85000000000001 is a tie, shown 94.8)
function r1(x){ var y = x * 10, f = Math.floor(y); return (Math.abs(y - f - 0.5) < 1e-6 ? (f % 2 === 0 ? f : f + 1) : Math.round(y)) / 10; }
function p1(x){ return x == null ? '—' : r1(x).toFixed(1) + '%'; }
function money(x){ return x == null ? '—' : '$' + x.toFixed(4); }
function secs(ms){ return ms == null ? '—' : (ms / 1000).toFixed(1) + 's'; }
function plural(n, one, many){ return n + ' ' + (n === 1 ? one : (many || one + 's')); }

// ---------- header strip ----------
var ICONS = {
  compare: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>',
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6 9 4z"/><path d="M9 4v14M15 6v14"/></svg>',
  list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M7.5 9h9M7.5 13h9M7.5 17h5"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5v-13zM20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5v-13z"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>'
};
var TABS = [['compare', 'Compare Models', ICONS.compare], ['winmap', 'Win Map', ICONS.map], ['evidence', 'Per-scenario evidence', ICONS.list], ['method', 'Implementation Approach', ICONS.book]];
if(INTERNAL) TABS.push(['details', 'Run details', ICONS.clock]);
// the study site's MediaGlyph drawings (40x22), one per media type
function glyphSvg(key){
  var r = function(x, y, w, h, rx, c, op){ return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="' + rx + '" fill="' + c + '" opacity="' + op + '"/>'; }, out = '';
  if(key === 'voice'){ [8, 14, 20, 12, 18, 16, 20, 10, 16, 8].forEach(function(h, i){ out += r(1 + i * 4, 11 - h / 2, 2, h, 1, i < 5 ? A_FILL : B_FILL, .9); }); }
  else if(key === 'video'){ [A_FILL, B_FILL, A_FILL, B_FILL, A_FILL].forEach(function(c, i){ out += r(i * 8.3, 3, 6.8, 16, 1.5, c, i === 3 ? .3 : .85); }); }
  else { for(var i = 0; i < 8; i++) out += r(1 + (i % 4) * 10, i < 4 ? 2 : 12, 8, 8, 1.5, i % 2 === 0 ? A_FILL : B_FILL, .85); }
  return '<svg viewBox="0 0 40 22" aria-hidden="true" focusable="false">' + out + '</svg>';
}
function glyph(key, off){ return '<span class="glyph' + (off ? ' off' : '') + '" aria-hidden="true">' + glyphSvg(key) + '</span>'; }
var CHECK = '<svg class="pm-ok" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 10.5l3.5 3.5 7.5-8"/></svg>';
function renderStrip(){
  applyMedia();
  var m = M(), home = st.step === 'media';   // no bank picked yet: the study, not a lane
  document.title = home ? D.study.name : m.title + ' · ' + D.study.name;
  document.getElementById('strip').innerHTML =
    '<div class="tl-wrap strip-in"><div class="strip-row"><div' + (home && !INTERNAL ? ' class="home"' : '') + ' style="min-width:0;flex:1">' +
    '<h1>' + esc(home ? D.study.name : m.title) + '</h1>' +
    (home && !INTERNAL ? '' : '<div class="crumbs">' + (home ? '' : '<a href="' + esc(D.study.href) + '">' + esc(D.study.name) + '</a><span class="sep">/</span><span class="cur">' + esc(m.label) + '</span>') + (INTERNAL ? '<span class="pill pill-warning" style="margin-left:6px">Internal</span>' : '') + '</div>') + '</div>' +
    '</div>' +
    '<nav class="tabs steps" aria-label="Steps">' + STEPS.map(function(t, i){
      var on = st.step === t[0], done = STEP_IDS.indexOf(st.step) > i && st.step !== 'method';
      return '<button class="tab step" data-act="step" data-v="' + t[0] + '"' + (on ? ' aria-current="step"' : '') + '><span class="sn' + (on ? ' on' : done ? ' done' : '') + '">' + (i + 1) + '</span>' + esc(t[1]) + '</button>';
    }).join('') +
    (INTERNAL ? '<span class="step-gap"></span><button class="tab step" data-act="step" data-v="method"' + (st.step === 'method' ? ' aria-current="step"' : '') + '>' + ICONS.book + 'Run details</button>' : '') + '</nav></div>';
  setChrome();
}
function setChrome(){ document.documentElement.style.setProperty('--chrome', (document.getElementById('strip').offsetHeight + (EMBED ? 0 : 64)) + 'px'); }
// the strip folds once the page scrolls, as on the study site: breadcrumbs fade,
// the picker label goes, and under 1024px the title row goes too
var compact = false;
function onScroll(){
  var y = window.scrollY, c = y > 120 ? true : y < 8 ? false : compact;
  if(c !== compact){ compact = c; document.getElementById('strip').classList.toggle('compact', c); setChrome(); }
}
var activeTab = 'compare';
function markTab(id){
  activeTab = id;
  document.querySelectorAll('.tab').forEach(function(t){ if(t.getAttribute('data-tab') === id) t.setAttribute('aria-current', 'true'); else t.removeAttribute('aria-current'); });
}

// ---------- compare: head-to-head ----------
function leadVal(v, side, lead){ return lead ? '<span class="lead ' + side + '">' + esc(v) + '</span>' : '<span class="trailv">' + esc(v) + '</span>'; }
function measure(label, gloss, av, bv, fmt, higher){
  if(av == null && bv == null) return '';
  var lead = (av == null || bv == null || fmt(av) === fmt(bv)) ? null : (higher ? (av > bv ? 'a' : 'b') : (av < bv ? 'a' : 'b'));
  var max = Math.max(av || 0, bv || 0) || 1;
  var bar = function(v, side){ var w = v == null ? 0 : Math.max(0, Math.min(100, v / max * 100)); return '<span class="track"><span class="fill ' + side + (lead === side ? '' : ' trail') + '" style="width:' + w.toFixed(1) + '%"></span></span>'; };
  return '<div class="mrow"><div class="mh"><span class="label">' + esc(label) + '</span><span class="gloss">' + esc(gloss) + '</span></div>' +
    '<div class="mgrid"><span>' + leadVal(fmt(av), 'a', lead === 'a') + '</span>' + bar(av, 'a') + bar(bv, 'b') + '<span>' + leadVal(fmt(bv), 'b', lead === 'b') + '</span></div></div>';
}
function tallyLegend(t){
  var ar = arms();
  return [[t.a, A_FILL, ar.a.short + ' won ' + t.a], [t.tie, TIE, 'Tied ' + t.tie], [t.b, B_FILL, ar.b.short + ' won ' + t.b]].filter(function(x){ return x[0] > 0; });
}
function tallyBar(t){
  var tot = (t.a + t.tie + t.b) || 1, leg = tallyLegend(t);
  var seg = function(n, c){ return n > 0 ? '<span style="width:' + (n / tot * 100) + '%;background:' + c + (c === TIE ? ';color:var(--ink)' : '') + '">' + n + '</span>' : ''; };
  return '<div class="tallybar" role="img" aria-label="' + esc(leg.map(function(x){ return x[2]; }).join(', ')) + '">' + seg(t.a, A_FILL) + seg(t.tie, TIE) + seg(t.b, B_FILL) + '</div>' +
    '<div class="legend">' + leg.map(function(x){ return '<span><span class="sw" style="background:' + x[1] + '"></span>' + esc(x[2]) + '</span>'; }).join('') + '</div>';
}
function runDate(){ return D.runDates.length ? fmtDate(D.runDates[D.runDates.length - 1]) : ''; }
function pricesTxt(){
  var m = M(), p = m.pricesAsOf || {}, ar = m.arms;
  if(!p.a && !p.b) return '';
  if(p.a === p.b) return ' (as of ' + p.a + ')';
  return ' (as of ' + [p.a ? p.a + ' for ' + ar.a.short : '', p.b ? p.b + ' for ' + ar.b.short : ''].filter(Boolean).join(', ') + ')';
}
function noScoreTxt(list){
  var ar = arms(), by = {}, order = [];
  list.forEach(function(s){
    ['a', 'b'].forEach(function(k){
      if(s[k].pct != null) return;
      var key = ar[k].short + ' · ' + (s[k].err ? s[k].err.kind : (s[k].st || 'no output'));
      if(!by[key]){ by[key] = []; order.push(key); }
      by[key].push(s.id);
    });
  });
  return order.map(function(k){ return esc(by[k].join(', ')) + ' (' + esc(k) + ')'; }).join('; ');
}
// Step 1 copies the sales view's "Choose a scenario bank" (studies.adlc.tilicho.in …/sales-view/scenario-bank/v2):
// the dark band, a card per bank in the bank's own words. A bank this report has no results for shows, faded.
var BANKS = [
  ['image', 'Image', 'Make a new picture from a brief, or change one that exists.', '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-9 9"/>'],
  ['video', 'Video', 'Change footage that already exists: swap, remove, relight, retime.', '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3"/>'],
  ['voice', 'Voice (TTS)', 'Read a script aloud in Indian languages mixed with English.', '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>'],
  ['live', 'Live calls', 'Hold a whole customer call with a live voice agent.', '<path d="M5 4h3.5l1.5 4.5-2.2 1.4a11 11 0 0 0 6.3 6.3l1.4-2.2L20 15.5V19a1.5 1.5 0 0 1-1.6 1.5A16.5 16.5 0 0 1 3.5 5.6 1.5 1.5 0 0 1 5 4z"/><path d="M15 3.5a5.5 5.5 0 0 1 5.5 5.5M15 7a2 2 0 0 1 2 2"/>']
];
function renderMediaStep(){
  // In the report's own light style (the sales view's step 1 was the reference, not a copy): one card per
  // scenario bank this report has results for.
  var cards = BANKS.filter(function(b){ return D.media[b[0]]; }).map(function(b){
    var x = D.media[b[0]], S = x.scenarios, ind = {}, mix = {}, task = {}, dd = (x.dims || []).filter(function(d){ return d.key === 'difficulty'; })[0];
    S.forEach(function(sc){ ind[sc.v.industry] = 1; mix[sc.v.difficulty] = (mix[sc.v.difficulty] || 0) + 1; task[sc.v.task] = (task[sc.v.task] || 0) + 1; });
    var order = dd ? dd.order : Object.keys(mix);
    var bar = order.map(function(v, i){ return mix[v] ? '<i style="flex:' + mix[v] + ';background:' + TIER[Math.min(i, 3)] + '"></i>' : ''; }).join('');
    var leg = order.map(function(v, i){ return mix[v] ? '<span><span class="sw" style="background:' + TIER[Math.min(i, 3)] + '"></span>' + esc(v) + ' ' + mix[v] + '</span>' : ''; }).join('');
    return '<button class="mb' + (b[0] === st.media ? ' on' : '') + '" data-act="pick-media" data-v="' + b[0] + '">' +
      '<span class="mb-top"><span class="oi lg" style="background:#E00917' + '1a;color:#C50714"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + b[3] + '</svg></span>' +
      '<span><span class="mb-n">' + esc(b[1]) + '</span><span class="mb-l">' + esc(b[2]) + '</span></span></span>' +
      '<span class="mb-c"><b class="tnum">' + S.length + '</b> scenarios · ' + plural(Object.keys(ind).length, 'industry', 'industries') + '</span>' +
      '<span class="mb-k">' + Object.keys(task).map(function(k){ return esc(k) + ' ' + task[k]; }).join(' · ') + '</span>' +
      '<span class="mb-arms"><span style="color:' + A_INK + '">' + esc(x.arms.a.name) + '</span> vs <span style="color:' + B_INK + '">' + esc(x.arms.b.name) + '</span></span>' +
      '<span class="mb-mix">' + bar + '</span><span class="mb-leg">' + leg + '</span>' +
      '<span class="mb-go">Choose →</span></button>';
  }).join('');
  return '<div class="block" id="media" style="margin-top:0"><div class="bhead"><div><div class="text-h4">Choose a scenario bank</div>' +
    '</div></div>' +
    '<div class="mbgrid">' + cards + '</div></div>';
}
function renderCompare(){
  var m = M(), ar = m.arms, list = cur(), t = tally(list), allN = all().length, V = m.kind === 'voice';
  var h = '<div id="compare"><div class="lanehead"><div class="eyebrow">' + esc(m.label) + '</div>' +
    '<p class="sub">' + plural(allN, 'scenario') + (V ? ' · ' + plural(m.rounds, 'round') + ' each' : ' · one matched setting') + '</p></div>';
  h += '<div class="card h2h"><div class="h2h-title"><div>Head-to-head results</div><div class="label" style="margin-top:2px">' + plural(list.length, 'scenario') + '</div></div>' +
    '<div class="arms"><span style="color:' + A_INK + '">◆ ' + esc(ar.a.name) + '</span><span class="label">vs</span><span class="ra" style="color:' + B_INK + '">' + esc(ar.b.name) + ' ◆</span></div>' +
    (V ? measure('Quality', 'mean rubric rating on clips that passed · higher is better', t.meanA, t.meanB, p1, true) +
      measure('Gates passed', plural(t.passN, 'clip') + ' · share that passed every automatic check · higher is better', t.passRateA, t.passRateB, p1, true) +
      measure('Reliability', 'lowest two-round mean on any single scenario · higher is better', t.minA, t.minB, p1, true) +
      measure('Cost per clip', 'lower is better', t.cpiA, t.cpiB, money, false) +
      measure('First audio p50', 'median, streamed · lower is better', t.f50A, t.f50B, secs, false) +
      measure('Latency p50', 'whole call, median · lower is better', t.p50A, t.p50B, secs, false) +
      '<div class="tally"><div class="th">W–T–L<small>won, tied, lost across ' + t.cmp + ' scenarios · a win needs a lead of more than ' + (m.tieBandPP || 0).toFixed(1) + ' pp</small></div>' + tallyBar(t) + '</div></div>' :
    measure('Quality', 'mean rating · higher is better', t.meanA, t.meanB, p1, true) +
    (t.passN ? measure('Pass criteria met', plural(t.passN, 'scenario') + ' with pass criteria · higher is better', t.passRateA, t.passRateB, p1, true) : '') +
    measure('Reliability', 'lowest rating on any single scenario · higher is better', t.minA, t.minB, p1, true) +
    measure('Cost per ' + m.unit, (t.est ? 'partly estimated · ' : '') + 'lower is better', t.cpiA, t.cpiB, money, false) +
    measure('Latency p50', 'median · lower is better', t.p50A, t.p50B, secs, false) +
    '<div class="tally"><div class="th">W–T–L<small>won, tied, lost across ' + t.cmp + ' decided scenarios</small></div>' + tallyBar(t) + '</div></div>');
  var invalid = list.some(function(s){ return s.a.st === 'invalid' || s.b.st === 'invalid'; });
  var none = list.filter(function(s){ return !s.win; });
  h += V ? voiceNotes(m) : '<ul class="notes">' +
    '<li><b>Quality</b> — the mean rubric rating, as a percentage. Higher is better.</li>' +
    (t.passN ? '<li><b>Pass criteria</b> — per scenario, from the scenario bank' + (m.passExample ? ', e.g. “' + esc(m.passExample) + '”' : '') + '; ' + t.passN + ' of the ' + t.cmp + ' decided scenarios have them.</li>' : '') +
    '<li><b>Reliability</b> — the model’s lowest rating on any single scenario. Higher is better.</li>' +
    '<li><b>Cost per ' + esc(m.unit) + '</b> — lower is better. Priced from the usage each provider returned, at its published price' + esc(pricesTxt()) + '.</li>' +
    '<li><b>Latency p50</b> — the median. Lower is better. Per ' + esc(m.unit) + ': the time of the request that returned it' + (INTERNAL ? '; waits after a rate-limited attempt are not included (Run details › Latency)' : '') + '.</li>' +
    (invalid ? '<li>A 0.0% comes from an image that failed an automated check, not from a judged rating.</li>' : '') +
    '</ul></div>';
  return h;
}
// the voice study's own definitions, as its dashboard words them
function voiceNotes(m){
  var p = m.pricesAsOf || {};
  return '<ul class="notes">' +
    '<li><b>Quality</b> — the mean rubric rating, as a percentage, over the clips that passed their checks. Higher is better.</li>' +
    '<li><b>Gates passed</b> — the share of clips that passed every automatic check: length, silence, loudness, the word error rate on English, and required or forbidden words such as “lakh”. A clip that fails scores 0.</li>' +
    '<li><b>Reliability</b> — the model’s lowest two-round mean on any single scenario. Higher is better.</li>' +
    '<li><b>Cost</b> — priced from the tokens Gemini reported and the characters sent to ElevenLabs, at list price' + (p.a || p.b ? ' (Gemini as of ' + esc(p.a || '—') + ', ElevenLabs as of ' + esc(p.b || '—') + ')' : '') + '.</li>' +
    '<li><b>W–T–L</b> — a scenario’s score for a model is the mean of its two rounds, and a model wins it by leading by more than ' + (m.tieBandPP || 0).toFixed(1) + ' pp.</li>' +
    '</ul></div>';
}
function fmtDate(iso){ var d = new Date(iso + 'T00:00:00Z'); return isNaN(d) ? iso : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }); }


// ---------- option icons: the scenario bank's (industryIcons.tsx, filterIcons.tsx), matched on the words ----------
var GL = {
  sparkle: { d: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z', 'M19 16v4M17 18h4'] },
  pencil: { d: ['M4 20l1-4L15.5 5.5l3 3L8 19z', 'M13.5 7.5l3 3'] },
  user: { d: ['M5 20a7 7 0 0114 0'], c: [[12, 8, 3.5]] },
  users: { d: ['M2.5 20a6.5 6.5 0 0113 0', 'M15 14.2a5 5 0 016.5 5.8'], c: [[9, 8, 3], [17, 8.5, 2.5]] },
  box: { d: ['M3 7.5l9-4.5 9 4.5v9L12 21l-9-4.5z', 'M3 7.5l9 4.5 9-4.5', 'M12 12v9'] },
  tag: { d: ['M3 12V4a1 1 0 011-1h8l9 9-9 9z'], c: [[7.5, 7.5, 1.3]] },
  card: { d: ['M3 6h18v12H3z', 'M3 10h18', 'M7 15h3'] },
  shield: { d: ['M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z', 'M8.5 12l2.5 2.5 4.5-5'] },
  calendar: { d: ['M4 6h16v14H4z', 'M4 10h16', 'M8 3v5M16 3v5'] },
  bell: { d: ['M6 16v-5a6 6 0 0112 0v5l1.5 2h-15z', 'M10 20.5a2 2 0 004 0'] },
  book: { d: ['M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2z', 'M4 19V5'] },
  image: { d: ['M4 5h16v14H4z', 'M4 16l4.5-4.5 4 4 2.5-2.5L20 18'], c: [[15.5, 9, 1.5]] },
  layers: { d: ['M12 3l9 4.5-9 4.5-9-4.5z', 'M3 12l9 4.5 9-4.5', 'M3 16.5L12 21l9-4.5'] },
  sun: { d: ['M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'], c: [[12, 12, 4]] },
  palette: { d: ['M12 3a9 9 0 100 18c1.2 0 1.6-1 1.2-2-.6-1.4.2-2.5 1.6-2.5H17a4 4 0 004-4c0-5-4-9.5-9-9.5z'], c: [[7.5, 11, 1], [10, 7, 1], [14.5, 7, 1]] },
  text: { d: ['M5 5h14', 'M12 5v14', 'M9 19h6'] },
  crop: { d: ['M6 2v16h16', 'M2 6h16v16'] },
  plan: { d: ['M4 4h16v16H4z', 'M4 12h8V4', 'M12 16h8'] },
  camera: { d: ['M4 8h3l2-3h6l2 3h3v11H4z'], c: [[12, 13, 3.5]] },
  film: { d: ['M4 4h16v16H4z', 'M8 4v16M16 4v16M4 8h4M4 16h4M16 8h4M16 16h4'] },
  headset: { d: ['M4 14v-2a8 8 0 0116 0v2', 'M4 14h3v5H4z', 'M17 14h3v5h-3z', 'M20 19a3 3 0 01-3 3h-3'] },
  landscape: { d: ['M3 20l6-9 4 6 3-4 5 7z'], c: [[16, 6.5, 2]] },
  grid: { d: ['M4 4h6.5v6.5H4zM13.5 4H20v6.5h-6.5zM4 13.5h6.5V20H4zM13.5 13.5H20V20h-6.5z'] },
  panels: { d: ['M3 5h18v14H3z', 'M9 5v14M15 5v14'] },
  single: { d: ['M5 4h14v16H5z'] }
};
// industries, first match wins (the bank's order, with publishing/media ahead of events for this report's names)
var IND = [
  [/\b(ads|advertising)\b/i, { d: ['M3 10v4h3l8 4.5V5.5L6 10H3z', 'M17.5 9a4 4 0 010 6', 'M19.8 6.5a7.5 7.5 0 010 11'] }],
  [/micro-drama|entertainment|film|ott|animation|streaming|production studios/i, { d: ['M3 10h18v10a1 1 0 01-1 1H4a1 1 0 01-1-1V10z', 'M3 10l1.2-4.6a1 1 0 011.2-.7l14.6 3.4-.6 1.9', 'M8.5 5.6l-1.3 4.4M13.5 6.8l-1.3 3.2'] }],
  [/banking|financial/i, { d: ['M3 9.5L12 4l9 5.5', 'M5 10v8M9.7 10v8M14.3 10v8M19 10v8', 'M3 20.5h18'] }],
  [/e-?commerce|retail|d2c|marketplace/i, { d: ['M5 8h14l-1.1 12.1a1 1 0 01-1 .9H7.1a1 1 0 01-1-.9L5 8z', 'M9 8V6.5a3 3 0 016 0V8'] }],
  [/gaming|game|esports/i, { d: ['M7 8h10a4 4 0 014 4v1.5a2.8 2.8 0 01-5 1.7L15 14H9l-1 1.2a2.8 2.8 0 01-5-1.7V12a4 4 0 014-4z', 'M8 10.5v3M6.5 12h3'], c: [[15.5, 11.2, 0.6], [17.4, 12.8, 0.6]] }],
  [/architecture|real estate|interior|property|building/i, { d: ['M4 21V6l8-3v18', 'M12 21V9l8 2.5V21', 'M2.5 21h19', 'M7 8h2M7 12h2M7 16h2M15 13.5h2M15 17h2'] }],
  [/fashion|beauty|sportswear/i, { d: ['M12 7.5a2 2 0 112-2c0 1.1-2 1.4-2 3', 'M12 8.5l8.6 6.2a1 1 0 01-.6 1.8H4a1 1 0 01-.6-1.8L12 8.5'] }],
  [/food|restaurant|beverage/i, { d: ['M4 9h13v3.5a6 6 0 01-6 6h-1a6 6 0 01-6-6V9z', 'M17 10.5h1.2a2.3 2.3 0 010 4.6H17', 'M8 3.5v2.5M12 3.5v2.5', 'M3 21h15'] }],
  [/travel|tourism|hospitality|hotel|mobility|delivery/i, { d: ['M21 3L3 10.4l6.8 2.8L12.6 20 21 3z', 'M9.8 13.2l4.7-4.7'] }],
  [/education|training/i, { d: ['M2 9l10-5 10 5-10 5L2 9z', 'M6 11v4.8c0 1.6 2.7 3.2 6 3.2s6-1.6 6-3.2V11', 'M22 9v5'] }],
  [/automotive/i, { d: ['M3 13h18v4.5a1 1 0 01-1 1H4a1 1 0 01-1-1V13z', 'M5 13l1.8-4.6a1.5 1.5 0 011.4-.9h7.6a1.5 1.5 0 011.4.9L19 13'], c: [[7.5, 18.5, 1.6], [16.5, 18.5, 1.6]] }],
  [/corporate/i, { d: ['M3.5 8h17a1 1 0 011 1v10a1 1 0 01-1 1h-17a1 1 0 01-1-1V9a1 1 0 011-1z', 'M9 8V5.5A1.5 1.5 0 0110.5 4h3A1.5 1.5 0 0115 5.5V8', 'M2.5 13.5h19'] }],
  [/news|media|publish|broadcast/i, { d: ['M4 5h12.5v14H6a2 2 0 01-2-2V5z', 'M16.5 9H20v8a2 2 0 01-2 2h-1.5', 'M7 9h6.5M7 12.5h6.5M7 16h4'] }],
  [/events|conference/i, { d: ['M4 5.5h16a1 1 0 011 1V20a1 1 0 01-1 1H4a1 1 0 01-1-1V6.5a1 1 0 011-1z', 'M3 10h18', 'M8 3v4M16 3v4'] }],
  [/cross-industry|probe|any buyer/i, { d: ['M12 3l9 4.5-9 4.5-9-4.5L12 3z', 'M3 12l9 4.5 9-4.5', 'M3 16.5L12 21l9-4.5'] }]
];
var OPT_RULES = {
  usecase: [[/multi-angle|angle|coverage/i, 'camera'], [/typography|layout|text/i, 'text'], [/character/i, 'user'], [/composition|aesthetic/i, 'crop'],
    [/product|brand/i, 'box'], [/storyboard/i, 'film'], [/depth|light|physics/i, 'sun'], [/production sheet|sheet/i, 'plan'], [/precision|edit/i, 'pencil'], [/written brief/i, 'sparkle']],
  task: [[/plain read/i, 'text'], [/direction/i, 'sparkle'], [/two-voice/i, 'users'], [/generate/i, 'sparkle'], [/edit/i, 'pencil']],
  output: [[/panel/i, 'panels'], [/each/i, 'layers'], [/single/i, 'single']],
  theme: [[/storytelling/i, 'book'], [/product/i, 'box'], [/sales|offers/i, 'tag'], [/people/i, 'user'], [/places/i, 'landscape'], [/characters/i, '@Gaming'],
    [/brand|text|graphics/i, 'text'], [/art|illustration/i, 'palette'], [/explainer/i, '@Education'], [/events/i, '@Events'], [/support/i, 'headset'], [/bookings|orders/i, 'calendar'], [/payments/i, 'card'], [/alerts|reminders/i, 'bell'], [/verification|security/i, 'shield']]
};
var OPT_FALLBACK = { language: 'text', skill: 'sparkle', speaker: 'user', usecase: 'tag', task: 'image', output: 'grid', theme: 'tag' };
// one colour per filter (the bank's), industries by name, difficulty by its meter
var OPT_COLOR = { language: '#0d9488', skill: '#8b5cf6', speaker: '#64748b', customer: '#0891b2', usecase: '#6366f1', task: '#3b82f6', theme: '#8b5cf6', output: '#64748b', lever: '#64748b', edge: '#64748b', bank: '#64748b', caveat: '#b45309' };
var TILE_PALETTE = ['#8b5cf6', '#3b82f6', '#06b6d4', '#6366f1', '#10b981', '#f59e0b', '#f43f5e', '#0891b2', '#7c3aed', '#2563eb'];
var TIER = ['#10b981', '#f59e0b', '#f97316', '#f43f5e'];
function tileColor(name){ var h = 0; for(var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0; return TILE_PALETTE[h % TILE_PALETTE.length]; }
function svgOf(g, cls){
  return '<svg class="' + (cls || '') + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    g.d.map(function(x){ return '<path d="' + x + '"/>'; }).join('') + (g.c || []).map(function(c){ return '<circle cx="' + c[0] + '" cy="' + c[1] + '" r="' + c[2] + '"/>'; }).join('') + '</svg>';
}
function indGlyph(name){ for(var i = 0; i < IND.length; i++){ if(IND[i][0].test(name)) return IND[i][1]; } return null; }
function optColor(key, v){ return key === 'industry' ? tileColor(v) : (OPT_COLOR[key] || '#64748b'); }
function optIcon(key, v, lg){
  var cls = 'oi' + (lg ? ' lg' : ''), c = optColor(key, v), box = function(inner){ return '<span class="' + cls + '" style="background:' + c + '1f;color:' + c + '" aria-hidden="true">' + inner + '</span>'; };
  if(key === 'difficulty'){
    var d = dimOf('difficulty'), t = d ? d.order.indexOf(v) : -1, n = d ? Math.min(d.order.length, 4) : 3;
    if(t < 0) return '';
    var col = TIER[Math.min(t, 3)], bars = '';
    for(var b = 0; b < n; b++) bars += '<i style="height:' + (lg ? 4 + b * 3 : 3 + b * 2.2) + 'px;background:' + (b <= t ? col : '#E5E7EB') + '"></i>';
    return '<span class="' + cls + ' meter" aria-hidden="true">' + bars + '</span>';
  }
  if(key === 'industry'){ return box(svgOf(indGlyph(v) || GL.grid)); }
  if(key === 'customer'){ return box(svgOf(indGlyph(v) || GL.users)); }
  var rules = OPT_RULES[key] || [], hit = null;
  for(var i = 0; i < rules.length; i++){ if(rules[i][0].test(v)){ hit = rules[i][1]; break; } }
  var name = hit || OPT_FALLBACK[key] || 'tag';
  return box(svgOf(name.charAt(0) === '@' ? (indGlyph(name.slice(1)) || GL.grid) : (GL[name] || GL.grid)));
}

// ---------- win map ----------
function dimOf(key){ return M().dims.filter(function(d){ return d.key === key; })[0]; }
function valuesOf(s, key){ var v = s.v[key]; return Array.isArray(v) ? v : (v == null ? [] : [v]); }
function groups(key, list){
  var d = dimOf(key), map = {};
  list.forEach(function(s){ valuesOf(s, key).forEach(function(v){ (map[v] = map[v] || []).push(s); }); });
  return d.order.filter(function(v){ return map[v]; }).map(function(v){ return { v: v, list: map[v], t: tally(map[v]) }; });
}
// measures per row, both models: the head-to-head card's measures, row by row
var FAM = { win: 'Win %', pass: 'Pass criteria met', quality: 'Quality mean', rel: 'Reliability', lat: 'Latency p50' };
var FAMS = ['win', 'pass', 'quality', 'rel', 'lat'];
function mval(t, fam, side){
  var A = side === 'a';
  if(fam === 'win') return t.cmp ? 100 * t[side] / t.cmp : null;
  if(fam === 'pass') return A ? t.passRateA : t.passRateB;
  if(fam === 'quality') return A ? t.meanA : t.meanB;
  if(fam === 'rel') return A ? t.minA : t.minB;
  if(fam === 'lat') return A ? t.p50A : t.p50B;
  return null;
}
function mtxt(t, fam, side){ var v = mval(t, fam, side); if(v == null) return '—'; return fam === 'win' ? winPct(t[side], t.cmp) : fam === 'lat' ? secs(v) : p1(v); }
// lead = how far the Google model is ahead (positive) or behind (negative); for latency, seconds faster
function mlead(t, fam){ var a = mval(t, fam, 'a'), b = mval(t, fam, 'b'); if(a == null || b == null) return null; return fam === 'lat' ? b - a : a - b; }
function leadTxt(fam, v){ if(v == null) return '—'; var sg = v > 0 ? '+' : v < 0 ? '−' : '±'; return sg + (fam === 'lat' ? (Math.abs(v) / 1000).toFixed(1) + 's' : r1(Math.abs(v)).toFixed(1) + ' pp'); }
function leaderOf(t, fam){
  if(fam === 'win') return t.a > t.b ? 'a' : t.b > t.a ? 'b' : null;
  var a = mval(t, fam, 'a'), b = mval(t, fam, 'b');
  if(a == null || b == null || mtxt(t, fam, 'a') === mtxt(t, fam, 'b')) return null;
  return fam === 'lat' ? (a < b ? 'a' : 'b') : (a > b ? 'a' : 'b');
}
function defaultDir(key){ return /^lat:[ab]$/.test(key) || key === 'order' ? 'asc' : 'desc'; }
function dirLabels(key){
  if(key === 'order') return null;
  if(/^lat:[ab]$/.test(key)) return { asc: 'Fastest first', desc: 'Slowest first' };
  if(key === 'n') return { desc: 'Most first', asc: 'Fewest first' };
  return { desc: 'Highest first', asc: 'Lowest first' };
}
// The evidence order that matches a Win Map sort: the Google model's measures and lead → its lead first; the rival's → the rival's lead first.
// the Win Map sort carried into the scenario list: win % -> that model's lead, latency -> fastest first,
// quality / pass criteria / reliability -> that model's score (a 'lead' pick follows the Google model)
function evSortFor(key){
  var p = key.split(':'), side = p[1] === 'b' ? 'b' : 'a';
  if(p[0] === 'win') return side === 'b' ? 'gb' : 'ga';
  if(p[0] === 'lat') return side === 'b' ? 'fb' : 'fa';
  if(p[0] === 'quality' || p[0] === 'pass' || p[0] === 'rel') return side;
  return st.ev.sort;
}
function sortVal(t, key){ var p = key.split(':'); if(p[0] === 'n') return t.cmp; return p[1] === 'lead' ? mlead(t, p[0]) : mval(t, p[0], p[1]); }
function sortRows(rows, key, dir, dk){
  var d = dimOf(dk || st.wm.dim), idx = function(v){ var i = d.order.indexOf(v); return i < 0 ? 1e9 : i; };
  if(key === 'order') return rows.slice().sort(function(x, y){ return idx(x.v) - idx(y.v); });
  var sg = dir === 'asc' ? 1 : -1;
  return rows.slice().sort(function(x, y){
    var a = sortVal(x.t, key), b = sortVal(y.t, key);
    if(a == null || b == null){ if(a != null) return -1; if(b != null) return 1; }   // no value: always last
    else if(a !== b) return sg * (a - b);
    return y.t.cmp - x.t.cmp || idx(x.v) - idx(y.v);
  });
}
function wmSortGroups(ar){
  var A = ar.a.short, B = ar.b.short, three = function(f){ return [[f + ':a', FAM[f] + ' · ' + A], [f + ':b', FAM[f] + ' · ' + B], [f + ':lead', FAM[f] + ' · ' + A + ' lead']]; };
  return [['Win %', three('win')], ['Pass criteria met', three('pass')], ['Quality mean', three('quality')],
    ['Reliability', [['rel:a', 'Reliability · ' + A], ['rel:b', 'Reliability · ' + B]]],
    ['Latency p50', three('lat')], ['Rows', [['n', 'Scenarios'], ['order', 'Category order']]]];
}
function selectGroups(act, val, groups, aria, label){
  return '<label class="sort"><span class="label">' + LICON.sort + esc(label || 'Sort by') + '</span><span class="sel"><select data-act="' + act + '" aria-label="' + esc(aria) + '">' +
    groups.map(function(g){ return '<optgroup label="' + esc(g[0]) + '">' + g[1].map(function(o){ return '<option value="' + esc(o[0]) + '"' + (o[0] === val ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') + '</optgroup>'; }).join('') +
    '</select>' + CHEV.replace('<svg ', '<svg aria-hidden="true" ') + '</span></label>';
}
function wtlSegs(t){
  var n = t.cmp || 1;
  return '<i style="width:' + (t.a / n * 100) + '%;background:' + A_FILL + '"></i><i style="width:' + (t.tie / n * 100) + '%;background:' + TIE + '"></i><i style="width:' + (t.b / n * 100) + '%;background:' + B_FILL + '"></i>';
}
var XLBL = { win: 'Win %', pass: 'Pass criteria', quality: 'Quality', rel: 'Reliability', lat: 'Latency p50' };
function tileFams(fam){ return [fam].concat(FAMS.filter(function(f){ return f !== fam && st.wm.fields[f]; })); }
function wmTile(r, key, fam, leadSel){
  var ar = arms(), t = r.t, on = picked(key, r.v), zero = !t.n && !on, tc = optColor(key, r.v);
  var head = '<span class="wt-head">' + optIcon(key, r.v, true) + '<span class="wt-name">' + esc(r.v) + '</span>' +
    (on ? '<span class="wt-check" aria-hidden="true"><svg viewBox="0 0 12 12" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.2l2.3 2.3 4.7-5"/></svg></span>' : '<span class="wt-n">' + t.n + '</span>') + '</span>';
  if(zero) return '<button type="button" class="wtile zero" disabled title="No scenario matches with the picks above">' + head + '</button>';
  var block = function(f, first){
    var ld = leaderOf(t, f);
    var val = function(side){ var txt = mtxt(t, f, side); return '<span class="v' + (ld === side ? ' lead ' + side : '') + '">' + esc(txt) + '</span>'; };
    var sub = esc(FAM[f]) + (f === 'pass' && !t.clips ? ' · ' + t.passN + ' with pass criteria' : '');
    var leadLine = first && leadSel ? '<span class="wt-lead">' + esc(ar.a.short) + ' lead <b>' + leadTxt(f, mlead(t, f)) + '</b></span>' : '';
    var tie = f === 'win' && t.cmp ? '<span class="c"><span class="tie">' + winPct(t.tie, t.cmp) + '</span><span class="k">tie</span></span>' : '';
    return '<span class="wt-k' + (first ? '' : ' wt-k2') + '">' + sub + '</span>' + leadLine +
      '<span class="wt-nums' + (first ? '' : ' wt-nums2') + '"><span>' + val('a') + '<span class="k" style="color:' + A_INK + '">' + esc(ar.a.short) + '</span></span>' + tie +
      '<span class="r">' + val('b') + '<span class="k" style="color:' + B_INK + '">' + esc(ar.b.short) + '</span></span></span>';
  };
  var extra = function(fs){
    if(!fs.length) return '';
    var cell = function(f, side){ var ld = leaderOf(t, f), txt = mtxt(t, f, side); return '<span class="xv ' + side + (ld === side ? ' lead' : '') + '" title="' + esc(ar[side].short) + '">' + esc(txt) + '</span>'; };
    return '<span class="wt-x">' +
      fs.map(function(f){ return '<span class="wt-xr"' + (f === 'pass' && !t.clips ? ' title="' + t.passN + ' with pass criteria"' : '') + '><span class="xl">' + esc(XLBL[f] || FAM[f]) + '</span>' + cell(f, 'a') + cell(f, 'b') + '</span>'; }).join('') + '</span>';
  };
  return '<button type="button" class="wtile" style="--tc:' + tc + '" data-act="wm-pick" data-k="' + key + '" data-v="' + esc(r.v) + '" aria-pressed="' + on + '" title="' + esc(plural(t.n, 'scenario')) + '">' +
    head + '<span class="wt-body">' + block(fam, true) + extra(tileFams(fam).slice(1)) + '</span>' +
    '<span class="wt-bar" title="' + esc(ar.a.short + ' won ' + t.a + ', tied ' + t.tie + ', ' + ar.b.short + ' won ' + t.b) + '">' + wtlSegs(t) + '</span>' +
    '<span class="wt-foot"><span><b style="color:' + A_INK + '">' + t.a + '</b> won · ' + t.tie + ' tied · <b style="color:' + B_INK + '">' + t.b + '</b> won</span><span>' + plural(t.n, 'scenario') + '</span></span>' +
    '</button>';
}
function wmTable(rows, key){
  var ar = arms(), d = dimOf(key), sf = st.wm.sort.split(':')[0];
  var hs = function(f){ return f === sf ? ' sorted' : ''; };
  var head = '<thead><tr><th rowspan="2" class="bot">' + esc(d.label) + '</th><th rowspan="2" class="r bot' + hs('n') + '">Scenarios</th><th rowspan="2" class="r bot">W–T–L</th>' +
    FAMS.map(function(f){ return '<th colspan="2" class="c bl' + hs(f) + '">' + esc(FAM[f]) + '</th>'; }).join('') + '</tr><tr>' +
    FAMS.map(function(f){ return '<th class="r bl sub' + hs(f) + '" style="color:' + A_INK + '">' + esc(ar.a.short) + '</th><th class="r sub' + hs(f) + '" style="color:' + B_INK + '">' + esc(ar.b.short) + '</th>'; }).join('') + '</tr></thead>';
  var body = rows.map(function(r){
    var t = r.t, on = picked(key, r.v);
    var cells = FAMS.map(function(f){
      var ld = leaderOf(t, f);
      var c = function(side){
        var txt = mtxt(t, f, side);
        var tip = f === 'pass' && t.passN && !t.clips ? (side === 'a' ? t.passA : t.passB) + ' of ' + t.passN + ' with pass criteria' : f === 'win' ? plural(t[side], 'win') : '';
        return '<td class="r' + (side === 'a' ? ' bl' : '') + hs(f) + '"' + (tip ? ' title="' + esc(tip) + '"' : '') + '>' + (ld === side ? '<span class="pill pill-success">' + esc(txt) + '</span>' : esc(txt)) + '</td>';
      };
      return c('a') + c('b');
    }).join('');
    return '<tr class="' + (on ? 'on' : '') + '"><td><button class="rowlink" data-act="wm-pick" data-k="' + key + '" data-v="' + esc(r.v) + '" aria-pressed="' + on + '">' + esc(r.v) + '<span class="pill pill-ink">shown below</span></button></td>' +
      '<td class="r' + hs('n') + '">' + t.cmp + '</td><td class="r nowrap">' + t.a + '–' + t.tie + '–' + t.b + '</td>' + cells + '</tr>';
  }).join('');
  return '<div class="card tblwrap"><table class="tbl wmt">' + head + '<tbody>' + body + '</tbody></table></div>';
}
// The Win Map is laid out like the scenario bank's step 2 (Ravi, 2026-10-12): a section per
// categorisation, a tile per value, several picks per section. A section's tiles count only what
// the picks in the sections above it allow (the bank's cascade); picks outside the map apply to all.
var WM_SECTIONS = ['industry', 'customer', 'usecase', 'task', 'difficulty'];
var WM_LABEL = { customer: 'Customers you can target', usecase: 'Use cases' };
var WM_CAP = 12;
function picked(k, v){ var a = st.ev.f[k]; return !!(a && a.indexOf(v) >= 0); }
function nPicks(){ return Object.keys(st.ev.f).reduce(function(n, k){ return n + (st.ev.f[k] || []).length; }, 0); }
function passKeys(s, keys){
  for(var i = 0; i < keys.length; i++){ var a = st.ev.f[keys[i]]; if(a && a.length && !valuesOf(s, keys[i]).some(function(v){ return a.indexOf(v) >= 0; })) return false; }
  return true;
}
function wmSections(){ var list = all(); return WM_SECTIONS.map(dimOf).filter(function(d){ return d && d.map !== false && groups(d.key, list).length > 1; }); }
function sectionRows(d, secs){
  var above = secs.slice(0, secs.indexOf(d)).map(function(x){ return x.key; });
  var outside = Object.keys(st.ev.f).filter(function(k){ return WM_SECTIONS.indexOf(k) < 0; });
  var base = all().filter(function(s){ return passKeys(s, above.concat(outside)); });
  return d.order.map(function(v){ var l = base.filter(function(s){ return valuesOf(s, d.key).indexOf(v) >= 0; }); return { v: v, list: l, t: tally(l) }; });
}
// A pick the picks above it have ruled out is dropped, top-down, as in the bank.
function prunePicks(){
  var secs = wmSections();
  secs.forEach(function(d){
    var a = st.ev.f[d.key]; if(!a || !a.length) return;
    var live = {}; sectionRows(d, secs).forEach(function(r){ if(r.t.n) live[r.v] = 1; });
    var keep = a.filter(function(v){ return live[v]; });
    if(keep.length) st.ev.f[d.key] = keep; else delete st.ev.f[d.key];
  });
}
function togglePick(k, v){ var a = (st.ev.f[k] || []).slice(), i = a.indexOf(v); if(i >= 0) a.splice(i, 1); else a.push(v); if(a.length) st.ev.f[k] = a; else delete st.ev.f[k]; prunePicks(); }
function miniTally(t){
  var ar = arms();
  if(!t.cmp) return '';
  return '<span class="wm-mini">Won by <b style="color:' + A_INK + '">' + esc(ar.a.short) + '</b> ' + winPct(t.a, t.cmp) + ' · tied ' + winPct(t.tie, t.cmp) + ' · won by <b style="color:' + B_INK + '">' + esc(ar.b.short) + '</b> ' + winPct(t.b, t.cmp) + ' <span class="mt">(' + t.cmp + ' scenarios both models completed)</span></span>';
}
// What each card shows, in the study page's own words for each measure (the head-to-head card's glosses).
var FAM_GLOSS = { win: 'wins out of the scenarios both models completed', pass: 'scenarios that met their pass criteria · higher is better',
  quality: 'mean rating · higher is better', rel: 'lowest rating on any single scenario · higher is better', lat: 'median time per image · lower is better' };
function fieldsDropdown(fam){
  var shown = tileFams(fam), open = !!st.wm.fieldsOpen;
  var summary = shown.length === 1 ? FAM[shown[0]] : FAM[shown[0]] + ' + ' + (shown.length - 1) + ' more';
  return '<div class="dd"><span class="ctrl-lbl">' + LICON.fields + 'Select fields</span>' +
    '<button type="button" class="dd-btn" data-act="wm-fields" aria-haspopup="true" aria-expanded="' + open + '"><span>' + esc(summary) + '</span>' + CHEV + '</button>' +
    (open ? '<div class="dd-menu" role="menu" aria-label="Fields shown on each card"><div class="dd-h">Fields shown on each card</div>' +
      FAMS.map(function(f){
        var on = f === fam || !!st.wm.fields[f], lock = f === fam;
        return '<button type="button" class="dd-opt" role="menuitemcheckbox" aria-checked="' + on + '" data-act="wm-field" data-v="' + f + '"' + (lock ? ' disabled' : '') + '>' +
          '<span class="cb' + (on ? ' on' : '') + '" aria-hidden="true">' + (on ? '<svg viewBox="0 0 12 12" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.2l2.3 2.3 4.7-5"/></svg>' : '') + '</span>' +
          '<span class="dd-t"><b>' + esc(FAM[f]) + '</b><span>' + esc(FAM_GLOSS[f]) + '</span>' + (lock ? '<em>Always shown: the cards are sorted by it</em>' : '') + '</span></button>';
      }).join('') + '</div>' : '') + '</div>';
}
// The bar keeps to one line however many tiles are picked: the first few as chips, the rest
// behind "+N more", which opens every pick, grouped by section, above the bar.
var BAR_CHIPS = 2;
function pickList(){
  var keys = WM_SECTIONS.concat(Object.keys(st.ev.f).filter(function(k){ return WM_SECTIONS.indexOf(k) < 0; })), out = [];
  keys.forEach(function(k){ (st.ev.f[k] || []).forEach(function(v){ out.push([k, v]); }); });
  return out;
}
function pickChip(k, v){ return '<span class="pk" title="' + esc((dimOf(k) || { label: k }).label + ': ' + v) + '"><span class="pk-t">' + esc(v) + '</span><button data-act="wm-pick" data-k="' + esc(k) + '" data-v="' + esc(v) + '" aria-label="Remove ' + esc(v) + '">×</button></span>'; }
function pickSummary(){
  var all_ = pickList(), extra = all_.length - BAR_CHIPS, open = !!st.wm.picksOpen && extra > 0;
  var h = '<span class="wm-picks">' + all_.slice(0, BAR_CHIPS).map(function(x){ return pickChip(x[0], x[1]); }).join('') + '</span>' +
    (extra > 0 ? '<button type="button" class="pk-more" data-act="wm-picks" aria-expanded="' + open + '">All ' + all_.length + ' picks' + CHEV + '</button>' : '');
  if(open){
    var groups = {}, order = [];
    all_.forEach(function(x){ if(!groups[x[0]]){ groups[x[0]] = []; order.push(x[0]); } groups[x[0]].push(x[1]); });
    h += '<div class="pk-pop" role="dialog" aria-label="Everything picked"><div class="pk-pop-h"><b>' + plural(all_.length, 'pick') + '</b><button type="button" class="linkbtn" data-act="wm-picks">Close</button></div>' +
      order.map(function(k){ return '<div class="pk-g"><span class="label">' + esc(WM_LABEL[k] || (dimOf(k) || { label: k }).label) + ' · ' + groups[k].length + '</span><div class="pk-gc">' + groups[k].map(function(v){ return pickChip(k, v); }).join('') + '</div></div>'; }).join('') + '</div>';
  }
  return h;
}
function renderWinMap(){
  var m = M(), ar = m.arms, secs = wmSections();
  var p = st.wm.sort.split(':'), fam = FAM[p[0]] ? p[0] : 'win', leadSel = p[1] === 'lead';
  var dl = dirLabels(st.wm.sort), first = defaultDir(st.wm.sort), dirs = first === 'asc' ? ['asc', 'desc'] : ['desc', 'asc'];
  var matched = all().filter(function(s){ return passKeys(s, Object.keys(st.ev.f)); }), mt = tally(matched), np = nPicks();
  var h = '<div class="block" id="winmap"><div class="bhead"><div><div class="text-h4">Win Map</div>' +
    '</div></div>' +
    '<div class="card ctrl"><div class="ctrl-row">' +
      (st.wm.view === 'map' ? fieldsDropdown(fam) : '') +
      '<div class="ctrl-g"><span class="ctrl-lbl">' + LICON.view + 'View</span><div class="toggle" role="tablist" aria-label="View"><button role="tab" data-act="wm-view" data-v="map" aria-selected="' + (st.wm.view === 'map') + '">Map</button><button role="tab" data-act="wm-view" data-v="table" aria-selected="' + (st.wm.view === 'table') + '">Table</button></div></div>' +
      sortMenu('wm-sort', st.wm.sort, wmSortGroups(ar), !!st.wm.sortOpen) +
      (dl ? '<div class="ctrl-g"><span class="ctrl-lbl">' + LICON.order + 'Order</span><div class="toggle" role="tablist" aria-label="Sort direction">' + dirs.map(function(x){ return '<button role="tab" data-act="wm-dir" data-v="' + x + '" aria-selected="' + (st.wm.dir === x) + '">' + esc(dl[x]) + '</button>'; }).join('') + '</div></div>' : '') +
    '</div>' +
    '</div>';
  secs.forEach(function(d){
    var rows = sortRows(sectionRows(d, secs), st.wm.sort, st.wm.dir, d.key), on = st.ev.f[d.key] || [];
    rows = rows.filter(function(r){ return r.t.n || on.indexOf(r.v) >= 0; }).concat(rows.filter(function(r){ return !r.t.n && on.indexOf(r.v) < 0; }));
    var long = rows.length > WM_CAP + 2, open = !!st.wm.more[d.key];
    var shown = long && !open ? rows.slice(0, WM_CAP).concat(rows.slice(WM_CAP).filter(function(r){ return on.indexOf(r.v) >= 0; })) : rows;
    var hidden = rows.length - shown.length;
    h += '<section class="wm-sec"><h3 class="wm-h">' + esc(WM_LABEL[d.key] || d.label) + '<span class="wm-hn">' + rows.filter(function(r){ return r.t.n; }).length + ' of ' + rows.length + '</span>' + (on.length ? '<span class="label" style="color:var(--b600)">' + on.length + ' picked</span>' : '') + '</h3>' +
      (st.wm.view === 'table' ? wmTable(shown, d.key) : '<div class="wm-grid">' + shown.map(function(r){ return wmTile(r, d.key, fam, leadSel); }).join('') + '</div>') +
      (long && (hidden || open) ? '<button class="linkbtn wm-more" data-act="wm-more" data-k="' + d.key + '">' + (open ? 'Show fewer' : 'Show ' + hidden + ' more') + '</button>' : '') +
      '</section>';
  });
  if(st.wm.view === 'map') h += '<div class="wm-legend"><span class="label" style="letter-spacing:.06em">W–T–L</span>' + [[A_FILL, ar.a.short + ' won'], [TIE, 'Tied'], [B_FILL, ar.b.short + ' won']].map(function(x){ return '<span><span class="sw" style="background:' + x[0] + '"></span>' + esc(x[1]) + '</span>'; }).join('') + '</div>';
  var multi = secs.filter(function(d){ return d.multi; }).map(function(d){ return d.label.toLowerCase(); });
  // the way on, always in reach: what matches, what is picked, and the scenarios themselves
  h += '<div class="wm-bar"><span class="wm-count"><b class="tnum">' + matched.length + '</b>of ' + all().length + ' scenarios match</span>' + miniTally(mt) +
    pickSummary() +
    (np ? '<button class="btn btn-secondary btn-sm" data-act="wm-clear">Clear</button>' : '') +
    '<button class="btn wm-go" data-act="wm-go"' + (matched.length ? '' : ' disabled') + '>See detailed observations →</button></div>';
  return h + '</div>';
}
function sortControl(act, val, opts, aria, label){
  return '<label class="sort"><span class="label">' + LICON.sort + esc(label || 'Sort by') + '</span><span class="sel"><select data-act="' + act + '" aria-label="' + esc(aria) + '">' +
    opts.map(function(o){ return '<option value="' + esc(o[0]) + '"' + (o[0] === val ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') +
    '</select>' + CHEV.replace('<svg ', '<svg aria-hidden="true" ') + '</span></label>';
}

// ---------- evidence ----------
var MAIN_DIMS = ['industry', 'usecase', 'task', 'difficulty'];
function textHit(s){
  if(!st.ev.q) return true;
  var hay = [s.id, s.title, s.brief, s.v.industry].concat(valuesOf(s, 'customer'), valuesOf(s, 'usecase')).join(' ').toLowerCase();
  return st.ev.q.toLowerCase().split(/\s+/).filter(Boolean).every(function(w){ return hay.indexOf(w) >= 0; });
}
function passes(s, skip){
  if(skip !== 'q' && !textHit(s)) return false;
  var f = st.ev.f;
  for(var k in f){ var a = f[k]; if(k !== skip && a && a.length && !valuesOf(s, k).some(function(v){ return a.indexOf(v) >= 0; })) return false; }
  if(skip !== 'result' && st.ev.result !== 'all'){ var w = s.win || 'none'; if(w !== st.ev.result) return false; }
  if(skip !== 'confident' && st.ev.confident && !(s.win === 'a' && s.margin >= D.confidentPP)) return false;
  return true;
}
function chipGroup(label, act, key, opts, sel, cap){
  cap = cap || 0;
  var ar = arms(), isOn = function(o){ return o.v === 'all' ? !sel.length : sel.indexOf(o.v) >= 0; };
  var q = (st.ev.optq[key] || '').toLowerCase(), searchable = opts.length > 11;
  if(q) opts = opts.filter(function(o){ return o.v === 'all' || isOn(o) || o.l.toLowerCase().indexOf(q) >= 0; });
  var shown = opts, extra = 0;
  if(cap && opts.length > cap + 1 && !(st.ev.more && st.ev.moreKey === key)){
    var keep = opts.slice(0, cap + 1);
    keep = keep.concat(opts.slice(cap + 1).filter(isOn));
    extra = opts.length - keep.length; shown = keep;
  }
  var wpTitle = ar.a.short + ' win % · ' + ar.b.short + ' win %';
  return '<div class="fgroup"><span class="label">' + esc(label) + '</span>' +
    (searchable ? '<input class="chipq" type="search" data-k="' + esc(key) + '" value="' + esc(st.ev.optq[key] || '') + '" placeholder="Search ' + esc(label.toLowerCase()) + '…" aria-label="Search ' + esc(label) + '">' : '') +
    '<div class="chips" role="group" aria-label="' + esc(label) + '">' +
    shown.map(function(o){ return '<button class="chip' + (o.arm ? ' ' + o.arm : '') + (o.n === 0 ? ' zero' : '') + '" data-act="' + act + '" data-k="' + esc(key) + '" data-v="' + esc(o.v) + '" aria-pressed="' + isOn(o) + '">' + esc(o.l) + '<span class="n">' + o.n + '</span>' +
      (o.wp ? '<span class="wp" title="' + esc(wpTitle) + '"><b style="color:' + A_INK + '">' + o.wp[0] + '</b> · <b style="color:' + B_INK + '">' + o.wp[1] + '</b></span>' : '') + '</button>'; }).join('') +
    (extra ? '<button class="linkbtn" data-act="ev-moreopts" data-k="' + esc(key) + '" style="padding:6px 4px">Show ' + extra + ' more</button>' : '') +
    '</div></div>';
}
function dimChips(key){
  var d = dimOf(key), base = all().filter(function(s){ return passes(s, key); }), cnt = {};
  base.forEach(function(s){ valuesOf(s, key).forEach(function(v){ cnt[v] = (cnt[v] || 0) + 1; }); });
  // each option's win %, over the scenarios the other picks allow (not the result filter)
  var wpBase = all().filter(function(s){ return passKeys(s, Object.keys(st.ev.f).filter(function(k){ return k !== key; })); });
  var present = {}; cur().forEach(function(s){ valuesOf(s, key).forEach(function(v){ present[v] = 1; }); });
  var opts = [{ v: 'all', l: 'All', n: base.length }].concat(d.order.filter(function(v){ return present[v]; }).map(function(v){
    var t = tally(wpBase.filter(function(s){ return valuesOf(s, key).indexOf(v) >= 0; }));
    return { v: v, l: v, n: cnt[v] || 0, wp: t.cmp ? [winPct(t.a, t.cmp), winPct(t.b, t.cmp)] : null };
  }));
  if(opts.length <= 2) return '';
  return chipGroup(d.label, 'ev-dim', key, opts, st.ev.f[key] || [], 10);
}
function scoreOf(s, side){ return s[side] && s[side].pct != null ? s[side].pct : -Infinity; }
function evSorts(){
  var ar = arms();
  return [['Who won', [['ga', 'Largest ' + ar.a.short + ' lead first'], ['gb', 'Largest ' + ar.b.short + ' lead first'], ['close', 'Closest results first']]],
    ['Score', [['a', ar.a.short + ' score, highest first'], ['b', ar.b.short + ' score, highest first']]],
    ['Speed', [['fa', ar.a.short + ' fastest first'], ['fb', ar.b.short + ' fastest first']]],
    ['Scenario', [['hard', 'Hardest first'], ['title', 'Name, A to Z'], ['id', 'Scenario id']]]];
}
var SORT_GI = { 'Who won': LICON.won, 'Score': LICON.score, 'Speed': LICON.speed, 'Scenario': LICON.scen };
function evSortLabel(k){ var l = ''; evSorts().forEach(function(g){ g[1].forEach(function(o){ if(o[0] === k) l = o[1]; }); }); return l; }
function visible(){
  var list = all().filter(function(s){ return passes(s); });
  var key = st.ev.sort;
  var ms = function(x, side){ var c = (x[side] && x[side].calls) || []; return c.length ? c.reduce(function(n, v){ return n + v[0]; }, 0) / c.length : Infinity; };
  var rank = function(x){ var d = dimOf('difficulty'); return d ? d.order.indexOf(x.v.difficulty) : 0; };
  var by = {
    id: function(){ return 0; },
    title: function(x, y){ return x.title.localeCompare(y.title); },
    close: function(x, y){ return Math.abs(scoreOf(x, 'a') - scoreOf(x, 'b')) - Math.abs(scoreOf(y, 'a') - scoreOf(y, 'b')); },
    fa: function(x, y){ return ms(x, 'a') - ms(y, 'a'); },
    fb: function(x, y){ return ms(x, 'b') - ms(y, 'b'); },
    hard: function(x, y){ return rank(y) - rank(x); },
    a: function(x, y){ return scoreOf(y, 'a') - scoreOf(x, 'a'); },
    b: function(x, y){ return scoreOf(y, 'b') - scoreOf(x, 'b'); },
    ga: function(x, y){ return (scoreOf(y, 'a') - scoreOf(y, 'b')) - (scoreOf(x, 'a') - scoreOf(x, 'b')); },
    gb: function(x, y){ return (scoreOf(y, 'b') - scoreOf(y, 'a')) - (scoreOf(x, 'b') - scoreOf(x, 'a')); }
  }[key] || function(){ return 0; };
  return list.sort(function(x, y){ return ((x.win ? 0 : 1) - (y.win ? 0 : 1)) || by(x, y) || (x.id < y.id ? -1 : 1); });
}
var FILTER_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>';
var SEARCH_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';
function filterCount(){ return nPicks() + (st.ev.result !== 'all' ? 1 : 0) + (st.ev.confident ? 1 : 0); }
var MODEL_GLYPH = {
  a: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 9.5c1.5 6 7 9.5 14 8.5-1.2 1.8-3.4 2.6-6 2.4C7.3 20 3.5 15.5 4.5 9.5z"/><path d="M18.5 18c2-2 2.6-5.3 1.6-8.5"/><path d="M19.2 7.2l1.3-2.2"/></svg>',
  b: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="6" width="13" height="13" rx="2"/><path d="M3 16l4-4 3 3 2-2 4 4"/><path d="M19.5 2.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" fill="currentColor" stroke="none"/></svg>'
};
function modelIcon(side){ var c = side === 'a' ? A_INK : B_INK; return '<span class="oi" style="background:' + c + '1f;color:' + c + '" aria-hidden="true">' + MODEL_GLYPH[side] + '</span>'; }
function resultIcon(v){
  if(v === 'a' || v === 'b') return modelIcon(v);
  var c = v === 'tie' ? '#6B6B6B' : '#9CA3AF';
  var inner = v === 'tie' ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M5 9h14M5 15h14"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 12h12"/></svg>';
  return '<span class="oi" style="background:' + c + '1f;color:' + c + '" aria-hidden="true">' + inner + '</span>';
}
function resultLabel(v){ var ar = arms(); return v === 'a' ? ar.a.short + ' wins' : v === 'b' ? ar.b.short + ' wins' : v === 'tie' ? 'Tied' : 'No score'; }
function activeChips(){
  var chips = [];
  Object.keys(st.ev.f).forEach(function(k){ (st.ev.f[k] || []).forEach(function(v){ chips.push([k, v, (dimOf(k) || { label: k }).label + ': ' + v]); }); });
  if(st.ev.result !== 'all') chips.push(['result', st.ev.result, 'Winning models: ' + resultLabel(st.ev.result)]);
  if(st.ev.confident) chips.push(['confident', '1', arms().a.short + ' high-confidence wins']);
  if(st.ev.q) chips.push(['q', st.ev.q, 'Search: “' + st.ev.q + '”']);
  if(!chips.length) return '';
  return '<div class="achips">' + chips.map(function(c){ return '<span class="pk">' + esc(c[2]) + '<button data-act="ev-unpick" data-k="' + esc(c[0]) + '" data-v="' + esc(c[1]) + '" aria-label="Remove ' + esc(c[2]) + '">×</button></span>'; }).join('') +
    '<button class="linkbtn" data-act="ev-clear">Clear all</button></div>';
}
function renderEvidence(){
  var m = M(), ar = m.arms, list = visible(), base = cur(), n = filterCount(), t = tally(list);
  return '<div class="block" id="evidence" style="margin-top:0">' +
    '<div class="ev-hero"><span class="eyebrow">' + esc(m.label) + ' · scenarios</span><h2 class="ev-h tnum">' + list.length + ' of ' + base.length + ' scenarios</h2>' + miniTally(t) + '</div>' +
    '<div class="lbar"><button class="btn btn-secondary fbtn" data-act="sheet-open" aria-haspopup="dialog">' + FILTER_ICON + 'Filters' + (n ? '<span class="fbadge">' + n + '</span>' : '') + '</button>' +
      sortMenu('ev-sort', st.ev.sort, evSorts(), !!st.ev.sortOpen) +
      '<span class="lcount"><b class="tnum">' + list.length + '</b> of ' + base.length + ' scenarios</span></div>' +
    activeChips() +
    '<div class="ev-tools"><button class="btn btn-secondary btn-sm" data-act="ev-expand">Expand all</button><button class="btn btn-secondary btn-sm" data-act="ev-collapse">Collapse all</button></div>' +
    '<div class="rows" id="rows">' + (list.length ? list.map(rowHtml).join('') : '<div class="card card-pad sm mt">No scenario matches all of these. Remove a filter to widen the list.</div>') + '</div></div>';
}
// The filters as a panel that slides in from the left, ticked like a checklist (the scenario bank's
// FilterSheet): one folding group per filter, the picks apply as they are made, Apply closes it.
var SHEET_OPEN = ['industry', 'customer', 'usecase', 'difficulty', 'result'];
var SHEET_CAP = 8;
function sheetRow(key, v, label, n, on, wp, act){
  var off = !n && !on;
  return '<button class="srow-opt' + (off ? ' off' : '') + '" role="checkbox" aria-checked="' + on + '"' + (off ? ' disabled' : '') + ' data-act="' + act + '" data-k="' + esc(key) + '" data-v="' + esc(v) + '">' +
    '<span class="cb' + (on ? ' on' : '') + '" aria-hidden="true">' + (on ? '<svg viewBox="0 0 12 12" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.2l2.3 2.3 4.7-5"/></svg>' : '') + '</span>' +
    (key === 'result' ? resultIcon(on ? st.ev.result : v) : key === 'confident' ? modelIcon('a') : optIcon(key, v === 'all' ? label : v)) + '<span class="ol">' + esc(label) + '</span>' +
    (wp ? '<span class="wp"><b style="color:' + A_INK + '">' + wp[0] + '</b> · <b style="color:' + B_INK + '">' + wp[1] + '</b></span>' : '') +
    '<span class="oc">' + n + '</span></button>';
}
function sheetGroup(key, label, rows, picks){
  var isShut = st.shut[key] != null ? st.shut[key] : (SHEET_OPEN.indexOf(key) < 0 && !picks);
  var long = rows.length > SHEET_CAP + 2, more = !!st.sheetMore[key];
  var shown = long && !more ? rows.slice(0, SHEET_CAP).concat(rows.slice(SHEET_CAP).filter(function(r){ return r.on; })) : rows;
  return '<section class="sg"><button class="sg-h" data-act="sheet-fold" data-k="' + esc(key) + '" aria-expanded="' + !isShut + '"><span class="label" style="color:var(--ink)">' + esc(label) + (picks ? ' · ' + picks : '') + '</span>' + CHEV + '</button>' +
    (isShut ? '' : '<div class="sg-b">' + shown.map(function(r){ return r.html; }).join('') +
      (long ? '<button class="linkbtn" data-act="sheet-more" data-k="' + esc(key) + '" style="padding:4px 6px">' + (more ? 'Show fewer' : 'Show ' + (rows.length - shown.length) + ' more') + '</button>' : '') + '</div>') + '</section>';
}
function renderSheet(){
  var el = document.getElementById('sheet');
  if(!el){ el = document.createElement('div'); el.id = 'sheet'; document.body.appendChild(el); }
  if(!st.sheet || st.step !== 'scenarios'){ el.innerHTML = ''; document.body.style.overflow = ''; return; }
  document.body.style.overflow = 'hidden';
  var m = M(), list = visible(), base = cur(), n = filterCount(), html = '';
  var order = ['industry', 'customer', 'usecase', 'task', 'difficulty'].concat(m.dims.map(function(d){ return d.key; }).filter(function(k){ return ['industry', 'customer', 'usecase', 'task', 'difficulty'].indexOf(k) < 0; }));
  order.forEach(function(key, i){
    var d = dimOf(key); if(!d) return;
    var cnt = {}; all().filter(function(s){ return passes(s, key); }).forEach(function(s){ valuesOf(s, key).forEach(function(v){ cnt[v] = (cnt[v] || 0) + 1; }); });
    var wpBase = all().filter(function(s){ return textHit(s) && passKeys(s, Object.keys(st.ev.f).filter(function(k){ return k !== key; })); });
    var sel = st.ev.f[key] || [];
    var rows = d.order.map(function(v){
      var t = tally(wpBase.filter(function(s){ return valuesOf(s, key).indexOf(v) >= 0; })), on = sel.indexOf(v) >= 0;
      return { on: on, n: cnt[v] || 0, html: sheetRow(key, v, v, cnt[v] || 0, on, t.cmp ? [winPct(t.a, t.cmp), winPct(t.b, t.cmp)] : null, 'ev-dim') };
    });
    rows = rows.filter(function(r){ return r.n || r.on; }).concat(rows.filter(function(r){ return !r.n && !r.on; }));
    if(rows.length > 1) html += sheetGroup(key, (WM_LABEL[key] || d.label), rows, sel.length);
    if(i === 0){
      // who won, right after the industry, as on the evidence card before
      var rb = all().filter(function(s){ return passes(s, 'result'); }), rc = { a: 0, b: 0, tie: 0, none: 0 }; rb.forEach(function(s){ rc[s.win || 'none']++; });
      var rrows = ['a', 'b', 'tie', 'none'].filter(function(v){ return cur().some(function(s){ return (s.win || 'none') === v; }); }).map(function(v){
        var on = st.ev.result === v; return { on: on, n: rc[v], html: sheetRow('result', on ? 'all' : v, resultLabel(v), rc[v], on, null, 'ev-res') }; });
      var confN = all().filter(function(s){ return passes(s, 'confident') && s.win === 'a' && s.margin >= D.confidentPP; }).length;
      rrows.push({ on: st.ev.confident, n: confN, html: sheetRow('confident', '1', m.arms.a.short + ' high-confidence wins (' + D.confidentPP + '% or more higher)', confN, st.ev.confident, null, 'ev-conf') });
      html += sheetGroup('result', 'Winning models', rrows, (st.ev.result !== 'all' ? 1 : 0) + (st.ev.confident ? 1 : 0));
    }
  });
  var prevBody = el.querySelector('.sh-body'), keepTop = prevBody ? prevBody.scrollTop : 0;
  el.innerHTML = '<div class="sh" role="dialog" aria-modal="true" aria-labelledby="sh-title"><div class="sh-back" data-act="sheet-close"></div>' +
    '<aside class="sh-panel' + (st.sheetAnim ? ' anim' : '') + '"><header class="sh-head"><div><h2 id="sh-title" class="text-h4">Filters</h2><p class="sh-n"><b class="tnum">' + list.length + '</b> of ' + base.length + ' scenarios</p></div>' +
    '<button class="linkbtn sh-clear" data-act="ev-clear"' + (n ? '' : ' style="visibility:hidden"') + '>Clear all' + (n ? ' (' + n + ')' : '') + '</button>' +
    '<button class="sh-x" data-act="sheet-close" aria-label="Close filters">×</button></header>' +
    '<div class="sh-body">' + html + '</div>' +
    '<footer class="sh-foot"><button class="btn btn-primary" data-act="sheet-close" style="width:100%">Apply</button></footer></aside></div>';
  st.sheetAnim = false;
  var nb = el.querySelector('.sh-body'); if(nb) nb.scrollTop = keepTop;
}
// the flag on a scenario whose input image still contradicts its brief (input-caveats.json)
function cavLabel(level){ return level === 'partial' ? 'Input image partly off-brief' : 'Input image off-brief'; }
var CAV_TIP = 'The input image given to both models does not fully match what the brief describes. Both models got the same input, so the comparison stays fair, but checks on the contradicted detail are noisy.';
function outcome(s){
  var ar = arms();
  if(!s.win) return '<span class="pill pill-neutral">No score</span>';
  if(s.win === 'tie') return '<span class="pill pill-warning">tie</span>';
  return '<span class="pill pill-success">' + esc(ar[s.win].name) + ' (+' + s.margin.toFixed(1) + ' pp)</span>';
}
function firstImg(arm){ return arm.main ? arm.main.img : (arm.frames && arm.frames.length ? arm.frames[0].img : null); }
function rowHtml(s){
  var open = !!(st.open[s.id] || st.open.all);
  var thumbs = '';
  if(s.sources.length) thumbs += '<img class="thumb src" alt="" data-img="' + s.sources[0].img + '">';
  if(!s.a.takes) ['a', 'b'].forEach(function(k){ var id = firstImg(s[k]); thumbs += id ? '<img class="thumb" alt="" data-img="' + id + '">' : '<span class="thumb none">—</span>'; });
  var sa = s.a.pct != null ? p1(s.a.pct) : '—', sb = s.b.pct != null ? p1(s.b.pct) : '—';
  var pf = function(a){ var ok = a.gn ? 'Passed every automatic check in both rounds' : 'Pass criteria met', no = a.gn ? 'Failed an automatic check in a round' : 'Pass criteria not met';
    return a.pass === true ? '<span class="pf ok" title="' + ok + '">✓</span>' : a.pass === false ? '<span class="pf no" title="' + no + '">✗</span>' : ''; };
  return '<details class="card card-hover srow" data-id="' + esc(s.id) + '"' + (open ? ' open' : '') + '><summary>' +
    '<span class="sid">' + esc(s.id) + '</span><span class="stitle">' + esc(s.title) + '</span>' +
    '<span class="pill pill-neutral">' + esc(s.v.industry) + '</span>' +
    (s.lang ? '<span class="pill pill-neutral">' + esc(s.lang) + '</span>' : '') +
    (s.set ? '<span class="pill pill-neutral">' + (s.set.mode === 'each' ? 'Set of ' + s.set.members.length : s.set.members.length + ' panels') + '</span>' : '') +
    (s.caveat ? '<span class="pill pill-warning" title="' + CAV_TIP + '">' + cavLabel(s.caveat.level) + '</span>' : '') +
    (s.canvasIssue ? '<span class="pill pill-danger" title="' + esc(s.canvasIssue) + '">Canvas differs</span>' : '') +
    outcome(s) +
    '<span class="scores"><b style="' + (s.win === 'a' ? 'color:' + A_INK : '') + '">' + sa + '</b>' + pf(s.a) + '<span class="mt"> vs </span><b style="' + (s.win === 'b' ? 'color:' + B_INK : '') + '">' + sb + '</b>' + pf(s.b) + '</span>' +
    '<span class="thumbs">' + thumbs + '</span>' + CHEV.replace('<svg ', '<svg class="chev" aria-hidden="true" ') +
    '</summary><div class="sbody">' + (open ? bodyHtml(s) : '') + '</div></details>';
}
function lbItems(s){
  var ar = arms(), items = [];
  s.sources.forEach(function(x){ items.push({ img: x.img, href: x.href, cap: s.id + ' · original input (' + x.role + ')' }); });
  ['a', 'b'].forEach(function(k){
    var a = s[k], sc = a.pct != null ? ' · ' + p1(a.pct) : '';
    if(a.main) items.push({ img: a.main.img, href: a.main.href, cap: s.id + ' · ' + ar[k].name + sc });
    (a.frames || []).forEach(function(f){ items.push({ img: f.img, href: f.href, cap: s.id + ' · ' + ar[k].name + ' · ' + frameLabel(f) }); });
  });
  return items;
}
function frameLabel(f){ return /^\d/.test(f.name || '') ? f.name : f.k + ' · ' + f.name; }
function figure(s, k){
  var ar = arms(), a = s[k], win = s.win === k, key = M().key;
  var box, has = !!(a.main || (a.frames && a.frames.length));
  if(a.main && key === 'video' && a.main.src){ box = '<video controls preload="none" playsinline src="' + esc(a.main.src) + '"' + (a.main.img ? ' poster="' + imgSrc(a.main.img) + '"' : '') + '></video>'; }
  else if(a.main && key === 'voice' && a.main.src){ box = '<audio controls preload="none" src="' + esc(a.main.src) + '"></audio>'; }
  else if(a.main){ box = '<img alt="' + esc(ar[k].name) + ' output" data-img="' + a.main.img + '" data-act="lb" data-s="' + esc(s.id) + '" data-i="' + a.main.img + '">'; }
  else if(a.frames && a.frames.length){
    var n = a.frames.length, cols = n <= 1 ? 1 : n <= 4 ? 2 : 3;
    box = '<div class="fgrid" style="grid-template-columns:repeat(' + cols + ',1fr);grid-template-rows:repeat(' + Math.ceil(n / cols) + ',minmax(0,1fr))">' + a.frames.map(function(f){
      return '<button data-act="lb" data-s="' + esc(s.id) + '" data-i="' + f.img + '" title="' + esc(frameLabel(f)) + '"><img alt="' + esc(f.name) + '" data-img="' + f.img + '"><span class="fk">' + f.k + '</span></button>'; }).join('') + '</div>';
  } else { box = '<span class="empty">no output</span>'; }
  var score = a.pct != null ? '<span class="pill score' + (win ? ' win' : '') + '">' + p1(a.pct) + '</span>'
    : (has ? '<span class="pill pill-danger score">' + (a.st === 'invalid' ? 'invalid — earned 0' : 'No score') + '</span>' : '');
  var calls = a.calls || [], tot = calls.reduce(function(x, c){ return x + c[0]; }, 0);
  var lat = calls.length ? (calls.length > 1 ? plural(calls.length, M().unit) + ' · ' + secs(tot) : secs(tot)) : '';
  var latTip = calls.length > 1 ? 'time to generate (sum of ' + calls.length + ' requests)' : 'time to generate';
  var full = a.main ? a.main.href : a.sheetHref;
  return '<figure><div class="figbox">' + box + score + '</div><figcaption><span class="fn">' + esc(ar[k].name) + '</span><span style="display:inline-flex;gap:8px;align-items:baseline">' +
    (full ? '<a href="' + esc(full) + '" target="_blank" rel="noopener">full size ↗</a>' : '') + (lat ? '<span class="pill pill-neutral" title="' + latTip + '">' + lat + '</span>' : '') + '</span></figcaption></figure>';
}
function srcFigure(s){
  var x = s.sources[0], extra = s.sources.slice(1);
  return '<figure><div class="figbox src"><img alt="original input" data-img="' + x.img + '" data-act="lb" data-s="' + esc(s.id) + '" data-i="' + x.img + '">' +
    (extra.length ? '<span class="extras">' + extra.map(function(e){ return '<button data-act="lb" data-s="' + esc(s.id) + '" data-i="' + e.img + '" title="' + esc(e.role) + '"><img alt="' + esc(e.role) + '" data-img="' + e.img + '"></button>'; }).join('') + '</span>' : '') +
    '</div><figcaption><span class="fn src">original · ' + esc(x.role) + (extra.length ? ' + ' + extra.length + ' more' : '') + '</span>' + (x.href ? '<a href="' + esc(x.href) + '" target="_blank" rel="noopener">full size ↗</a>' : '') + '</figcaption></figure>';
}
function wfmt(w){ return w == null ? '' : (Math.round(w * 100) / 100).toFixed(2); }
function errHtml(e){
  if(!e) return '';
  var head = e.kind + (e.member ? ' · image ' + e.member : '') + (e.attempts ? ' · after ' + plural(e.attempts, 'attempt') : '');
  var line = [e.msg, (e.codes || []).join(' · ')].filter(Boolean).join(' — ');
  return '<p class="err"><span class="pill pill-danger">' + esc(head) + '</span>' + (line ? ' <span>' + esc(line) + '</span>' : '') + '</p>' +
    (e.raw ? '<details class="why"><summary>Error as recorded</summary><div class="wb"><p class="mono">' + esc(e.raw) + '</p></div></details>' : '');
}
function diag(s, k){
  var ar = arms(), a = s[k];
  var pills = [];
  if(a.pass === true) pills.push('<span class="pill pill-success">✓ Pass criteria met</span>');
  else if(a.pass === false) pills.push('<span class="pill pill-danger">✗ Pass criteria not met</span>');
  if(a.cn) pills.push('<span class="pill pill-neutral">' + a.cy + ' of ' + a.cn + ' checks yes</span>');
  if(a.gates) pills.push(a.gates.ok ? '<span class="pill pill-success">✓ all gates</span>' : '<span class="pill pill-danger">✗ failed gates: ' + esc(a.gates.fail.join('; ')) + '</span>');
  if(a.earlier && a.earlier.length){
    var nE = a.earlier.reduce(function(x, e){ return x + e[1]; }, 0);
    pills.push('<span class="pill pill-warning" title="It completed on a later attempt; the rating here is from that one.">' +
      esc(nE === 1 ? 'An earlier attempt: ' + a.earlier[0][0] : plural(nE, 'earlier attempt') + ': ' + a.earlier.map(function(e){ return e[0] + (e[1] > 1 ? ' ×' + e[1] : ''); }).join(', ')) + '</span>');
  }
  var crit = (a.crit || []).map(function(c){
    var op = c.pct < 50 ? .35 : c.pct < 75 ? .6 : 1;
    return '<div class="crit"><span class="cn" title="' + esc(c.src + (c.w != null ? ' · weight ' + wfmt(c.w) : '')) + '">' + esc(c.n) + '</span><span class="bar"><i style="width:' + Math.max(0, Math.min(100, c.pct)) + '%;opacity:' + op + '"></i></span><span class="cv2">' + c.pct.toFixed(0) + '%</span></div>';
  }).join('');
  var why = (a.crit || []).filter(function(c){ return c.why; }).map(function(c){
    return '<p><b>' + esc(c.n) + '</b> <span class="src">' + esc(c.src + (c.w != null ? ' · w ' + wfmt(c.w) : '')) + '</span><br>“' + esc(c.why) + '”</p>'; }).join('') +
    (a.note ? '<p><b>Judge note:</b> “' + esc(a.note) + '”</p>' : '');
  return '<div class="diag"><div class="dn">' + esc(ar[k].name) + '</div>' + (pills.length ? '<div class="pills">' + pills.join('') + '</div>' : '') + errHtml(a.err) +
    (crit ? '<div class="crits">' + crit + '</div>' : '') +
    (why ? '<details class="why"><summary>Automated judge reasoning — why these scores</summary><div class="wb">' + why + '</div></details>' : '') + '</div>';
}
function checklist(s){
  var ar = arms(), cl = s.checklist || [];
  if(!cl.length || (!s.a.ans && !s.b.ans)) return '';
  var both = !!(s.a.ans && s.b.ans);
  var yn = function(v){ return v === 1 ? '<span class="pill pill-success yn">yes</span>' : v === 0 ? '<span class="pill pill-danger yn">no</span>' : '<span class="mt">—</span>'; };
  var split = 0, hasEv = false;
  var rows = cl.map(function(c, i){
    var va = s.a.ans ? s.a.ans[i] : null, vb = s.b.ans ? s.b.ans[i] : null, d = va != null && vb != null && va !== vb;
    if(d) split++;
    var eva = s.a.ev ? s.a.ev[i] : '', evb = s.b.ev ? s.b.ev[i] : '';
    if(eva || evb) hasEv = true;
    return '<tr class="' + (d ? 'split' : '') + (i >= CL_SHOW ? ' extra' : '') + '"><td class="r mt">' + (i + 1) + '</td><td><span class="q">' + esc(c.q) + '</span> <span class="meta">' + esc([c.dim, c.scope].filter(Boolean).join(' · ')) + '</span>' +
      (eva ? '<span class="evd"><b style="color:' + A_INK + '">' + esc(ar.a.short) + ':</b> ' + esc(eva) + '</span>' : '') +
      (evb ? '<span class="evd"><b style="color:' + B_INK + '">' + esc(ar.b.short) + ':</b> ' + esc(evb) + '</span>' : '') +
      '</td><td class="c bl">' + yn(va) + '</td><td class="c">' + yn(vb) + '</td></tr>';
  }).join('');
  return '<details class="cl fold"><summary><span class="label" style="font-weight:600;color:inherit">Evaluation checklist · ' + plural(cl.length, 'check') + '</span>' +
    (both ? (split ? '<span class="xs mt">' + plural(split, 'answer') + ' differ</span>' : '<span class="xs mt">same answers on every check</span>') : '') + CHEV + '</summary>' +
    '<div class="cl-tools" style="margin-top:8px"><span></span><span style="display:inline-flex;gap:12px;align-items:center;flex-wrap:wrap">' + (both && split ? '<span class="xs mt">Differences highlighted</span>' : '') +
    (hasEv ? '<button class="btn btn-secondary btn-sm" data-act="cl-ev" aria-pressed="false">Show the judge’s evidence</button>' : '') + '</span></div>' +
    '<div class="card tblwrap clt' + (cl.length > CL_SHOW ? ' folded' : '') + '" style="margin-top:8px;border-radius:8px"><table class="tbl cltbl"><thead><tr><th class="r" style="width:40px">#</th><th>Check</th>' +
    '<th class="c bl ans" style="color:' + A_INK + '">' + esc(ar.a.short) + '</th><th class="c ans" style="color:' + B_INK + '">' + esc(ar.b.short) + '</th></tr></thead><tbody>' + rows + '</tbody></table>' +
    (cl.length > CL_SHOW ? '<div class="cl-more"><button class="linkbtn" data-act="cl-more" data-n="' + (cl.length - CL_SHOW) + '">Show ' + plural(cl.length - CL_SHOW, 'more check') + '</button></div>' : '') + '</div></details>';
}
var CL_SHOW = 5;
// voice: the script and its checks, then each model's two takes with a player, facts and the judge's scores
function voiceTake(t, side, s){
  var fill = side === 'a' ? A_FILL : B_FILL;
  var pill = t.st === 'scored' ? '<span class="pill ' + (s.win === side ? 'pill-success' : 'pill-neutral') + '">' + p1(t.score * 10) + '</span>'
    : t.st === 'invalid' ? '<span class="pill pill-danger">failed a check · 0%</span>' : '<span class="pill pill-neutral">not judged</span>';
  var crit = t.crit.map(function(c){ var v = c[1]; return '<div class="tcrit"><span>' + esc(c[0]) + '</span><span class="tb"><i style="width:' + (v * 10) + '%;background:' + fill + ';opacity:' + (v < 5 ? .35 : v < 7.5 ? .6 : 1) + '"></i></span><span class="tnum">' + v.toFixed(1) + '</span></div>'; }).join('');
  var why = t.why.map(function(w){ return '<p><b>' + esc(w[0]) + '</b> <span class="mt">' + (w[1] != null ? w[1] + '/10' : '') + '</span><br>“' + esc(w[2]) + '”</p>'; }).join('') + (t.note ? '<p><b>Judge note:</b> “' + esc(t.note) + '”</p>' : '');
  return '<div class="take"><div class="take-h"><span class="label" style="color:var(--ink)">' + esc(t.r) + '</span>' + pill + '</div>' +
    (t.au ? '<audio controls preload="none" data-au="' + esc(t.au) + '"></audio>' : '<p class="xs mt">No audio</p>') +
    '<div class="facts"><span>Length <b>' + (t.dur != null ? t.dur.toFixed(1) + 's' : '—') + '</b></span><span>First audio <b>' + secs(t.ttfa) + '</b></span><span>Whole call <b>' + secs(t.lat) + '</b></span>' +
    (t.wer != null ? '<span>Word errors <b>' + (t.wer * 100).toFixed(1) + '%</b></span>' : '') + '<span>Cost <b>' + money(t.cost != null ? t.cost / 1e6 : null) + '</b></span></div>' +
    (t.failed.length ? '<div class="failbox">' + t.failed.map(function(f){ return '✕ ' + esc(f); }).join('<br>') + '</div>' : '') +
    (crit ? '<div class="tcrits">' + crit + '</div>' : '') +
    (why ? '<details class="why"><summary>Automated judge reasoning — why these scores</summary><div class="wb">' + why + '</div></details>' : '') + '</div>';
}
function voiceBody(s){
  var ar = arms();
  var checks = [].concat(s.mustSay.map(function(w){ return 'must say “' + w + '”'; }), s.mustSayAny.map(function(ws){ return 'must say one of ' + ws.map(function(w){ return '“' + w + '”'; }).join(', '); }), s.mustNotSay.map(function(w){ return 'must not say “' + w + '”'; }));
  var h = '<div class="briefs"><div class="brief sindic"><b>Script (verbatim' + (s.langCode ? ', ' + esc(s.langCode) : '') + '): </b>' + esc(s.brief) + (s.direction ? '\n<b>Delivery direction: </b>' + esc(s.direction) : '') + '</div>' +
    '<div class="brief"><b>Expected: </b>' + esc(s.expectedList.map(function(e, i){ return (i + 1) + ') ' + e; }).join('\n')) +
    (checks.length ? '\n<b>Word checks: </b>' + esc(checks.join('; ')) : '') + (s.maxWer != null ? '\n<b>Word error rate: </b>at most ' + Math.round(s.maxWer * 100) + '% against the script' : '') + '</div></div>';
  h += '<div class="takes">' + ['a', 'b'].map(function(side){
    return '<div class="arm ' + side + '"><div class="arm-h"><span class="fn" style="color:' + (side === 'a' ? A_INK : B_INK) + '">' + esc(ar[side].name) + '</span><span class="sm">Two-round mean <b class="tnum">' + (s[side].pct != null ? p1(s[side].pct) : '—') + '</b></span></div>' +
      s[side].takes.map(function(t){ return voiceTake(t, side, s); }).join('') + '</div>';
  }).join('') + '</div>';
  return h;
}
function bodyHtml(s){
  if(s.a.takes) return voiceBody(s);
  var h = '<div class="briefs"><div class="brief"><b>Brief (verbatim): </b>' + esc(s.brief) +
    (s.set && s.set.members.length ? '<ol>' + s.set.members.map(function(mm){ return '<li><b>' + esc(mm.name) + '</b> — ' + esc(mm.brief) + '</li>'; }).join('') + '</ol>' : '') + '</div>' +
    '<div class="brief"><b>Expected: </b>' + esc(s.expected) + (s.required ? '\n<b>Exact wording in the image: </b>' + esc(s.required) : '') +
    (s.passRule ? '\n<b>Pass criteria: </b>' + esc(s.passRule) : '') + '</div></div>';
  if(s.canvasIssue) h += '<div class="cv" style="border-left-color:#E11D48;background:#FFF1F2"><b>Canvas differs:</b> ' + esc(s.canvasIssue) + '</div>';
  if(s.caveat) h += '<div class="cv"><b>' + cavLabel(s.caveat.level) + (s.caveat.why ? ':</b> ' + esc(s.caveat.why) : '</b> ' + CAV_TIP) +
    (s.caveat.still && s.caveat.still.length ? '<ul class="still">' + s.caveat.still.map(function(x){ return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '') + '</div>';
  h += '<div class="figs' + (s.sources.length ? ' three' : '') + '">' + (s.sources.length ? srcFigure(s) : '') + figure(s, 'a') + figure(s, 'b') + '</div>';
  h += '<div class="diags">' + diag(s, 'a') + diag(s, 'b') + '</div>';
  h += checklist(s);
  return h;
}

// ---------- implementation approach (method facts) ----------
// gate descriptions: the study site's (studies.json image lane, "The Gates")
var GATE_DESC = { decodes: 'The file opens as an image.', format: 'An allowed file type for the scenario.',
  dimensions: 'At least the size the scenario asks for.', not_blank: 'Pixel spread above a floor, so a flat image cannot pass.',
  preservation: 'Edit tasks: pHash distance to the source inside the declared bound.' };
function renderMethod(){
  var m = M(), ar = m.arms, S = all(), n = S.length;
  var c = function(f){ return S.filter(f).length; };
  var v3 = c(function(s){ return s.bankKey === 'v3'; }), v2 = n - v3;
  var edit = c(function(s){ return s.task === 'image_edit'; }), gen = n - edit;
  var sets = c(function(s){ return s.set && s.set.mode === 'each'; }), panels = c(function(s){ return s.set && s.set.mode === 'panels'; });
  var cav = S.filter(function(s){ return s.caveat; });
  var cavLv = {}; cav.forEach(function(s){ cavLv[s.caveat.level] = (cavLv[s.caveat.level] || 0) + 1; });
  var cv = m.canvas || { checked: 0, mismatched: [], quality: {} };
  var qb = (cv.quality && cv.quality.b) || [], qa = (cv.quality && cv.quality.a) || [];
  var J = m.judge || {}, G = m.gates || { names: [] };
  var li = function(x){ return '<li>' + x + '</li>'; };
  var items = [
    ['Scenarios', '<p>' + [plural(n, 'scenario') + ': ' + v3 + ' from the v3 bank, ' + v2 + ' from the v2 bank',
      edit + ' “' + esc(TASK_LABEL.image_edit) + '”, ' + gen + ' “' + esc(TASK_LABEL.text_to_image) + '”',
      sets ? sets + ' sets: “' + esc(OUTPUT_LABEL.each) + '”' : '', panels ? panels + ' sets: “' + esc(OUTPUT_LABEL.panels) + '”' : ''].filter(Boolean).join(' · ') + '</p>'],
    ['How each scenario is run', '<p>Each scenario’s inputs, the brief and any source or reference image it supplies, are frozen and fingerprinted before the run. Each scenario is scored on one generation per model, not several seeds; attempts, timeouts and refusals are recorded as results.</p>' +
      '<p>Both models: the same brief, the same input images and the same pixel size per scenario' + (qb.length ? '; ' + esc(ar.b.short) + ' quality “' + esc(qb.join('/')) + '”' : '') + (qa.length ? '; ' + esc(ar.a.short) + ' quality “' + esc(qa.join('/')) + '”' : '') + '.' +
      (cv.checked ? ' Delivered pixel size identical in ' + (cv.checked - cv.mismatched.length) + ' of ' + cv.checked + ' scenarios with an output from each' + (sets ? ', image by image for sets' : '') + '.' : '') + '</p>'],
    ['Gates', '<p>Code checks every output before judging. An output that fails a gate is made once more; if the second one fails too, the cell is invalid (scored 0) and the judge is not called for it.</p>' +
      (G.names.length ? '<ul class="mlist">' + G.names.map(function(g){ return li('<b class="mono">' + esc(g) + '</b> — ' + esc(GATE_DESC[g] || '')); }).join('') + '</ul>' : '') +
      (G.cells ? '<p>' + G.passed + ' of ' + G.cells + ' outputs passed every gate.</p>' : '')],
    ['Judging', '<p>The judge is <span class="mono">' + esc(J.model || '—') + '</span>' + (J.provider === 'google-vertex' ? ', served from Vertex AI' + (J.location ? ' at the ' + esc(J.location) + ' endpoint' : '') : '') + ', at temperature ' + esc(J.temperature) + (J.thinking ? ' (thinking level ' + esc(J.thinking) + ')' : '') + '. ' +
      'It rates one model’s output per call, a set’s images together, against the brief and the scenario’s checklist, never beside the other model’s output; the scenario’s input images come first. ' +
      'Each output carries a letter in a per-scenario shuffled order and is re-encoded so no metadata survives, and no model or vendor name appears in the prompt.</p>' +
      '<p>Weights sum to 1.0, and the weighted total is the scenario’s score. Scores are compared at two decimals.</p>'],
    ['Input caveats', cav.length ? (m.caveatNote ? '<p>“' + esc(m.caveatNote) + '”</p>' : '') + '<p>' + plural(cav.length, 'scenario') + ': ' + Object.keys(cavLv).sort().reverse().map(function(k){ return cavLv[k] + ' ' + esc(k); }).join(', ') + '.</p>' : ''],
    ['Not guaranteed', '<ul class="mlist">' + li('The judge is a Google model, and one of the models it scores is also Google’s') + li('The judge has not been calibrated against human raters') + li('One generation per scenario, not three seeds') + '</ul>']
  ];
  return '<div class="block" id="method"><div class="bhead"><div class="text-h4">Implementation Approach</div></div><div class="card method">' +
    items.filter(function(x){ return x[1]; }).map(function(x){ return '<div class="mitem"><span class="label">' + esc(x[0]) + '</span><div>' + x[1] + '</div></div>'; }).join('') + '</div></div>';
}
var TASK_LABEL = { text_to_image: 'Generate from a text prompt', image_edit: 'Edit an existing image' };
var OUTPUT_LABEL = { each: 'Each output is its own request, from the same inputs', panels: 'One image, every output a panel in it' };

// ---------- run details (internal) ----------
function dTable(t, solo){
  var al = t.cols.map(function(c){ return /\|r$/.test(c) ? 'r' : ''; }), cols = t.cols.map(function(c){ return c.replace(/\|r$/, ''); });
  var starts = {}, at = 0;   // first column of each header group gets a rule
  (t.head || []).forEach(function(x, i){ if(i) starts[at] = 1; at += x[1]; });
  var cl = function(i){ return (al[i] || '') + (starts[i] ? ' bl' : ''); };
  var nw = function(v){ return String(v).length <= 26 ? ' nowrap' : ''; };
  var head = (t.head ? '<tr>' + t.head.map(function(x, i){ return '<th colspan="' + x[1] + '" class="c' + (i ? ' bl' : '') + '">' + esc(x[0]) + '</th>'; }).join('') + '</tr>' : '') +
    '<tr>' + cols.map(function(c, i){ return '<th class="' + cl(i) + '">' + esc(c) + '</th>'; }).join('') + '</tr>';
  var body = t.rows.map(function(r){ return '<tr>' + r.map(function(v, i){ return '<td class="' + cl(i) + nw(v) + '">' + esc(v) + '</td>'; }).join('') + '</tr>'; }).join('');
  return '<div class="dt">' + (solo ? '' : '<div class="dt-h"><span class="dt-t">' + esc(t.title) + '</span></div>') +
    '<div class="card tblwrap"><table class="tbl dtbl"><thead>' + head + '</thead><tbody>' + (body || '<tr><td colspan="' + cols.length + '" class="mt">none</td></tr>') + '</tbody></table></div>' +
    (t.src ? '<p class="dt-s">' + esc(t.src) + '</p>' : '') + '</div>';
}
function renderDetails(){
  var R = M().details; if(!R) return '';
  return '<div class="block" id="details"><div class="bhead"><div class="text-h4">Run details</div></div>' +
    R.groups.map(function(g){ var solo = g.tables.length === 1 && g.tables[0].title === g.title; return '<section class="dgroup"><h3 class="text-h4 dgl">' + esc(g.title) + '</h3>' + g.tables.map(function(t){ return dTable(t, solo); }).join('') + '</section>'; }).join('') + '</div>';
}

// ---------- page ----------
// each lane may name its own measures and Win Map sections (Voice: gates passed, language first)
var MEDIA_BASE = null;
function applyMedia(){
  if(!MEDIA_BASE) MEDIA_BASE = { fam: FAM, gloss: FAM_GLOSS, secs: WM_SECTIONS };
  var m = M(); FAM = m.fam || MEDIA_BASE.fam; FAM_GLOSS = m.famGloss || MEDIA_BASE.gloss; WM_SECTIONS = m.sections || MEDIA_BASE.secs;
}
function renderLane(){
  applyMedia();
  var html = st.only === 'details' && INTERNAL ? renderDetails()
    : st.step === 'narrow' ? renderCompare() + renderWinMap()
    : st.step === 'scenarios' ? renderEvidence()
    : st.step === 'method' && INTERNAL ? renderDetails()
    : renderMediaStep();
  document.getElementById('lane').innerHTML = html;
  hydrate(document.getElementById('lane'));
  renderSheet();
}
function goStep(v, replace){
  st.step = v; st.sheet = false;
  if(location.hash !== '#' + v) history[replace ? 'replaceState' : 'pushState'](null, '', location.pathname + location.search + '#' + v);
  renderStrip(); renderLane(); window.scrollTo(0, 0);
}
window.addEventListener('popstate', function(){ var h = location.hash.slice(1); st.step = STEP_IDS.indexOf(h) >= 0 ? h : STEP_IDS[0]; st.sheet = false; renderStrip(); renderLane(); });
function renderBlock(id, html){
  var el = document.getElementById(id); if(!el) return;
  var tmp = document.createElement('div'); tmp.innerHTML = html;
  var neu = tmp.firstElementChild; el.parentNode.replaceChild(neu, el); hydrate(neu);
}
function renderFoot(){
  var el = document.getElementById('foot-meta'); if(!el) return;
  el.innerHTML = '<span class="label">This report</span>' + (INTERNAL ? 'Internal · run ' + D.runs.map(esc).join(', ') + '<br>' : '') + 'Generated ' + esc(D.generated);
}

// images: data URIs are attached as rows render; the browser defers decoding
// offscreen ones (loading=lazy), and printing or a screenshot still gets every picture
function hydrate(root){
  root.querySelectorAll('audio[data-au]').forEach(function(a){ var el = document.getElementById('au-' + a.getAttribute('data-au')); if(el) a.src = 'data:audio/mpeg;base64,' + el.textContent.trim(); else if(D.audioBase) a.src = D.audioBase + a.getAttribute('data-au') + '.mp3'; a.removeAttribute('data-au'); });
  root.querySelectorAll('img[data-img]').forEach(function(im){
    im.loading = 'lazy'; im.decoding = 'async';
    im.src = imgSrc(im.getAttribute('data-img')); im.removeAttribute('data-img');
  });
}
var so = null;
function observeSections(){
  return;
  if(so) so.disconnect();
  so = new IntersectionObserver(function(es){
    var vis = es.filter(function(e){ return e.isIntersecting; }).sort(function(a, b){ return a.boundingClientRect.top - b.boundingClientRect.top; });
    if(vis.length) markTab(vis[0].target.id);
  }, { rootMargin: '-' + (parseInt(getComputedStyle(document.documentElement).getPropertyValue('--chrome')) || 190) + 'px 0px -55% 0px' });
  TABS.forEach(function(t){ var el = document.getElementById(t[0]); if(el) so.observe(el); });
}

// lightbox
function openLb(sid, imgId){
  var s = all().filter(function(x){ return x.id === sid; })[0]; if(!s) return;
  var items = lbItems(s), i = Math.max(0, items.map(function(x){ return x.img; }).indexOf(imgId));
  st.lb = { items: items, i: i }; drawLb();
}
function drawLb(){
  var el = document.getElementById('lb');
  if(!st.lb){ el.innerHTML = ''; document.body.style.overflow = ''; return; }
  var L = st.lb, it = L.items[L.i];
  document.body.style.overflow = 'hidden';
  el.innerHTML = '<div class="lb" role="dialog" aria-modal="true" aria-label="' + esc(it.cap) + '" data-act="lb-close">' +
    '<div class="lb-top" data-stop="1"><span class="cnt">' + (L.i + 1) + ' / ' + L.items.length + '</span>' + (it.href ? '<a href="' + esc(it.href) + '" target="_blank" rel="noopener">open full size ↗</a>' : '<span></span>') +
    '<button class="lb-x" data-act="lb-close" aria-label="Close"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
    '<div class="lb-mid" data-stop="1"><figure><img alt="' + esc(it.cap) + '" src="' + imgSrc(it.img) + '"><figcaption>' + esc(it.cap) + '</figcaption></figure></div>' +
    '<div class="lb-chips" data-stop="1">' + L.items.map(function(x, j){ return '<button class="lbchip' + (j === L.i ? ' on' : '') + '" data-act="lb-jump" data-v="' + j + '">' + esc(x.cap.split(' · ').slice(1).join(' · ')) + '</button>'; }).join('') + '</div></div>';
  fitLb();
  var x = el.querySelector('.lb-x'); if(x) x.focus();
}
function fitLb(){
  var im = document.querySelector('.lb img'); if(!im) return;
  var go = function(){
    var nw = im.naturalWidth, nh = im.naturalHeight; if(!nw || !nh) return;
    var k = Math.min(im.parentNode.clientWidth / nw, window.innerHeight * 0.7 / nh, 2);
    im.style.width = Math.floor(nw * k) + 'px'; im.style.height = Math.floor(nh * k) + 'px';
  };
  if(im.complete) go(); else im.onload = go;
}

// events
document.addEventListener('click', function(e){
  var t = e.target.closest('[data-act]');
  if(st.menu && !(t && (t.getAttribute('data-act') === 'menu' || t.getAttribute('data-act') === 'media'))){ st.menu = false; renderStrip(); }
  if(st.wm.fieldsOpen && !e.target.closest('.dd')){ st.wm.fieldsOpen = false; renderBlock('winmap', renderWinMap()); }
  if(st.wm.sortOpen && !e.target.closest('.dd')){ st.wm.sortOpen = false; renderBlock('winmap', renderWinMap()); }
  if(st.ev.sortOpen && !e.target.closest('.dd')){ st.ev.sortOpen = false; renderBlock('evidence', renderEvidence()); }
  if(st.wm.picksOpen && !e.target.closest('.wm-bar')){ st.wm.picksOpen = false; renderBlock('winmap', renderWinMap()); }
  if(!t) return;
  var act = t.getAttribute('data-act'), v = t.getAttribute('data-v'), k = t.getAttribute('data-k');
  if(act === 'lb-close'){ if(e.target.closest('[data-stop]') && !e.target.closest('.lb-x')) return; st.lb = null; drawLb(); return; }
  switch(act){
    case 'menu': st.menu = !st.menu; renderStrip(); if(st.menu){ var cur0 = document.querySelector('.picker-menu [aria-current]'); if(cur0) cur0.focus(); } break;
    case 'media': st.menu = false; if(v !== st.media){ st.media = v; st.ev.f = {}; st.ev.result = 'all'; renderStrip(); renderLane(); } else renderStrip(); break;
    case 'wm-dim': st.wm.dim = v; renderBlock('winmap', renderWinMap()); break;
    case 'wm-dir': st.wm.dir = v; renderBlock('winmap', renderWinMap()); break;
    case 'wm-view': st.wm.view = v; renderBlock('winmap', renderWinMap()); break;
    case 'wm-field': st.wm.fields[v] = !st.wm.fields[v]; renderBlock('winmap', renderWinMap()); break;
    case 'wm-picks': st.wm.picksOpen = !st.wm.picksOpen; renderBlock('winmap', renderWinMap()); break;
    case 'wm-fields': st.wm.fieldsOpen = !st.wm.fieldsOpen; renderBlock('winmap', renderWinMap()); break;
    case 'wm-sort-open': st.wm.sortOpen = !st.wm.sortOpen; renderBlock('winmap', renderWinMap()); break;
    case 'wm-sort-pick': st.wm.sortOpen = false; st.wm.sort = v; st.wm.dir = defaultDir(v); st.ev.sort = evSortFor(v); renderBlock('winmap', renderWinMap()); renderBlock('evidence', renderEvidence()); break;
    case 'ev-sort-open': st.ev.sortOpen = !st.ev.sortOpen; renderBlock('evidence', renderEvidence()); break;
    case 'ev-sort-pick2': st.ev.sortOpen = false; st.ev.sort = v; renderBlock('evidence', renderEvidence()); break;
    case 'wm-pick': togglePick(k, v); renderBlock('winmap', renderWinMap()); renderBlock('evidence', renderEvidence()); break;
    case 'wm-more': st.wm.more[k] = !st.wm.more[k]; renderBlock('winmap', renderWinMap()); break;
    case 'wm-clear': st.ev.f = {}; renderBlock('winmap', renderWinMap()); renderBlock('evidence', renderEvidence()); break;
    case 'wm-go': goStep('scenarios'); break;
    case 'step': goStep(v); break;
    case 'pick-media': if(v !== st.media){ st.media = v; st.ev.f = {}; st.ev.result = 'all'; } goStep('narrow'); break;
    case 'ev-sort-pick': st.ev.sort = v; renderBlock('evidence', renderEvidence()); renderSheet(); break;
    case 'sheet-open': st.sheet = true; st.sheetAnim = true; renderSheet(); break;
    case 'sheet-close': st.sheet = false; renderSheet(); break;
    case 'sheet-fold': st.shut[k] = t.getAttribute('aria-expanded') === 'true'; renderSheet(); break;
    case 'sheet-more': st.sheetMore[k] = !st.sheetMore[k]; renderSheet(); break;
    case 'ev-unpick': if(k === 'result') st.ev.result = 'all'; else if(k === 'confident') st.ev.confident = false; else if(k === 'q') st.ev.q = ''; else togglePick(k, v); renderBlock('evidence', renderEvidence()); renderSheet(); break;
    case 'ev-dim': if(v === 'all') delete st.ev.f[k]; else togglePick(k, v); renderBlock('evidence', renderEvidence()); renderBlock('winmap', renderWinMap()); renderSheet(); break;
    case 'ev-res': st.ev.result = v; renderBlock('evidence', renderEvidence()); renderSheet(); break;
    case 'ev-conf': st.ev.confident = !st.ev.confident; renderBlock('evidence', renderEvidence()); renderSheet(); break;
    case 'ev-moreopts': st.ev.more = true; st.ev.moreKey = k; renderBlock('evidence', renderEvidence()); break;
    case 'ev-clear': st.ev.f = {}; st.ev.result = 'all'; st.ev.confident = false; st.ev.q = ''; renderSheet(); renderBlock('evidence', renderEvidence()); renderBlock('winmap', renderWinMap()); break;
    case 'ev-expand': st.open = { all: true }; renderBlock('evidence', renderEvidence()); break;
    case 'ev-collapse': st.open = {}; renderBlock('evidence', renderEvidence()); break;
    case 'lb': e.preventDefault(); openLb(t.getAttribute('data-s'), t.getAttribute('data-i')); break;
    case 'lb-jump': st.lb.i = +v; drawLb(); break;
    case 'cl-more': { var tb = t.closest('.clt'), fold = !tb.classList.contains('folded'); tb.classList.toggle('folded', fold); t.textContent = fold ? 'Show ' + plural(+t.getAttribute('data-n'), 'more check') : 'Show fewer checks'; break; }
    case 'cl-ev':
      var box = t.closest('.cl').querySelector('.clt'), on = !box.classList.contains('ev');
      box.classList.toggle('ev', on); t.setAttribute('aria-pressed', String(on)); t.textContent = on ? 'Hide the judge’s evidence' : 'Show the judge’s evidence'; break;
  }
});
document.addEventListener('input', function(e){
  var t = e.target;
  if(t && t.classList && t.classList.contains('evq')){
    var pos = t.selectionStart; st.ev.q = t.value.trim() ? t.value : '';
    renderBlock('evidence', renderEvidence());
    var nq = document.querySelector('.evq'); if(nq){ nq.focus(); nq.value = t.value; try{ nq.setSelectionRange(pos, pos); }catch(_){} }
    return;
  }
  if(!(t && t.classList && t.classList.contains('chipq'))) return;
  var k = t.getAttribute('data-k'), pos = t.selectionStart;
  st.ev.optq[k] = t.value;
  renderBlock('evidence', renderEvidence());
  var n = document.querySelector('.chipq[data-k="' + k + '"]'); if(n){ n.focus(); try{ n.setSelectionRange(pos, pos); }catch(_){} }
});
document.addEventListener('change', function(e){
  var t = e.target, act = t.getAttribute && t.getAttribute('data-act');
  if(act === 'wm-sort'){ st.wm.sort = t.value; st.wm.dir = defaultDir(t.value); st.ev.sort = evSortFor(t.value); renderBlock('winmap', renderWinMap()); renderBlock('evidence', renderEvidence()); }
  else if(act === 'ev-sort'){ st.ev.sort = t.value; renderBlock('evidence', renderEvidence()); }
});
document.addEventListener('toggle', function(e){
  var d = e.target;
  if(d.classList && d.classList.contains('srow')){
    var id = d.getAttribute('data-id'), body = d.querySelector('.sbody');
    if(d.open){ st.open[id] = true; if(!body.innerHTML){ var s = all().filter(function(x){ return x.id === id; })[0]; body.innerHTML = bodyHtml(s); hydrate(body); } }
    else { delete st.open[id]; if(st.open.all){ delete st.open.all; } }
  } else if(d.classList && d.classList.contains('more')){ st.ev.more = d.open; }
}, true);
document.addEventListener('keydown', function(e){
  if(st.sheet && e.key === 'Escape'){ st.sheet = false; renderSheet(); return; }
  if(st.lb){
    if(e.key === 'Escape'){ st.lb = null; drawLb(); }
    else if(e.key === 'ArrowRight'){ st.lb.i = (st.lb.i + 1) % st.lb.items.length; drawLb(); }
    else if(e.key === 'ArrowLeft'){ st.lb.i = (st.lb.i - 1 + st.lb.items.length) % st.lb.items.length; drawLb(); }
  } else if(e.key === 'Escape' && st.menu){ st.menu = false; renderStrip(); var pb = document.querySelector('.picker-btn'); if(pb) pb.focus(); }
  else if(st.menu && (e.key === 'ArrowDown' || e.key === 'ArrowUp')){
    var its = Array.prototype.slice.call(document.querySelectorAll('.picker-menu button')), i = its.indexOf(document.activeElement);
    if(its.length){ e.preventDefault(); its[(i + (e.key === 'ArrowDown' ? 1 : -1) + its.length) % its.length].focus(); }
  }
});
window.addEventListener('beforeprint', function(){ document.querySelectorAll('img[loading=lazy]').forEach(function(im){ im.loading = 'eager'; }); });
window.addEventListener('resize', function(){ setChrome(); fitLb(); });
window.addEventListener('scroll', onScroll, { passive: true });

renderStrip(); renderLane(); renderFoot(); onScroll();
if(STEP_IDS.indexOf(location.hash.slice(1)) < 0) history.replaceState(null, '', location.pathname + location.search + '#' + st.step);
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
