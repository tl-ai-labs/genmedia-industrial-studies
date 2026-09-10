#!/usr/bin/env python3
"""
Build the blind listener panel.

Reads the committed v3-vs-Gemini MP3s in ../dashboard/audio/, the scenario
scripts in ../scenarios/, and the loudness measurements in a ../runs/ folder,
and writes everything the review page needs:

    manifest.blind.js      what index.html loads. No model names anywhere.
    clips/c_*.mp3          the audio, copied byte-for-byte, opaque names.
    reveal.json            clip id -> {card, model, take}. LOCAL ONLY. The
                           de-blind key. Never deploy it, never commit it.
    panel-for-review.zip   index.html + manifest.blind.js + clips/ + serve.py
                           + HOW-TO.txt -- the package you hand to a reviewer.

Nothing here is modified: the MP3s are copied with shutil.copyfile, bit for
bit. Loudness is matched in the browser by attenuating the louder clip of
each pair down to its partner (a per-clip <audio>.volume <= 1.0), so the two
clips a reviewer compares are level-matched without touching a file.

    python voice/panel/build.py
    python voice/panel/build.py --run 2026-09-09_041808_voice-p1 --attention-checks 2

Offline, no API key. Deterministic: the opaque names are a salted hash of
(card, model), so re-running rewrites nothing unless a clip actually changed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import yaml

PANEL = Path(__file__).resolve().parent
VOICE = PANEL.parent
REPO = VOICE.parent

AUDIO_SRC = VOICE / "dashboard" / "audio"
SCEN_DIR = VOICE / "scenarios"
RUNS_DIR = VOICE / "runs"

TAKE = "voice-p1"                       # take p1 only, by decision
MODELS = ("elevenlabs-v3", "gemini-3-1-flash-tts")
SALT = "genmedia-voice-panel/v1"        # changing this renames every clip


# --------------------------------------------------------------------------- #
# scenarios                                                                    #
# --------------------------------------------------------------------------- #
def load_scenarios() -> dict:
    out = {}
    for f in sorted(SCEN_DIR.rglob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text())
        except yaml.YAMLError as e:
            sys.exit(f"cannot parse {f}: {e}")
        if isinstance(d, dict) and d.get("id"):
            out[d["id"]] = d
    return out


def card_meta(scenarios: dict, scenario_id: str, variant: str | None) -> dict:
    """script / style / lang / task / title for one card, variant merged in."""
    d = scenarios.get(scenario_id)
    if d is None:
        sys.exit(f"no scenario yaml for {scenario_id!r}")
    base_in = d.get("input", {}) or {}
    task = d.get("task", "-")
    title = d.get("title", scenario_id)

    if variant:
        vs = {v.get("id"): v for v in (d.get("variants") or [])}
        v = vs.get(variant)
        if v is None:
            sys.exit(f"{scenario_id} has no variant {variant!r}")
        v_in = v.get("input", {}) or {}
        script = (v.get("script") or v_in.get("script") or "").strip()
        style = v.get("style") or v_in.get("style") or base_in.get("style")
        lang = v_in.get("language") or base_in.get("language")
    else:
        script = (base_in.get("script") or "").strip()
        style = base_in.get("style")
        lang = base_in.get("language")

    if not script:
        sys.exit(f"{scenario_id}{'#' + variant if variant else ''}: empty script")
    return {"task": task, "title": title, "script": script,
            "style": style, "lang": lang}


# --------------------------------------------------------------------------- #
# loudness                                                                     #
# --------------------------------------------------------------------------- #
def pick_run(explicit: str | None) -> Path:
    if explicit:
        p = RUNS_DIR / explicit
        if not (p / "checks.jsonl").exists():
            sys.exit(f"{p}/checks.jsonl not found")
        return p
    cands = sorted(p for p in RUNS_DIR.glob(f"*_{TAKE}") if (p / "checks.jsonl").exists())
    if not cands:
        cands = sorted(p.parent for p in RUNS_DIR.glob("*/checks.jsonl"))
    if not cands:
        sys.exit(f"no run with checks.jsonl under {RUNS_DIR}")
    return cands[-1]


def load_loudness(run: Path) -> dict:
    """(scenario_id, model_id) -> {rms_dbfs, peak_dbfs}."""
    out = {}
    for line in (run / "checks.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        m = r.get("measurements", {})
        out[(r.get("scenario_id"), r.get("model_id"))] = {
            "rms_dbfs": m.get("rms_dbfs"),
            "peak_dbfs": m.get("peak_dbfs"),
        }
    return out


def pair_gains(rms: dict[str, float | None]) -> tuple[dict[str, float], float | None]:
    """Attenuate the louder clip of the pair down to the quieter one.

    Returns per-model linear gain in (0, 1] and the shared target dBFS.
    A missing measurement -> that clip plays at 1.0 and is left out of the
    target (rare; warned by the caller).
    """
    known = {k: v for k, v in rms.items() if isinstance(v, (int, float))}
    if not known:
        return {k: 1.0 for k in rms}, None
    target = min(known.values())
    gains = {}
    for k in rms:
        if k in known:
            gains[k] = round(10 ** ((target - known[k]) / 20.0), 4)   # <= 1.0
        else:
            gains[k] = 1.0
    return gains, round(target, 2)


# --------------------------------------------------------------------------- #
# filenames                                                                    #
# --------------------------------------------------------------------------- #
def parse_clip(name: str) -> tuple[str, str, str | None] | None:
    """'vr-game-04--gemini-3-1-flash-tts--engineer--voice-p1.mp3'
        -> ('vr-game-04', 'gemini-3-1-flash-tts', 'engineer')"""
    if not name.endswith(f"--{TAKE}.mp3"):
        return None
    stem = name[: -len(f"--{TAKE}.mp3")]
    for model in MODELS:
        tok = f"--{model}"
        if tok in stem:
            scenario, _, rest = stem.partition(tok)
            variant = rest.strip("-") or None
            return scenario, model, variant
    return None


def clip_id(card_key: str, model: str) -> str:
    h = hashlib.sha1(f"{card_key}|{model}|{SALT}".encode()).hexdigest()
    return "c_" + h[:12]


# --------------------------------------------------------------------------- #
# build                                                                        #
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="runs/<id> to read loudness from (default: latest *_voice-p1)")
    ap.add_argument("--attention-checks", type=int, default=2,
                    help="identical-pair integrity cards to add (default 2, 0 to disable)")
    ap.add_argument("--no-zip", action="store_true", help="skip panel-for-review.zip")
    args = ap.parse_args()

    if not AUDIO_SRC.is_dir():
        sys.exit(f"{AUDIO_SRC} not found -- run the client-report export first")

    scenarios = load_scenarios()
    run = pick_run(args.run)
    loud = load_loudness(run)

    # group the take-p1 MP3s by card
    cards_src: dict[str, dict] = {}
    for f in sorted(AUDIO_SRC.glob(f"*--{TAKE}.mp3")):
        parsed = parse_clip(f.name)
        if not parsed:
            continue
        scenario, model, variant = parsed
        key = f"{scenario}#{variant}" if variant else scenario
        cards_src.setdefault(key, {"scenario": scenario, "variant": variant, "files": {}})
        cards_src[key]["files"][model] = f

    clips_dir = PANEL / "clips"
    for stale in clips_dir.glob("c_*.mp3"):
        stale.unlink()
    clips_dir.mkdir(exist_ok=True)

    cards: list[dict] = []
    reveal: dict[str, dict] = {}
    warnings: list[str] = []

    for key in sorted(cards_src):
        src = cards_src[key]
        have = src["files"]
        missing = [m for m in MODELS if m not in have]
        if missing:
            warnings.append(f"{key}: no {', '.join(missing)} clip -- card skipped")
            continue

        meta = card_meta(scenarios, src["scenario"], src["variant"])
        look_id = key  # checks.jsonl uses the same '#variant' form
        rms = {m: (loud.get((look_id, m)) or {}).get("rms_dbfs") for m in MODELS}
        for m in MODELS:
            if rms[m] is None:
                warnings.append(f"{key}/{m}: no loudness row in {run.name} -- plays at 1.0")
        gains, target = pair_gains(rms)

        pair = []
        for m in MODELS:
            cid = clip_id(key, m)
            shutil.copyfile(have[m], clips_dir / f"{cid}.mp3")   # byte-for-byte
            reveal[cid] = {"card": key, "model": m, "take": "p1",
                           "variant": src["variant"]}
            pair.append({"clip": cid, "gain": gains[m]})
        pair.sort(key=lambda x: x["clip"])                       # order leaks nothing

        cards.append({
            "id": key,
            "attn": False,
            "task": meta["task"],
            "lang": meta["lang"],
            "style": meta["style"],
            "title": meta["title"],
            "script": meta["script"],
            "target_rms_dbfs": target,
            "pair": pair,
        })

    if not cards:
        sys.exit("no complete cards built -- nothing to write")

    # integrity cards: the same clip on both sides
    n_attn = max(0, args.attention_checks)
    if n_attn:
        step = max(1, len(cards) // (n_attn + 1))
        for i in range(n_attn):
            donor = cards[min(len(cards) - 1, step * (i + 1))]
            slot = donor["pair"][i % 2]
            cards.append({
                "id": f"attn-{i + 1}",
                "attn": True,
                "task": "check",
                "lang": None,
                "style": None,
                "title": "Identical-pair check",
                "script": ("Both clips below are the same recording. If they sound "
                           "identical to you, choose “No preference”."),
                "target_rms_dbfs": None,
                "pair": [{"clip": slot["clip"], "gain": slot["gain"]},
                         {"clip": slot["clip"], "gain": slot["gain"]}],
            })

    manifest_hash = hashlib.sha1(
        json.dumps(cards, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    obj = {
        "builtAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "manifestHash": manifest_hash,
        "sourceRun": run.name,
        "take": "p1",
        "arms": 2,
        "note": "model identities are in reveal.json, which is not shipped",
        "cards": cards,
    }
    (PANEL / "manifest.blind.js").write_text(
        "// generated by build.py -- do not edit\n"
        "window.PANEL = " + json.dumps(obj, indent=2, ensure_ascii=False) + ";\n"
    )
    (PANEL / "reveal.json").write_text(json.dumps(reveal, indent=2) + "\n")

    n_real = sum(1 for c in cards if not c["attn"])
    clip_bytes = sum(f.stat().st_size for f in clips_dir.glob("c_*.mp3"))
    print(f"panel: {n_real} scenario cards + {len(cards) - n_real} integrity cards")
    print(f"       {len(list(clips_dir.glob('c_*.mp3')))} clips in clips/ = {clip_bytes / 1e6:.1f} MB")
    print(f"       loudness from runs/{run.name}, manifestHash {manifest_hash}")
    for w in warnings:
        print(f"  warn: {w}")

    if not args.no_zip:
        write_review_zip(manifest_hash)


REVIEW_HOWTO = """\
Blind voice listening panel -- how to run it
============================================

1. Unzip this folder somewhere.

2. Open index.html.
   - Double-click it. It should just open in your browser.
   - If the page is blank or the clips will not play, run instead:
         python serve.py
     and use the address it prints (http://localhost:8000).

3. Type your name or initials, click Start.

4. For each screen: read the line at the top, play BOTH clips fully,
   then choose "A is better", "No preference", or "B is better".
   A one-line "why" is optional. A few pairs are the same clip twice --
   choose "No preference" on those.

5. On the last screen click Copy JSON (or Download) and send the result
   back to whoever gave you this folder.

Your progress is saved in the browser as you go, so you can close the tab
and come back to the same machine and browser to finish.
"""


def write_review_zip(manifest_hash: str) -> None:
    out = PANEL / "panel-for-review.zip"
    members = ["index.html", "manifest.blind.js", "serve.py"]
    missing = [m for m in members if not (PANEL / m).exists()]
    if missing:
        print(f"  warn: {', '.join(missing)} missing -- zip not written")
        return
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for m in members:
            z.write(PANEL / m, m)
        z.writestr("HOW-TO.txt", REVIEW_HOWTO)
        for clip in sorted((PANEL / "clips").glob("c_*.mp3")):
            z.write(clip, f"clips/{clip.name}")
    print(f"       panel-for-review.zip ({out.stat().st_size / 1e6:.1f} MB) "
          f"-- reveal.json is NOT in it")


if __name__ == "__main__":
    main()
