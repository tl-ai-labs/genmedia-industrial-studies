"""Build the gaming image study report: three tier comparisons in one file.

The runner's own `report` command renders the repo's standard dashboard. This
script renders the *client presentation* of the same numbers in the idiom of
studies.adlc.tilicho.in — same tokens, same components (cards with a
title/sub head, pills, seg toggles, .tbl tables, an evidence list with filter
chips, expand/collapse and a lightbox). tools/report-site.css is that design
system rebuilt as portable CSS so the report stays one self-contained file.

Nothing here recomputes a score: scores.jsonl is the source of truth, and a
scenario only enters a mean when BOTH arms scored it (the runner's
--complete-only rule, applied here as well).

    .venv/bin/python tools/build_gaming_report.py --out runs/gaming-tier-study.html
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
CSS = Path(__file__).parent / "report-site.css"

# One lane per tier. The high tier ran as two single-arm runs (the Vertex
# project was suspended mid-study and its arm was re-run on 21 Sep); the judge
# is absolute — one output per call — so runs may be merged without bias.
TIERS = [
    {"key": "high", "label": "High complexity", "short": "High",
     "blurb": "The hardest prompts in the bank: multi-element on-screen displays, "
              "exact strings, and edits that must hold a reference.",
     "runs": ["2026-09-21_043404_image", "2026-09-18_101646_image"]},
    {"key": "medium", "label": "Medium complexity", "short": "Medium",
     "blurb": "Mainstream production work: scene builds, restyles and edits with "
              "a handful of adherence clauses each.",
     "runs": ["2026-09-21_044246_image"]},
    {"key": "low", "label": "Low complexity", "short": "Low",
     "blurb": "Bread-and-butter asks: single-subject renders and simple, "
              "well-bounded edits.",
     "runs": ["2026-09-21_051019_image"]},
]

MAX_PX = 720          # longest edge of an embedded still
JPEG_Q = 72

CRITERION_GLOSS = {
    "prompt_adherence": "did it do what was asked",
    "edit_fidelity": "did the edit land as specified",
    "preservation": "did the rest of the image survive",
    "visual_quality": "sharpness, artefacts, finish",
    "composition": "framing and layout",
    "realism_style": "does it sit in the intended style",
    "text_accuracy": "are the required strings exact",
}


# ---------------------------------------------------------------- data ----
def load_models() -> dict:
    cfg = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text())
    out = {}
    for m in cfg["image"]:
        name = m.get("display", m["id"])
        # display already carries the tier for the OpenAI arms ("GPT Image 2
        # (high)"); only add it when it is missing.
        q = (m.get("params") or {}).get("quality")
        if q and f"({q})" not in name:
            name = f"{name} ({q})"
        out[m["id"]] = {"name": name, "provider": m["provider"],
                        "vendor": "Google" if "gemini" in m["id"] else "OpenAI"}
    return out


def load_scenarios() -> dict:
    out = {}
    for p in (ROOT / "scenarios" / "bank-image-gaming").glob("*.yaml"):
        s = yaml.safe_load(p.read_text())
        out[s["id"]] = s
    return out


def jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def game_of(scn: dict) -> str:
    for t in scn.get("tags", []):
        if t.startswith("game-"):
            return t[5:].upper()
    return "—"


def family_of(scn: dict) -> str:
    """Task family from the test tag: 'test-t2i-4' -> 'T2I', 'test-e-7' -> 'EDIT'."""
    for t in scn.get("tags", []):
        if t.startswith("test-"):
            return "T2I" if "t2i" in t else "EDIT"
    return "—"


def collect(tier: dict, scenarios: dict) -> dict:
    """Merge every run of one tier into a single comparison."""
    scores, telemetry, judges, cells = [], [], [], {}
    for rid in tier["runs"]:
        d = RUNS / rid
        scores += jsonl(d / "scores.jsonl")
        telemetry += jsonl(d / "telemetry.jsonl")
        judges += jsonl(d / "judge.jsonl")
        man = json.loads((d / "manifest.json").read_text())
        for k, v in man["cells"].items():
            cells[k] = {**v, "run": rid}

    arms = sorted({r["model_id"] for r in scores},
                  key=lambda m: 0 if "gemini" in m else 1)
    # latest row wins, matching the runner's own aggregate()
    score_of = {}
    for r in scores:
        score_of[(r["scenario_id"], r["model_id"])] = r
    judge_of = {(r["scenario_id"], r["model_id"]): r for r in judges}

    sids = sorted({r["scenario_id"] for r in scores},
                  key=lambda s: (s.split("-")[1], int(s.split("-")[-1])))

    rows, comparable = [], []
    for sid in sids:
        cellrows = [score_of.get((sid, a)) for a in arms]
        row = {"id": sid, "scn": scenarios.get(sid, {}), "cells": cellrows}
        row["both"] = all(c and c["status"] == "scored" for c in cellrows)
        if row["both"]:
            a, b = (c["score"] for c in cellrows)
            row["winner"] = ("a" if round(a, 2) > round(b, 2)
                             else "b" if round(b, 2) > round(a, 2) else "tie")
        else:
            row["winner"] = None
        rows.append(row)
        if row["both"]:
            comparable.append(row)

    stats = {}
    for i, arm in enumerate(arms):
        vals = [r["cells"][i]["score"] for r in comparable]
        calls = [t for t in telemetry if t["model_id"] == arm]
        ok = [t for t in calls if t["status"] == "ok"]
        micro = sum((t.get("cost") or {}).get("micro_usd", 0) for t in calls)
        invalid = sum(1 for k, v in cells.items()
                      if k.endswith(f"::{arm}") and v["state"] == "invalid")
        stats[arm] = {
            "mean": statistics.mean(vals) if vals else None,
            "n": len(vals),
            "cost_total": micro / 1e6,
            "cost_each": micro / 1e6 / len(ok) if ok else 0,
            "latency": statistics.median([t["latency_ms"] for t in ok]) / 1000 if ok else 0,
            "calls": len(calls), "ok": len(ok), "retries": len(calls) - len(ok),
            "invalid": invalid,
        }

    crit = defaultdict(lambda: defaultdict(list))
    for r in comparable:
        for i, arm in enumerate(arms):
            for name, c in (r["cells"][i].get("criteria") or {}).items():
                crit[name][arm].append(c["score"])
    criteria = {k: {a: statistics.mean(v[a]) for a in arms if v.get(a)}
                for k, v in crit.items()}

    tally = Counter(r["winner"] for r in comparable)
    return {"arms": arms, "rows": rows, "comparable": comparable,
            "stats": stats, "criteria": criteria, "tally": tally,
            "judge_of": judge_of, "cells": cells}


# ------------------------------------------------------------- images ----
_cache: dict[str, str] = {}


def data_uri(path: Path) -> str:
    key = str(path)
    if key in _cache:
        return _cache[key]
    try:
        im = Image.open(path)
        im.thumbnail((MAX_PX, MAX_PX))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_Q, optimize=True)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        uri = ""
    _cache[key] = uri
    return uri


def output_path(sid: str, model: str, runs: list[str]) -> Path | None:
    for rid in runs:
        for ext in ("png", "jpeg", "jpg", "webp"):
            p = RUNS / rid / "outputs" / "image" / sid / f"{model}.{ext}"
            if p.exists():
                return p
    return None


def input_paths(sid: str, runs: list[str]) -> list[tuple[str, Path]]:
    for rid in runs:
        d = RUNS / rid / "inputs" / sid
        if d.exists():
            found = []
            for p in sorted(d.glob("*")):
                if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    role = "reference" if "reference" in p.name else "source"
                    found.append((role, p))
            if found:
                return found
    return []


# -------------------------------------------------------------- render ----
def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def fmt(v, dp=2, dash="—"):
    return dash if v is None else f"{v:.{dp}f}"


def bar_row(label, gloss, a_val, b_val, fmt_fn, higher_better=True):
    if a_val is None or b_val is None:
        return ""
    top = max(a_val, b_val) or 1
    a_pct, b_pct = 100 * a_val / top, 100 * b_val / top
    a_lead = (a_val > b_val) if higher_better else (a_val < b_val)
    b_lead = (b_val > a_val) if higher_better else (b_val < a_val)
    a_cls = "leadpill a" if a_lead else "trail"
    b_cls = "leadpill b" if b_lead else "trail"
    return f"""<div class="mrow"><div class="mhead"><span class="label ink">{e(label)}</span>
<span class="gloss">{e(gloss)}</span></div>
<div class="mgrid"><span class="{a_cls} num">{fmt_fn(a_val)}</span>
<span class="track"><span class="fill a" style="width:{a_pct:.1f}%"></span></span>
<span class="track"><span class="fill b" style="width:{b_pct:.1f}%"></span></span>
<span class="r {b_cls} num">{fmt_fn(b_val)}</span></div></div>"""


def verdict_line(data, names):
    a, b = data["arms"]
    sa, sb = data["stats"][a], data["stats"][b]
    ma, mb = sa["mean"], sb["mean"]
    if ma is None or mb is None:
        return "Not enough comparable scenarios to call this tier."
    gap = abs(ma - mb)
    t = data["tally"]
    if gap < 0.15:
        return (f"Too close to call on quality: {names[a]['name']} {ma:.2f} against "
                f"{names[b]['name']} {mb:.2f} over {sa['n']} shared scenarios "
                f"({t['a']}–{t['tie']}–{t['b']} win–tie–loss). Decide this tier on "
                f"cost and reliability, not score.")
    lead, trail = (a, b) if ma > mb else (b, a)
    w = t["a"] if lead == a else t["b"]
    l = t["b"] if lead == a else t["a"]
    return (f"{names[lead]['name']} leads {names[trail]['name']} by {gap:.2f} points "
            f"({max(ma, mb):.2f} against {min(ma, mb):.2f}) over {sa['n']} shared "
            f"scenarios, winning {w} of them to {l} with {t['tie']} tied.")


def split_table(data, names, dim, getter):
    a, b = data["arms"]
    buckets = defaultdict(list)
    for r in data["comparable"]:
        buckets[getter(r)].append(r)
    body = ""
    for k, rs in sorted(buckets.items()):
        ma = statistics.mean([r["cells"][0]["score"] for r in rs])
        mb = statistics.mean([r["cells"][1]["score"] for r in rs])
        wa = sum(1 for r in rs if r["winner"] == "a")
        wb = sum(1 for r in rs if r["winner"] == "b")
        body += (f"<tr><td class='strong'>{e(str(k).replace('_', ' '))}"
                 f"<span class='sub'>{len(rs)} scenario(s)</span></td>"
                 f"<td class='num bl'>{ma:.2f}</td><td class='num'>{mb:.2f}</td>"
                 f"<td><div class='wonmore'><span class='wbar'>"
                 f"<span class='a' style='width:{100*wa/len(rs):.0f}%'></span>"
                 f"<span class='b' style='width:{100*wb/len(rs):.0f}%'></span></span>"
                 f"{wa}–{len(rs)-wa-wb}–{wb}</div></td></tr>")
    return f"""<div class="card"><div class="cardhead"><div>
<div class="h4">By {e(dim)}</div><p class="sub">win–tie–loss from {e(names[a]['name'])}'s side</p></div></div>
<div class="tblwrap"><table class="tbl"><thead><tr><th>{e(dim.title())}</th>
<th class="right a bl">{e(names[a]['name'])}</th><th class="right b">{e(names[b]['name'])}</th>
<th>Scenario wins</th></tr></thead><tbody>{body}</tbody></table></div></div>"""


def render_tier(tier, data, names) -> str:
    a, b = data["arms"]
    na, nb = names[a]["name"], names[b]["name"]
    sa, sb = data["stats"][a], data["stats"][b]
    t = data["tally"]
    total = sum(t.values()) or 1

    bars = "".join([
        bar_row("Quality", "weighted rubric score, 0–10", sa["mean"], sb["mean"],
                lambda v: f"{v:.2f}"),
        bar_row("Cost per image", "billed from returned usage", sa["cost_each"],
                sb["cost_each"], lambda v: f"${v:.4f}", higher_better=False),
        bar_row("Latency", "median seconds per call", sa["latency"], sb["latency"],
                lambda v: f"{v:.0f}s", higher_better=False),
        bar_row("Clean calls", "share needing no retry",
                100 * sa["ok"] / max(sa["calls"], 1), 100 * sb["ok"] / max(sb["calls"], 1),
                lambda v: f"{v:.0f}%"),
    ])

    crit_rows = ""
    for name, vals in sorted(data["criteria"].items()):
        va, vb = vals.get(a), vals.get(b)
        d = (va - vb) if va is not None and vb is not None else None
        crit_rows += (f"<tr><td class='strong'>{e(name.replace('_', ' '))}"
                      f"<span class='sub'>{e(CRITERION_GLOSS.get(name, ''))}</span></td>"
                      f"<td class='num bl'>{fmt(va)}</td><td class='num'>{fmt(vb)}</td>"
                      f"<td class='num'>{fmt(d)}</td></tr>")

    cards = "".join(render_card(r, tier, data, names) for r in data["rows"])
    games = sorted({game_of(r["scn"]) for r in data["rows"]})
    fams = sorted({family_of(r["scn"]) for r in data["rows"]})
    gchips = "".join(
        f"<button class='chip' data-filter='game' data-value='{e(g)}' aria-pressed='false'>{e(g)}"
        f"<span class='n'>{sum(1 for r in data['rows'] if game_of(r['scn']) == g)}</span></button>"
        for g in games)
    fchips = "".join(
        f"<button class='chip' data-filter='family' data-value='{e(f)}' aria-pressed='false'>{e(f)}"
        f"<span class='n'>{sum(1 for r in data['rows'] if family_of(r['scn']) == f)}</span></button>"
        for f in fams)
    wchips = "".join(
        f"<button class='chip' data-filter='winner' data-value='{v}' aria-pressed='false'>{lbl}"
        f"<span class='n'>{sum(1 for r in data['rows'] if r['winner'] == v)}</span></button>"
        for v, lbl in (("a", e(na)), ("tie", "Tied"), ("b", e(nb))))

    return f"""<section class="sec" id="tier-{tier['key']}" data-lane="{tier['key']}">
<div class="lane"><span class="eyebrow">{e(tier['label'])}</span>
<h2 style="margin-top:8px">{e(na)} vs {e(nb)}</h2>
<p class="sub">{e(tier['blurb'])}</p></div>

<div class="card verdict"><span class="label">Verdict</span>
<p>{e(verdict_line(data, names))}</p>
<div class="overall">{len(data['rows'])} scenarios run · {sa['n']} scored by both arms ·
judged blind, one output per call, temperature 0</div></div>

<div class="grid2">
<div class="card"><div class="cardhead"><div><div class="h4">Head to head</div>
<p class="sub">Four columns, never blended into one.</p></div></div>
<div class="arms"><span class="a">{e(na)}</span><span class="dim small">vs</span>
<span class="b">{e(nb)}</span></div>{bars}
<div class="tally"><span class="label ink">Scenario wins</span>
<div class="tallybar">
<div class="seg-fill a" style="width:{100*t['a']/total:.1f}%">{t['a'] or ''}</div>
<div class="seg-fill tie" style="width:{100*t['tie']/total:.1f}%">{t['tie'] or ''}</div>
<div class="seg-fill b" style="width:{100*t['b']/total:.1f}%">{t['b'] or ''}</div></div>
<div class="legend">
<span class="legend-item"><span class="sw a"></span>{e(na)} {t['a']}</span>
<span class="legend-item"><span class="sw tie"></span>tied {t['tie']}</span>
<span class="legend-item"><span class="sw b"></span>{e(nb)} {t['b']}</span></div></div></div>

<div class="card"><div class="cardhead"><div><div class="h4">By criterion</div>
<p class="sub">Mean over the {sa['n']} shared scenarios.</p></div></div>
<div class="tblwrap"><table class="tbl"><thead><tr><th>Criterion</th>
<th class="right a bl">{e(na)}</th><th class="right b">{e(nb)}</th><th class="right">Δ</th></tr></thead>
<tbody>{crit_rows}</tbody></table></div></div>
</div>

<div class="grid2">{split_table(data, names, 'task family', lambda r: family_of(r['scn']))}
{split_table(data, names, 'game', lambda r: game_of(r['scn']))}</div>

<div class="evhead"><div><div class="h4">Per-scenario evidence</div>
<p class="hint">Every output this tier produced, with the judge's own words.</p></div>
<div class="btns"><button class="btn btn-secondary btn-sm" data-all="1">Expand all</button>
<button class="btn btn-secondary btn-sm" data-all="0">Collapse all</button></div></div>

<div class="card filters"><div class="ftop">
<div><span class="fcount">{len(data['rows'])}</span>
<span class="dim small"> scenarios shown</span>
<button class="clear">Clear filters</button></div></div>
<div class="groups">
<div class="group"><span class="label">Game</span><div class="chips">{gchips}</div></div>
<div class="group"><span class="label">Task family</span><div class="chips">{fchips}</div></div>
<div class="group"><span class="label">Outcome</span><div class="chips">{wchips}</div></div>
</div></div>
<div class="empty card">No scenarios match these filters.</div>
{cards}
</section>"""


def render_card(r, tier, data, names) -> str:
    scn = r["scn"]
    figs = ""
    for role, p in input_paths(r["id"], tier["runs"]):
        uri = data_uri(p)
        if uri:
            figs += (f"<figure class='fig'><div class='figbox src'>"
                     f"<img src='{uri}' alt='{e(role)}' loading='lazy'"
                     f" data-cap='{e(r['id'])} · original input ({e(role)})'></div>"
                     f"<div class='figcap'><span class='who'>input · {e(role)}</span>"
                     f"<span class='pill'>frozen</span></div></figure>")

    for i, cell in enumerate(r["cells"]):
        mid = data["arms"][i]
        p = output_path(r["id"], mid, tier["runs"])
        uri = data_uri(p) if p else ""
        cell = cell or {}
        status = cell.get("status", "missing")
        score = cell.get("score")
        if status == "scored":
            label, pill = f"{score:.2f}", ("pill pill-success" if score >= 9
                                           else "pill pill-warning" if score >= 7
                                           else "pill pill-danger")
        elif status == "invalid":
            label, pill = "invalid · 0", "pill pill-danger"
        elif status == "unjudged":
            label, pill = "no score", "pill"
        else:
            label, pill = status, "pill"
        side = "a" if i == 0 else "b"
        jr = data["judge_of"].get((r["id"], mid), {})
        why = ""
        for cname, c in (cell.get("criteria") or {}).items():
            if c.get("source") == "judge" and c.get("reasoning"):
                why += (f"<li><b>{e(cname.replace('_', ' '))} {c['score']:.0f}</b> — "
                        f"{e(c['reasoning'])}</li>")
        note = jr.get("overall_note", "")
        reason = (data["cells"].get(f"{r['id']}::{mid}") or {}).get("reason", "")
        img = (f"<img src='{uri}' alt='{e(names[mid]['name'])} output' loading='lazy'"
               f" data-cap='{e(r['id'])} · {e(names[mid]['name'])} · {e(label)}'>"
               if uri else f"<div class='fail'>{e(reason or 'no output')}</div>")
        figs += f"""<figure class="fig"><div class="figbox">{img}</div>
<div class="figcap"><span class="who {side}">{e(names[mid]['name'])}</span>
<span class="{pill}">{e(label)}</span></div>
<div class="why">{'<p>' + e(note) + '</p>' if note else ''}
{'<ul>' + why + '</ul>' if why else ''}</div></figure>"""

    game, fam = game_of(scn), family_of(scn)
    task = scn.get("task", "—")
    wpill = {"a": ("pill pill-success", names[data["arms"][0]]["name"]),
             "b": ("pill pill-success", names[data["arms"][1]]["name"]),
             "tie": ("pill pill-warning", "Tied"),
             None: ("pill", "Not compared")}[r["winner"]]
    return f"""<article class="card crow" data-open="0" data-game="{e(game)}"
 data-family="{e(fam)}" data-winner="{e(r['winner'] or 'none')}">
<div class="crowtop">
<button class="rowbtn"><span class="caret">›</span>{e(scn.get('title', r['id']))}</button>
<span class="spacer"></span>
<span class="pill">{e(r['id'])}</span>
<span class="pill">{e(task.replace('_', ' '))}</span>
<span class="pill">{e(game)}</span>
<span class="{wpill[0]}">{e(wpill[1])}</span></div>
<div class="crowbody">
<div class="briefs">
<div class="brief"><b>Brief (verbatim): </b>{e(scn.get('prompt', ''))}</div>
<div class="brief"><b>Expected: </b>{e(scn.get('expected', ''))}</div></div>
<div class="figs">{figs}</div></div></article>"""


SCRIPT = """
document.querySelectorAll('.rowbtn').forEach(b=>b.addEventListener('click',()=>{
  const row=b.closest('.crow'); row.dataset.open = row.dataset.open==='1'?'0':'1';}));
document.querySelectorAll('[data-all]').forEach(b=>b.addEventListener('click',()=>{
  b.closest('.sec').querySelectorAll('.crow').forEach(r=>r.dataset.open=b.dataset.all);}));
document.querySelectorAll('.sec[data-lane]').forEach(sec=>{
  const chips=[...sec.querySelectorAll('.chip[data-filter]')];
  const rows=[...sec.querySelectorAll('.crow')];
  const count=sec.querySelector('.fcount'), clear=sec.querySelector('.clear');
  const empty=sec.querySelector('.empty');
  function apply(){
    const on={};
    chips.filter(c=>c.getAttribute('aria-pressed')==='true').forEach(c=>{
      (on[c.dataset.filter]=on[c.dataset.filter]||[]).push(c.dataset.value);});
    let n=0;
    rows.forEach(r=>{
      const ok=Object.entries(on).every(([k,vals])=>vals.includes(r.dataset[k]));
      r.hidden=!ok; if(ok)n++;});
    count.textContent=n;
    clear.classList.toggle('show',Object.keys(on).length>0);
    empty.classList.toggle('show',n===0);
  }
  chips.forEach(c=>c.addEventListener('click',()=>{
    c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();}));
  clear.addEventListener('click',()=>{
    chips.forEach(c=>c.setAttribute('aria-pressed','false'));apply();});
});
const lb=document.getElementById('lb'), lbimg=lb.querySelector('img'), lbcap=lb.querySelector('.cap');
document.querySelectorAll('.figbox img').forEach(img=>img.addEventListener('click',()=>{
  lbimg.src=img.src; lbcap.textContent=img.dataset.cap||''; lb.classList.add('show');}));
lb.addEventListener('click',()=>lb.classList.remove('show'));
document.addEventListener('keydown',ev=>{if(ev.key==='Escape')lb.classList.remove('show');});
"""


def render(tiers_data, names, meta) -> str:
    lanes = "".join(t["html"] for t in tiers_data)
    nav = "".join(f"<a class='seg-item' href='#tier-{t['key']}'>{e(t['short'])}</a>"
                  for t in tiers_data)
    cost_rows = ""
    for t in tiers_data:
        for arm in t["data"]["arms"]:
            s = t["data"]["stats"][arm]
            cost_rows += (f"<tr><td class='strong'>{e(names[arm]['name'])}"
                          f"<span class='sub'>{e(t['label'].lower())} · {e(names[arm]['vendor'])}</span></td>"
                          f"<td class='num bl'>{s['ok']}</td>"
                          f"<td class='num'>${s['cost_each']:.4f}</td>"
                          f"<td class='num'>${s['cost_total']:.2f}</td>"
                          f"<td class='num'>{s['latency']:.0f}s</td>"
                          f"<td class='num'>{s['retries']}</td>"
                          f"<td class='num'>{s['invalid']}</td></tr>")
    stats = "".join(f"""<div class="card stat"><div class="statv">{v}</div>
<div class="statsub">{e(k)}</div></div>""" for k, v in meta["stats"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gaming image study</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>{CSS.read_text()}</style>
</head>
<body>
<div class="topbar"><div class="wrap">Tilicho Labs · Studies<span>GenMedia · image lane</span></div></div>

<header class="pagehead"><div class="wrap">
<span class="eyebrow">Voice, Image and Video Model Comparison</span>
<h1 style="margin-top:10px">Gaming image study</h1>
<p class="lede">Three tiers, three head-to-heads: the premium, mainstream and entry arms of
Gemini and GPT Image, run on the same gaming scenarios and judged blind. Quality, cost,
latency and reliability stay four separate columns.</p>
<div class="stats">{stats}</div>
<div class="seg" style="margin-bottom:24px">{nav}</div>
</div></header>

<main><div class="wrap">
<div class="caveat"><b>How the numbers were made.</b> Every scenario went to both arms
unchanged. Code gates each output first — does it decode, is it the right size, is it blank,
does OCR read the required text; a failed gate makes the cell invalid (scored 0) and the judge
is never called for it. What code cannot measure, a blind judge scores at temperature 0, one
output per call, with model names and order stripped. A mean counts a scenario only when both
arms scored it; anything one arm failed stays visible in the reliability columns.</div>
{lanes}

<section class="sec"><div class="lane"><span class="eyebrow">Spend</span>
<h2 style="margin-top:8px">What this study cost</h2>
<p class="sub">Generation only, billed from returned usage. Judging is counted separately and
came to ${meta['judge_cost']:.2f} across all six arms.</p></div>
<div class="card" style="margin-top:16px"><div class="tblwrap"><table class="tbl"><thead><tr>
<th>Arm</th><th class="right bl">Images</th><th class="right">Per image</th>
<th class="right">Total</th><th class="right">Median latency</th>
<th class="right">Retries</th><th class="right">Invalid</th></tr></thead>
<tbody>{cost_rows}</tbody></table></div></div></section>

<section class="sec"><div class="lane"><span class="eyebrow">Method</span>
<h2 style="margin-top:8px">What this method guarantees</h2></div>
<div class="grid2">
<div class="card"><div class="cardhead"><div><div class="h4">Guaranteed</div></div></div>
<div class="cardbody"><ul class="notes tight">
<li>Identical inputs to both arms, frozen and fingerprinted</li>
<li>Blind judging — names, order and metadata all stripped</li>
<li>Weights sum to 1.0, enforced by the loader</li>
<li>A failed gate is recorded and scored 0, never quietly dropped</li>
<li>Quality, cost, latency and reliability reported separately</li>
<li>Every score traceable to a rubric hash and a judge prompt hash</li>
</ul></div></div>
<div class="card"><div class="cardhead"><div><div class="h4">Not guaranteed</div></div></div>
<div class="cardbody"><ul class="notes tight">
<li>The judge is a Google model, and half the arms it scores are also Google's</li>
<li>The judge has not been calibrated against human raters</li>
<li>One generation per scenario, not three seeds</li>
<li>Retry counts reflect this project's Vertex quota, not model reliability</li>
<li>The low tier's gap sits inside the run-to-run spread</li>
</ul></div></div></div>
<ul class="notes" style="margin-top:16px">
<li><b>Only an identical score is a tie.</b> Scores are compared at two decimals.</li>
<li><b>A cheaper arm that loses by a tenth of a point is usually the better buy.</b>
This report will not make that trade for you.</li>
</ul></section>
</div></main>

<footer class="wrap dim small" style="padding-block:24px 48px">{meta['footer']}</footer>
<div class="lb" id="lb"><button class="x" aria-label="Close">×</button><img alt=""><div class="cap"></div></div>
<script>{SCRIPT}</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(RUNS / "gaming-tier-study-client.html"))
    args = ap.parse_args()

    names, scenarios = load_models(), load_scenarios()
    tiers_data, judge_cost, run_ids, gen_cost = [], 0.0, [], 0.0
    for tier in TIERS:
        data = collect(tier, scenarios)
        tiers_data.append({**tier, "data": data,
                           "html": render_tier(tier, data, names)})
        gen_cost += sum(s["cost_total"] for s in data["stats"].values())
        for rid in tier["runs"]:
            run_ids.append(rid)
            for j in jsonl(RUNS / rid / "judge.jsonl"):
                judge_cost += (j.get("cost") or {}).get("micro_usd", 0) / 1e6

    n_scen = sum(len(t["data"]["rows"]) for t in tiers_data)
    n_out = sum(s["ok"] for t in tiers_data for s in t["data"]["stats"].values())
    meta = {
        "judge_cost": judge_cost,
        "stats": [("scenarios judged", n_scen), ("outputs generated", n_out),
                  ("head-to-heads", len(tiers_data)),
                  ("generation spend", f"${gen_cost:.2f}")],
        "footer": "Runs: " + ", ".join(run_ids),
    }

    out = Path(args.out)
    out.write_text(render(tiers_data, names, meta), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
