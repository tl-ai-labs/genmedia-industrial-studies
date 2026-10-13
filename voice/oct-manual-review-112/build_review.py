"""Build the manual review page for the October voice runs: every scenario done in BOTH rounds
(oct-p1 and oct-p2), Gemini on the left and ElevenLabs on the right, round 1 and round 2 of each.

Reads only (nothing in runs/ is written): the order file, scenario YAMLs, each run's manifest,
checks.jsonl, judge.jsonl and transcripts, and the listening list. Audio is linked, not copied.

    cd voice && .venv/bin/python oct-manual-review-112/build_review.py
writes oct-manual-review-112/index.html. Delete the folder to remove everything.
"""
import glob
import html
import json
import re
import statistics
from datetime import datetime
from pathlib import Path

import yaml

VOICE = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
ORDER = VOICE.parent.parent / "oct-voice-run-tools" / "oct-order-200.txt"
LISTEN = VOICE / "runs" / "oct-2026-listen-list.md"
MODELS = [("gemini-3-8-flash-tts", "Gemini 3.8 Flash TTS", "g"), ("elevenlabs-v4", "ElevenLabs Eleven v4", "e")]
ROUNDS = [("oct-p1", "Round 1"), ("oct-p2", "Round 2")]
LANG = {"english": "English", "hinglish": "Hinglish", "hindi": "Hindi", "telugu": "Telugu", "tenglish": "Tenglish"}
E = lambda s: html.escape(str(s if s is not None else ""))


def jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def latest(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        out[r["model_id"]] = r
    return out


def run_dir(label: str, sid: str) -> Path | None:
    d = sorted(glob.glob(str(VOICE / "runs" / f"*_voice-{label}-{sid}")))
    return Path(d[-1]) if d else None


def gate_label(g: str) -> str:
    base = {"decodes": "Audio decodes", "duration_in_range": "Length within limits", "not_silent": "Not silent",
            "silence_within_bounds": "No long silences", "no_clipping": "No clipping",
            "loudness_sane": "Loudness normal", "wer_within_max": "Word error rate within limit"}
    if g in base:
        return base[g]
    m = re.match(r"(must_say_any|must_say|must_not_say)\[(.*)\]?$", g)
    if m:
        kind = {"must_say": "Must say", "must_not_say": "Must not say", "must_say_any": "Must say one of"}[m.group(1)]
        return f'{kind}: "{m.group(2).rstrip("]")}"'
    return g.replace("_", " ")


def listening_items() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not LISTEN.exists():
        return out
    seen = set()
    for line in LISTEN.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not cells[0].isdigit() or cells[0] in seen:
            continue
        seen.add(cells[0])
        sid = cells[1].split()[0]
        out.setdefault(sid, []).append({"n": cells[0], "where": cells[1], "model": cells[2],
                                        "note": " | ".join(cells[4:-1]), "result": cells[-1]})
    return out


def clip(label: str, sid: str, mid: str) -> dict:
    d = run_dir(label, sid)
    if not d:
        return {"missing": True}
    c = latest(jsonl(d / "checks.jsonl")).get(mid)
    j = latest(jsonl(d / "judge.jsonl")).get(mid)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    vm = next((m.get("voice_map", {}) for m in man.get("models", []) if m.get("id") == mid), {})
    o = d / "outputs" / "voice" / sid
    read = lambda p: p.read_text(encoding="utf-8").strip() if p.exists() else None
    return {"run": d.name, "check": c, "judge": j, "voice_map": vm,
            "audio": f"../runs/{d.name}/outputs/voice/{sid}/{mid}.wav",
            "has_audio": (o / f"{mid}.wav").exists(),
            "normal": read(o / f"{mid}.txt"), "spelled": read(o / f"{mid}.spelled.txt")}


def passed(c: dict) -> bool | None:
    return None if not c.get("check") else bool(c["check"]["passed"])


# ---------------------------------------------------------------- html pieces

def judge_html(j: dict | None, pass_: bool | None) -> str:
    if not j or j.get("status") != "judged" or not j.get("scores"):
        why = "not judged" + (" - the clip failed a length or audio check" if pass_ is False else "")
        if j and j.get("status") not in (None, "judged"):
            why = f"not judged ({E(j.get('status'))})"
        return f'<div class="blk"><div class="bh"><span class="eb">Judge scores</span><span class="muted">{why}</span></div></div>'
    sc, rs = j["scores"], j.get("reasoning") or {}
    rows = "".join(
        f'<div class="crit"><span>{E(k.replace("_", " "))}</span><span class="bar"><i style="width:{float(v) * 10:.0f}%"></i></span><b>{float(v):g}</b></div>'
        for k, v in sc.items())
    why = "".join(f'<div class="wr"><b>{E(k.replace("_", " "))} {float(sc.get(k, 0)):g}</b><p>{E(t)}</p></div>' for k, t in rs.items())
    note = f'<p class="jsum">{E(j["overall_note"])}</p>' if j.get("overall_note") else ""
    mean = statistics.mean(float(v) for v in sc.values())
    return f"""<div class="blk"><div class="bh"><span class="eb">Judge scores</span><span class="bn">{mean:.2f}<small> mean of {len(sc)}</small></span></div>
  {rows}<details class="why"><summary>Judge reasoning</summary>{note}{why}</details></div>"""


def checks_html(c: dict) -> str:
    gates = c["gates"]
    bad = [g for g in gates if not g["passed"]]
    items = "".join(
        f'<li class="{"ok" if g["passed"] else "bad"}"><span class="ico">{"✓" if g["passed"] else "✕"}</span>'
        f'<span>{E(gate_label(g["gate"]))}<small>{E(g.get("detail", ""))}</small></span></li>'
        for g in sorted(gates, key=lambda g: g["passed"]))
    head = (f'<span class="bn bad">{len(bad)} failed</span>' if bad else '<span class="bn ok">all passed</span>')
    return f"""<div class="blk"><div class="bh"><span class="eb">Automatic checks · {len(gates) - len(bad)} of {len(gates)}</span>{head}</div>
  <details class="why"{' open' if bad else ''}><summary>Every check</summary><ul class="cl">{items}</ul></details></div>"""


def clip_html(sid: str, mid: str, short: str, rnd: int, rname: str, k: dict, other: dict) -> str:
    key = f"{sid}|{short}{rnd}"
    if k.get("missing") or not k.get("check"):
        return (f'<div class="clip"><div class="ch"><span class="rn">{rname}</span><span class="muted">no result</span></div></div>', "")
    c = k["check"]
    m = c.get("measurements", {})
    p = c["passed"]
    w = (c.get("wer") or {})
    wer = m.get("normalized_wer")
    stat = '<span class="st ok">PASS</span>' if p else f'<span class="st bad">FAIL</span> <small class="fg">{E(", ".join(gate_label(g) for g in c["failed_gates"]))}</small>'
    op = other.get("check") and other["check"]["passed"]
    changed = (other.get("check") is not None) and op != p
    facts = [f"length <b>{m.get('duration_s')} s</b>",
             f"first audio <b>{m.get('ttfa_ms')} ms</b>" if m.get("ttfa_ms") else None,
             f"word error <b>{wer * 100:.2f}%</b>" if isinstance(wer, (int, float)) else "word error <b>not scored</b>",
             f"loudness <b>{m.get('rms_dbfs')} dBFS</b>" if m.get("rms_dbfs") is not None else None,
             f"pace <b>{m.get('speech_rate_wpm')} wpm</b>" if m.get("speech_rate_wpm") else None]
    vm = k.get("voice_map") or {}
    voice = ", ".join(f"{a} → {b}" for a, b in vm.items()) if vm else ""
    tr = ""
    if w.get("normalized_transcript") and w.get("normalized_script"):
        tr += f'<div class="tx"><span class="eb">Transcript, as scored (cleaned up)</span><p>{E(w["normalized_transcript"])}</p></div>'
    tr += f'<div class="tx"><span class="eb">Transcript (transcriber, normal)</span><p>{E(k["normal"]) if k["normal"] else "<span class=muted>none saved</span>"}</p></div>'
    if k["spelled"]:
        tr += f'<div class="tx"><span class="eb">Spelled-out second transcription</span><p>{E(k["spelled"])}</p></div>'
    audio = (f'<audio controls preload="none" src="{E(k["audio"])}"></audio>' if k["has_audio"]
             else '<p class="muted">audio file not found</p>')
    top = f"""<div class="clip{' changed' if changed else ''}" data-key="{E(key)}">
  <div class="ch"><span class="rn">{rname}</span>{stat}{'<span class="pill warn">differs from the other round</span>' if changed else ''}</div>
  {audio}
  <div class="facts">{' · '.join(f for f in facts if f)}</div>
  <div class="rev"><span class="eb">Your verdict</span>
    <label><input type="radio" name="{E(key)}" value="ok"> sounds right</label>
    <label><input type="radio" name="{E(key)}" value="issue"> issue</label>
    <label><input type="radio" name="{E(key)}" value="unsure"> unsure</label>
    <label><input type="radio" name="{E(key)}" value=""> clear</label>
  </div>
</div>"""
    detail = f"""<details class="rd"{' open' if not p else ''}><summary>{rname} details - checks, judge, transcripts{' (failed)' if not p else ''}</summary>
  {f'<div class="muted small">voice: {E(voice)} · run {E(k["run"])}</div>' if voice else f'<div class="muted small">run {E(k["run"])}</div>'}
  {checks_html(c)}
  {judge_html(k.get("judge"), p)}
  <details class="why"><summary>Transcripts</summary>{tr}</details>
</details>"""
    return top, detail


def build() -> Path:
    order = [l.strip() for l in ORDER.read_text().splitlines() if l.strip()]
    items = [x for x in order if run_dir("oct-p1", x.split("/")[1]) and run_dir("oct-p2", x.split("/")[1])]
    listen = listening_items()
    data, cards = [], []
    for i, item in enumerate(items):
        folder, sid = item.split("/")
        sc = yaml.safe_load((VOICE / "scenarios-oct-2026" / folder / f"{sid}.yaml").read_text(encoding="utf-8"))
        res = {mid: [clip(lbl, sid, mid) for lbl, _ in ROUNDS] for mid, _, _ in MODELS}
        st = {f"{s}{n + 1}": passed(res[mid][n]) for mid, _, s in MODELS for n in range(2)}
        data.append({"sid": sid, "lang": LANG[folder], "res": res, "st": st})
        li = listen.get(sid, [])
        chk = sc.get("checks") or {}
        conf = []
        if chk.get("duration_s"):
            conf.append(f"length {chk['duration_s'].get('min')}-{chk['duration_s'].get('max')} s")
        if "max_wer" in chk:
            conf.append(f"word error at most {chk['max_wer'] * 100:g}%")
        if chk.get("asr_scored") is False:
            conf.append("word error not scored (non-English)")
        for k2, lab in (("must_say", "must say"), ("must_not_say", "must not say"), ("must_say_any", "must say one of")):
            if chk.get(k2):
                conf.append(f"{lab}: " + "; ".join(json.dumps(v, ensure_ascii=False) for v in chk[k2]))
        if chk.get("asr_hotwords"):
            conf.append("transcriber hints: " + ", ".join(chk["asr_hotwords"]))
        speakers = (sc.get("params") or {}).get("speakers")
        voice = ("; ".join(f"{s['label']}: {s['voice']}" + (f" ({s['style']})" if s.get("style") else "") for s in speakers)
                 if speakers else (sc.get("params") or {}).get("voice", ""))
        dots = "".join(
            f'<span class="dot {"ok" if st[f"{s}{n}"] else "bad" if st[f"{s}{n}"] is False else "na"} {s}" title="{name} round {n}">{"GE"[j]}{n}</span>'
            for j, (mid, name, s) in enumerate(MODELS) for n in (1, 2))
        anyfail = any(v is False for v in st.values())
        diff = st["g1"] != st["g2"] or st["e1"] != st["e2"]
        cols = []
        for mid, name, s in MODELS:
            r1, r2 = res[mid]
            (t1, d1), (t2, d2) = clip_html(sid, mid, s, 1, 'Round 1', r1, r2), clip_html(sid, mid, s, 2, 'Round 2', r2, r1)
            cols.append(f"""<div class="col {s}"><div class="colh"><span class="sys {s}">{E(name)}</span></div>
  {t1}{t2}{d1}{d2}</div>""")
        lis = "".join(
            f'<li><b>Item {E(x["n"])}</b> · {E(x["where"])} · {E(x["model"])}<br>{E(x["note"])}<br><span class="muted">Automatic result: {E(x["result"])}</span></li>'
            for x in li)
        cards.append(f"""<details class="card" id="{E(sid)}" data-i="{i}" data-lang="{E(LANG[folder])}" data-fail="{int(anyfail)}" data-diff="{int(diff)}" data-listen="{int(bool(li))}" data-text="{E((sid + ' ' + sc.get('title', '')).lower())}">
<summary><span class="num">{i + 1})</span><span class="sid">{E(sid)}</span><span class="tt">{E(sc.get('title', ''))}</span>
  <span class="pill">{E(LANG[folder])}</span>{f'<span class="pill warn">listening list ×{len(li)}</span>' if li else ''}
  <span class="dots">{dots}</span><span class="revd" data-sid="{E(sid)}"></span></summary>
<div class="body">
  <div class="brief">
    <div><span class="eb">Script</span><pre>{E(sc['input']['script'].strip())}</pre>
      {f'<p><b>Delivery:</b> {E(sc["input"]["style"])}</p>' if sc['input'].get('style') else ''}
      <p><b>Voice:</b> {E(voice)} · <b>Language:</b> {E(sc['input'].get('language'))} · <b>Task:</b> {E(sc.get('task', '').replace('_', ' '))}</p></div>
    <div><span class="eb">What a good read does</span><pre class="exp">{E((sc.get('expected') or '').strip())}</pre>
      <p class="small"><b>Checks:</b> {E(' · '.join(conf))}</p></div>
  </div>
  {f'<div class="listen"><span class="eb">On the listening list</span><ul>{lis}</ul></div>' if li else ''}
  <div class="pair">{''.join(cols)}</div>
  <div class="notes"><span class="eb">Your notes on this scenario</span><textarea data-sid="{E(sid)}" placeholder="What you heard, which model read it better, anything to fix…"></textarea></div>
</div></details>""")

    # ---------------- summary
    def stats(rows, mid, n):
        cs = [r["res"][mid][n].get("check") for r in rows]
        cs = [c for c in cs if c]
        t = [c["measurements"].get("ttfa_ms") for c in cs if c["measurements"].get("ttfa_ms")]
        js = [r["res"][mid][n].get("judge") for r in rows]
        js = [statistics.mean(float(v) for v in j["scores"].values()) for j in js if j and j.get("status") == "judged" and j.get("scores")]
        return {"p": sum(c["passed"] for c in cs), "n": len(cs), "ttfa": statistics.median(t) / 1000 if t else None,
                "judge": statistics.mean(js) if js else None, "jn": len(js)}

    groups = [("All", data)] + [(L, [d for d in data if d["lang"] == L]) for L in LANG.values() if any(d["lang"] == L for d in data)]
    trs = []
    for gname, rows in groups:
        cells = []
        for mid, _, s in MODELS:
            for n in range(2):
                x = stats(rows, mid, n)
                cells.append(f'<td class="{s}"><b>{x["p"]}</b> / {x["n"]}</td>')
        trs.append(f'<tr><th>{E(gname)}</th>{"".join(cells)}</tr>')
    for lab, key, fmt in (("Median time to first audio", "ttfa", lambda v: f"{v:.2f} s"),
                          ("Judge, mean score (judged clips)", "judge", lambda v: f"{v:.2f}")):
        cells = []
        for mid, _, s in MODELS:
            for n in range(2):
                x = stats(data, mid, n)
                v = x[key]
                cells.append(f'<td class="{s}">{fmt(v) if v is not None else "—"}{f"<small> ({x['jn']} clips)</small>" if key == "judge" else ""}</td>')
        trs.append(f'<tr class="sub"><th>{lab}</th>{"".join(cells)}</tr>')
    langs = sorted({d["lang"] for d in data}, key=list(LANG.values()).index)
    chips = "".join(f'<button class="chip" data-f="lang" data-v="{E(L)}">{E(L)} <span>{sum(d["lang"] == L for d in data)}</span></button>' for L in langs)
    nfail = sum(any(v is False for v in d["st"].values()) for d in data)
    ndiff = sum(d["st"]["g1"] != d["st"]["g2"] or d["st"]["e1"] != d["st"]["e2"] for d in data)
    nlist = sum(bool(listen.get(d["sid"])) for d in data)
    page = TEMPLATE.format(
        css=CSS, js=JS, n=len(data), cards="".join(cards), rows="".join(trs), chips=chips,
        nfail=nfail, ndiff=ndiff, nlist=nlist,
        generated=datetime.now().strftime("%d %b %Y, %H:%M"))
    out = HERE / "index.html"
    out.write_text(page, encoding="utf-8")
    return out


CSS = r"""
:root{--paper:#f6f8f8;--card:#fff;--ink:#171d20;--muted:#5a686d;--faint:#8a979c;--rule:#e1e8e8;
 --g:#0c7d6a;--g-soft:#e3f2ee;--e:#4a5b8c;--e-soft:#e8ebf5;--ok:#1d7a45;--bad:#b3352a;--bad-soft:#f9e9e7;
 --warn:#96590f;--warn-soft:#fbf0dd;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#101617;--card:#171f21;--ink:#e5ecec;--muted:#9fb0b3;
 --faint:#6f8085;--rule:#263235;--g:#43c0a8;--g-soft:#12312b;--e:#93a4d6;--e-soft:#1c2236;--ok:#5cc98a;--bad:#e58d82;
 --bad-soft:#301d1a;--warn:#dfa856;--warn-soft:#2c2214}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 var(--sans)}
.wrap{max-width:1280px;margin:0 auto;padding:0 16px 80px}
header{padding:28px 0 6px}h1{font-size:clamp(22px,3vw,30px);margin:0 0 6px;letter-spacing:-.01em}
.lead{color:var(--muted);max-width:900px;margin:4px 0}.small{font-size:12.5px}.muted{color:var(--muted)}
.eb{display:block;font:600 10.5px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-bottom:4px}
.notice{background:var(--warn-soft);border:1px solid var(--rule);border-radius:10px;padding:10px 14px;margin:12px 0;font-size:13.5px}
.notice li{margin:2px 0}
table.sum{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--rule);border-radius:12px;overflow:hidden;margin:14px 0;font-size:14px}
.sumwrap{overflow-x:auto}
table.sum th,table.sum td{padding:7px 10px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}
table.sum th:first-child{text-align:left}table.sum thead th{font:600 11px var(--mono);text-transform:uppercase;color:var(--muted)}
table.sum td.g b{color:var(--g)}table.sum td.e b{color:var(--e)}table.sum tr.sub td,table.sum tr.sub th{font-size:13px;color:var(--muted)}
.bar-top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--paper) 92%,transparent);backdrop-filter:blur(8px);
 padding:10px 0;border-bottom:1px solid var(--rule);display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip,.btn{border:1px solid var(--rule);background:var(--card);color:var(--ink);border-radius:20px;padding:4px 11px;font-size:13px;cursor:pointer}
.chip span{color:var(--faint);font-size:11.5px}.chip.on{background:var(--g-soft);border-color:var(--g);color:var(--g)}
input[type=search]{border:1px solid var(--rule);background:var(--card);color:var(--ink);border-radius:20px;padding:5px 12px;font-size:13px;min-width:180px}
#shown{font:12px var(--mono);color:var(--muted);margin-left:auto}
details.card{background:var(--card);border:1px solid var(--rule);border-radius:12px;margin:10px 0}
details.card>summary{list-style:none;cursor:pointer;padding:11px 14px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
details.card>summary::-webkit-details-marker{display:none}
.num{font:600 12.5px var(--mono);color:var(--faint);min-width:34px;text-align:right}.sid{font:600 12.5px var(--mono);color:var(--muted);min-width:96px}.tt{font-weight:600;flex:1 1 300px}
.pill{font-size:11.5px;border:1px solid var(--rule);border-radius:12px;padding:1px 8px;color:var(--muted)}
.pill.warn{background:var(--warn-soft);color:var(--warn);border-color:transparent}
.dots{display:flex;gap:4px}.dot{font:600 10.5px var(--mono);padding:2px 5px;border-radius:5px;border:1px solid var(--rule)}
.dot.ok.g{color:var(--g)}.dot.ok.e{color:var(--e)}.dot.bad{background:var(--bad-soft);color:var(--bad);border-color:var(--bad)}
.dot.na{color:var(--faint)}.revd{font:11px var(--mono);color:var(--g)}
.body{padding:0 14px 14px;border-top:1px solid var(--rule)}
.brief{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:12px 0}
pre{white-space:pre-wrap;font:13.5px/1.5 var(--sans);background:var(--paper);border-radius:8px;padding:8px 10px;margin:0}
pre.exp{font-size:12.5px;color:var(--muted)}.brief p{margin:6px 0;font-size:13.5px}
.listen{background:var(--warn-soft);border-radius:10px;padding:8px 12px;margin:8px 0;font-size:13px}.listen ul{margin:4px 0;padding-left:18px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.col{border-radius:10px;padding:8px;border-top:4px solid}.col.g{border-color:var(--g);background:var(--g-soft)}.col.e{border-color:var(--e);background:var(--e-soft)}
.colh{padding:2px 4px 8px}.sys{font-weight:700}.sys.g{color:var(--g)}.sys.e{color:var(--e)}
.clip{background:var(--card);border:1px solid var(--rule);border-radius:9px;padding:10px;margin-bottom:10px}
details.rd{background:var(--card);border:1px solid var(--rule);border-radius:9px;padding:8px 10px;margin-bottom:8px;font-size:13px}\ndetails.rd>summary{cursor:pointer;font-weight:600;font-size:12.5px}\n.clip.changed{box-shadow:0 0 0 2px var(--warn) inset}
.ch{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}.rn{font-weight:700}
.st{font:700 11px var(--mono);padding:2px 7px;border-radius:5px}.st.ok{color:var(--ok);border:1px solid var(--ok)}
.st.bad{color:#fff;background:var(--bad)}.fg{color:var(--bad)}
audio{width:100%;height:36px;margin:2px 0 4px}
.facts{font-size:12.5px;color:var(--muted)}.facts b{color:var(--ink);font-weight:600}
.blk{margin-top:8px}.bh{display:flex;justify-content:space-between;align-items:baseline}
.bn{font-weight:700}.bn small{font-weight:400;color:var(--muted);font-size:11px}.bn.ok{color:var(--ok);font-size:12px}.bn.bad{color:var(--bad);font-size:12px}
.crit{display:grid;grid-template-columns:110px 1fr 34px;gap:8px;align-items:center;font-size:12.5px}
.bar{height:6px;background:var(--rule);border-radius:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--muted)}
.g .bar i{background:var(--g)}.e .bar i{background:var(--e)}.crit b{text-align:right}
details.why{margin-top:6px;font-size:12.5px}details.why>summary{cursor:pointer;color:var(--muted)}
.wr{margin:6px 0}.wr p,.jsum{margin:2px 0;color:var(--muted)}
ul.cl{list-style:none;padding:0;margin:4px 0}ul.cl li{display:flex;gap:6px;margin:3px 0}
ul.cl small{display:block;color:var(--muted);font-family:var(--mono);font-size:11px;word-break:break-word}
ul.cl li.bad{color:var(--bad)}.ico{width:14px;flex:none}
.tx p{margin:2px 0 8px;font-size:13px}
.rev{margin-top:8px;padding-top:8px;border-top:1px dashed var(--rule);font-size:12.5px;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center}
.rev .eb{margin:0}
.notes{margin-top:10px}textarea{width:100%;min-height:60px;border:1px solid var(--rule);border-radius:8px;background:var(--card);color:var(--ink);padding:8px;font:13.5px var(--sans)}
@media (max-width:820px){.pair,.brief{grid-template-columns:1fr}}
"""

JS = r"""
const KEY='oct-manual-review-112';
const store={get(){try{return JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){return {}}},
  set(v){try{localStorage.setItem(KEY,JSON.stringify(v))}catch(e){}}};
let S=store.get(); S.v=S.v||{}; S.n=S.n||{};
function paint(){
  document.querySelectorAll('.rev input').forEach(i=>{i.checked=(S.v[i.name]||'')===i.value&&i.value!==''});
  document.querySelectorAll('textarea[data-sid]').forEach(t=>{t.value=S.n[t.dataset.sid]||''});
  document.querySelectorAll('.revd').forEach(r=>{const sid=r.dataset.sid;
    const k=Object.keys(S.v).filter(x=>x.startsWith(sid+'|')&&S.v[x]);const iss=k.filter(x=>S.v[x]==='issue').length;
    r.textContent=k.length?`reviewed ${k.length}/4${iss?' · '+iss+' issue':''}`:'';});
  const done=new Set(Object.keys(S.v).filter(k=>S.v[k]).map(k=>k.split('|')[0]));
  document.getElementById('progress').textContent=`${done.size} of ${document.querySelectorAll('details.card').length} scenarios have a verdict`;
}
document.addEventListener('change',e=>{if(e.target.matches('.rev input')){S.v[e.target.name]=e.target.value;store.set(S);paint();filter()}});
document.addEventListener('input',e=>{if(e.target.matches('textarea[data-sid]')){S.n[e.target.dataset.sid]=e.target.value;store.set(S)}});
const F={lang:'',view:''};
function filter(){
  const q=(document.getElementById('q').value||'').toLowerCase();let n=0;
  document.querySelectorAll('details.card').forEach(c=>{
    let ok=(!F.lang||c.dataset.lang===F.lang)&&(!q||c.dataset.text.includes(q));
    if(F.view==='fail')ok=ok&&c.dataset.fail==='1';
    if(F.view==='diff')ok=ok&&c.dataset.diff==='1';
    if(F.view==='listen')ok=ok&&c.dataset.listen==='1';
    if(F.view==='todo')ok=ok&&!Object.keys(S.v).some(k=>k.startsWith(c.id+'|')&&S.v[k]);
    if(F.view==='issue')ok=ok&&Object.keys(S.v).some(k=>k.startsWith(c.id+'|')&&S.v[k]==='issue');
    c.style.display=ok?'':'none';n+=ok;});
  document.getElementById('shown').textContent=`${n} shown`;
}
document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click',()=>{
  const f=b.dataset.f,v=b.dataset.v;F[f]=(F[f]===v?'':v);
  document.querySelectorAll(`.chip[data-f="${f}"]`).forEach(x=>x.classList.toggle('on',x.dataset.v===F[f]&&F[f]!==''));filter();}));
document.getElementById('q').addEventListener('input',filter);
document.getElementById('openall').onclick=()=>document.querySelectorAll('details.card').forEach(d=>{if(d.style.display!=='none')d.open=true});
document.getElementById('closeall').onclick=()=>document.querySelectorAll('details.card').forEach(d=>d.open=false);
document.getElementById('export').onclick=()=>{
  const rows=[];document.querySelectorAll('details.card').forEach(c=>{const sid=c.id;
    const v={};['g1','g2','e1','e2'].forEach(k=>{v[k]=S.v[sid+'|'+k]||''});
    if(Object.values(v).some(x=>x)||S.n[sid])rows.push({scenario:sid,gemini_round1:v.g1,gemini_round2:v.g2,elevenlabs_round1:v.e1,elevenlabs_round2:v.e2,notes:S.n[sid]||''});});
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({exported:new Date().toISOString(),reviews:rows},null,2)],{type:'application/json'}));
  a.download='oct-manual-review-112.json';a.click();};
document.getElementById('import').onchange=e=>{const f=e.target.files[0];if(!f)return;f.text().then(t=>{
  const d=JSON.parse(t);(d.reviews||[]).forEach(r=>{const m={g1:r.gemini_round1,g2:r.gemini_round2,e1:r.elevenlabs_round1,e2:r.elevenlabs_round2};
    Object.entries(m).forEach(([k,v])=>{if(v)S.v[r.scenario+'|'+k]=v});if(r.notes)S.n[r.scenario]=r.notes;});store.set(S);paint();filter();});};
document.addEventListener('play',e=>{document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true);
paint();filter();
"""

TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>October Voice Review</title><style>{css}</style></head><body><div class="wrap">
<header>
  <span class="eb">GenMedia voice study · October 2026 · manual review</span>
  <h1>{n} scenarios, both rounds: Gemini 3.8 Flash TTS vs ElevenLabs Eleven v4</h1>
  <p class="lead">Every scenario that has a round 1 and a round 2 run. Open a scenario to hear all four clips (Gemini on the left,
  ElevenLabs on the right; each side's round 1 and round 2 players sit together at the top, details below), with the automatic checks, transcripts and judge scores. Mark what you hear; your marks stay in this browser until you export them.</p>
  <div class="notice"><ul>
    <li><b>The judge</b> is Gemini 2.5 Flash on Vertex AI - the same vendor as one of the two models - and has been unreliable on what was actually said. Trust the checks and transcripts for wording; listening decides.</li>
    <li><b>Outside English</b> a pass covers only length, silence, loudness and the few phrase checks; pronunciation and wording rest on listening and the judge.</li>
    <li><b>Voices:</b> every woman is ElevenLabs Matilda / Gemini Aoede, every man Eric / Charon, the older man Bill / Fenrir. Age is not voiced and Matilda is an American voice - an age or accent note is a limit of this cast, not of either model.</li>
    <li><b>Licensing:</b> ElevenLabs clips made on Free plan keys (most of round 1 Hinglish and all of round 2) are not licensed for commercial use.</li>
    <li>Clips play from <code>voice/runs/</code>; open this file from its folder (<code>voice/oct-manual-review-112/</code>) so the links work. Built {generated}.</li>
  </ul></div>
</header>
<div class="sumwrap"><table class="sum"><thead><tr><th>Passed all checks</th><th>Gemini R1</th><th>Gemini R2</th><th>ElevenLabs R1</th><th>ElevenLabs R2</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<div class="bar-top">
  {chips}
  <button class="chip" data-f="view" data-v="fail">Any fail <span>{nfail}</span></button>
  <button class="chip" data-f="view" data-v="diff">Result differs between rounds <span>{ndiff}</span></button>
  <button class="chip" data-f="view" data-v="listen">On listening list <span>{nlist}</span></button>
  <button class="chip" data-f="view" data-v="todo">Not reviewed</button>
  <button class="chip" data-f="view" data-v="issue">Marked issue</button>
  <input type="search" id="q" placeholder="Search id or title">
  <button class="btn" id="openall">Open all</button><button class="btn" id="closeall">Close all</button>
  <button class="btn" id="export">Export my review</button>
  <label class="btn">Import<input type="file" id="import" accept=".json" hidden></label>
  <span id="shown"></span><span id="progress" class="small muted"></span>
</div>
{cards}
</div><script>{js}</script></body></html>"""

if __name__ == "__main__":
    print(build())
