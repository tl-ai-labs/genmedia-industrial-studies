"""
Export one image run as a dated round of the blind human panel.

    python tools/export_panel_round.py --run runs/2026-11-03_115853_image --date 2026-11-03 \
        --out <site>/dashboard/public/reports/genmedia-blind-panel/2026-11-03 \
        --key ../panel/private/key-2026-11-03.json

The October image lane on the site was built the same way (Sainadh's
export_image_nb21.py, described in the site's CLAUDE.md): outputs at most
1600 px on the long side, inputs at most 1200 px, JPEG quality 88; a set's
frames as `frames`, keyed by the side's main file (frame 1, the file a vote
names); the set's member briefs appended to the prompt as "One image each".

`--out` gets items.json and media/, which name no model and no run: media ids
are salted hashes, the pair is sorted by them, and a JPEG written here carries
no metadata. The id-to-model map goes to `--key` only, in the panel's key
shape. The vote store must be given those items (merged into its key) before
this round can take votes; see panel/tools/merge_key.py.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image

IMAGE_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PX, INPUT_PX, QUALITY = 1600, 1200, 88
LANE = "image"


def opaque(*parts: str, n: int = 12) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:n]


def jpeg(src: Path, longest: int) -> bytes:
    im = Image.open(src)
    im = im.convert("RGB")
    if max(im.size) > longest:
        r = longest / max(im.size)
        im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=QUALITY, optimize=True)
    return buf.getvalue()


def input_label(role: str, seen: dict[str, int]) -> str:
    base = role.split("_")[0]
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base} {seen[base]}"


def input_path(run: Path, sid: str, rel: str) -> Path:
    p = IMAGE_ROOT / rel
    return p if p.exists() else run / "inputs" / sid / Path(rel).name


def brief_of(sc: dict) -> str:
    text = sc["prompt"].strip()
    members = (sc.get("output_set") or {}).get("members") or []
    if members:
        text += "\n\nOne image each:\n" + "\n".join(
            f"{i}. {m['name']}: {m['brief'].strip()}" for i, m in enumerate(members, 1))
    return text


def run_label(run: Path) -> str:
    """The run folder as the studies repo names it (image/runs/<id>), wherever it was read from."""
    try:
        return str(run.resolve().relative_to(IMAGE_ROOT.parent))
    except ValueError:
        return str(run)


def judge_scores(run: Path) -> dict[tuple[str, str], float | None]:
    out: dict[tuple[str, str], float | None] = {}
    for line in (run / "scores.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[(r["scenario_id"], r["model_id"])] = r.get("score")
    return out


def export(run: Path, date: str, out: Path, key_path: Path, salt: str | None = None) -> dict:
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    salt = salt or secrets.token_hex(16)
    scores = judge_scores(run)
    if out.exists():
        shutil.rmtree(out)
    (out / "media").mkdir(parents=True)

    def write(name: str, data: bytes) -> str:
        (out / "media" / name).write_bytes(data)
        return name

    items, key_items, models = [], {}, set()
    for yml in sorted((run / "scenarios").glob("*.yaml")):
        sc = yaml.safe_load(yml.read_text(encoding="utf-8"))
        sid = sc["id"]
        sdir = run / "outputs" / LANE / sid
        arms = sorted(p.stem for p in sdir.glob("*.png"))
        if len(arms) != 2:
            raise SystemExit(f"{sid}: expected two outputs, found {arms}")
        models.update(arms)
        members = (sc.get("output_set") or {}).get("members") or []
        mains, frames, media_map = [], {}, {}
        for model in arms:
            if members:
                files = [sdir / "frames" / f"{model}__f{i}.png" for i in range(1, len(members) + 1)]
                missing = [f.name for f in files if not f.exists()]
                if missing:
                    raise SystemExit(f"{sid}: missing frames {missing}")
                names = [write(f"item-{opaque(salt, LANE, run_id, sid, model, *(() if i == 1 else ('frame', str(i))))}.jpg",
                               jpeg(f, OUTPUT_PX)) for i, f in enumerate(files, 1)]
                frames[names[0]] = [{"media": n, "label": f"{i} · {m['name']}"}
                                    for i, (n, m) in enumerate(zip(names, members), 1)]
                main = names[0]
            else:
                main = write(f"item-{opaque(salt, LANE, run_id, sid, model)}.jpg", jpeg(sdir / f"{model}.png", OUTPUT_PX))
            mains.append(main)
            media_map[main] = model
        seen: dict[str, int] = {}
        inputs = []
        for role, rel in (sc.get("inputs") or {}).items():
            label = input_label(role, seen)
            name = write(f"input-{opaque(salt, LANE, run_id, sid, 'input', label)}.jpg",
                         jpeg(input_path(run, sid, rel), INPUT_PX))
            inputs.append({"label": label, "media": name, "kind": "image"})
        item_id = opaque("item", LANE, run_id, sid, n=10)
        item = {
            "id": item_id, "lane": LANE, "scenario_id": sid, "task": sc["task"],
            "title": sc["title"], "brief": brief_of(sc), "industry": sc["meta"]["industry"],
            "kind": "image", "inputs": inputs, "pair": sorted(mains),
        }
        if frames:
            item["frames"] = {k: frames[k] for k in sorted(frames)}
        items.append(item)
        key_items[item_id] = {"lane": LANE, "run_id": run_id, "scenario_id": sid, "media": media_map,
                              "judge": {m: scores.get((sid, m)) for m in arms}}

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    (out / "items.json").write_text(json.dumps(
        {"generated": generated, "round": date, "lanes": [LANE], "items": items},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(
        {"generated": generated, "round": date, "salt": salt, "runs": {LANE: {run_id: run_label(run)}},
         "items": key_items}, indent=1) + "\n", encoding="utf-8")

    # The blind, checked: no model id and no run id in any public name or byte.
    tells = sorted(models) + [run_id]
    for f in out.rglob("*"):
        if f.is_file():
            blob = f.read_bytes()
            for t in tells:
                if t in f.name or t.encode() in blob:
                    raise SystemExit(f"{f} names {t}: the round is not blind")
    return {"items": len(items), "files": sum(1 for _ in (out / "media").iterdir()),
            "mb": round(sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6, 1),
            "run_id": run_id, "key": str(key_path)}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--date", required=True, help="the round's date, YYYY-MM-DD: its URL segment")
    p.add_argument("--out", type=Path, required=True, help="the site's public/reports/genmedia-blind-panel/<date>")
    p.add_argument("--key", type=Path, required=True, help="where the private key goes; never under --out")
    p.add_argument("--salt", help="reuse a salt so media ids repeat (default: fresh)")
    a = p.parse_args()
    if a.key.resolve().is_relative_to(a.out.resolve()):
        raise SystemExit("refusing: the key would sit inside the public folder")
    print(json.dumps(export(a.run, a.date, a.out, a.key, a.salt), indent=1))


if __name__ == "__main__":
    main()
