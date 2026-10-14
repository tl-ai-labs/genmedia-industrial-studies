"""Make the input images for the October run (Nano Banana 2.1 vs GPT Image 2.5).

Edit scenarios need a picture to edit before either arm can run:
  * v3 bank: each source is a written prompt (106 uses, 59 unique prompts);
  * v2 bank: the site holds only thumbnails (127-180 px wide), so every v2
    input is remade here at full size from the bank's description, with the
    site thumbnail (where one exists) as a layout guide, and the exact text the
    edit depends on written into the prompt (V2_TEXT).

Every made source comes from ONE model, Nano Banana Pro (gemini-3-pro-image on
Vertex, the team's decision on 2026-10-09). Pro is not an arm under test, so
neither arm edits a picture it drew itself. Same prompt + aspect = same file,
so a portrait shared by five scenarios is one image, not five.

Usage (from image/):
    python tools/make_oct_sources.py --plan <runplan-data.json> \
        --bank-v2 <v2/image.json> --bank-v3 <v3/image.json> \
        --site-v2-dir <site repo .../scenario-bank/v2/image> [--dry-run]

Writes assets/bank-oct/sources/<key>.png and assets/bank-oct/sources.jsonl
(one row per made image: prompt, aspect, model version, usage, cost). Re-running
skips any file that already exists, so a stopped run resumes for free.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runner.adapters.base import Asset, GenRequest, RateLimited  # noqa: E402
from runner.cost import compute_cost  # noqa: E402
from runner.image.gemini_image import build  # noqa: E402
from runner.loaders import load_models  # noqa: E402

OUT = ROOT / "assets" / "bank-oct"
SOURCE_MODEL_ID = "gemini-3-pro-image-vertex"
# What a v2 source must show for its edit to make sense — taken from the
# edit brief (the text it changes, keeps or must reproduce exactly).
V2_TEXT = {
    "IMGEDIT-039": "The paper label clearly reads CASTELLO BIANCO and, below it, 2024, in a "
                   "dark red serif type on a cream label on a dark green bottle; the label is "
                   "applied visibly crooked, rotated several degrees off vertical.",
    "IMGEDIT-073": "The sign reads PLEASE KEEP OFF THE GRASS in white capital letters on dark green.",
    "IMGEDIT-074": "The large headline reads SAVE 25% over a photographic area, and a darker "
                   "strip along the bottom of the banner is completely empty, with no text.",
    "IMGEDIT-075": "The six lines read exactly, one per line: 'Thank you for your recent order "
                   "with us.' / 'We are pleased to confirm that your parcel' / 'has been "
                   "dispatched today and you should' / 'recieve it within three working days. "
                   "If' / 'anything is missing, please contact our' / 'customer service team "
                   "at your convenience.' The misspelling recieve on the fourth line is "
                   "deliberate and must be kept.",
    "IMGEDIT-080": "The three white text blocks read: PORT ELLAN in the sky, SUMMER SAILINGS "
                   "across the boats, and DAILY FERRIES JUNE TO SEPTEMBER over the quay.",
    "IMGEDIT-072": "The label reads ORGANIC OATS 1kg at the top, a large heavy black price "
                   "£3.49 horizontally centred, and a small unit price line per kg £3.49, "
                   "inside a thin black border.",
    "IMGEDIT-123": "A flat UI screenshot, not a photograph. Dark header bar with the wordmark "
                   "NORTHGATE on the left and a cart icon on the right; navigation row NEW IN, "
                   "MEN, WOMEN, SALE; section heading Winter Essentials; three columns: a grey "
                   "coat 'Ardmore Coat 149.00', a navy jumper 'Selby Knit 79.00', tan boots "
                   "'Harlow Boot 115.00'; light grey footer strip reading Free returns within "
                   "30 days. No other copy.",
    "IMGEDIT-124": "A flat UI screenshot of a phone, not a photograph, white background, plain "
                   "sans-serif. Title Checkout; order summary rows Ardmore Coat 149.00, Selby "
                   "Knit 79.00, Harlow Boot 115.00, a rule, then a bold row Total 343.00; a "
                   "block headed Deliver to with a two-line address; a row headed Payment with "
                   "Card ending 4417; a dark full-width button Place order at the bottom.",
    "IMGEDIT-027": "Each of the three identical white signs reads EXIT in bold black uppercase "
                   "sans-serif.",
    "IMGEDIT-051": "The two lines of text read HARBOUR FESTIVAL and 18 JULY.",
}


# Corrections from the visual review of the input images (2026-10-09): two
# independent reviewers looked at every image against its prompt and against
# each edit that uses it; these 22 were confirmed unusable as made. The bank's
# prompt is kept and an exact-requirements block is appended, naming every side
# both ways ("her left = the picture's right"), because the generator flips
# handedness. Keyed by the ORIGINAL image's key, so the record shows what changed.
SOURCE_FIXES = {
    "src-0f18b05e6f51": {"add": "A plain full-bleed photograph: no headline, title, caption, lettering, logo or watermark anywhere, and no decorative garland or leaf border or coloured strip. Exactly SEVEN lit clay diyas in ONE row along the balcony ledge, and no other diyas anywhere (none on the railing top, none on the floor, none in the rangoli). The round rangoli is pink, yellow and white only, with nothing in its centre. Faces lit warm and low by the diyas."},
    "src-24f931e652f3": {"drop_guide": True, "add": "Exactly one tulip stands at the front centre of the bunch, in front of all the others, on a longer stem, so its head is clearly the highest in the whole bunch; every other tulip sits lower and behind it. About fifteen identical yellow tulips in a clear cylindrical glass vase with the stems visible in the water, on a worn wooden table against a plain pale wall, soft light from the left."},
    "src-2fc7b1294c2a": {"add": "She faces the camera, so her right is the PICTURE'S LEFT. The bob is longer on the picture's LEFT side (her right), and the single teal streak is in that longer side. The yellow 07 patch is on her left sleeve, the sleeve on the PICTURE'S RIGHT. The white bandage is on her left knee, the knee on the PICTURE'S RIGHT. The background signs are abstract glowing shapes with no letters or numbers; 07 is the only text in the image."},
    "src-319c33412a4b": {"add": "He faces the camera, so his right is the PICTURE'S LEFT. The single gold tooth is in the grin on the PICTURE'S LEFT side of his mouth. The stalk quiff makes one clear curl bending toward the PICTURE'S RIGHT (his left). His sneakers are ALL WHITE (white upper, white toe, white sole) with bright RED laces; no red on the shoes except the laces."},
    "src-354d728846e6": {"add": "Exact RIGHT side profile: the nose points to FRAME RIGHT and the tail with the white 07 decal is at FRAME LEFT, so we see the bike's right side. The dent and scraped paint are on the front fairing just behind the nose, at frame right. A sleek single-seat hover motorbike with a narrow body, straddle seat and handlebars, not a car."},
    "src-3781053ebdd5": {"add": "Exactly ONE white sock, on the horse's LEFT FORELEG, from the hoof to just below the knee. The horse faces frame right, so its left foreleg is the FAR foreleg: stagger the stance so the far foreleg is clearly visible between the near legs. The near (right) foreleg and BOTH hind legs are solid dark bay to the hoof, with no white on them."},
    "src-3d64dc140ca6": {"add": "Facing the camera almost straight on, turned only slightly, with BOTH ears clearly visible. The mustard knitted patch is on the toy's OWN LEFT ear, which is the ear on the RIGHT side of the picture; the ear on the left side of the picture is plain grey. The trunk curls up and out to the toy's OWN RIGHT, toward the LEFT side of the picture."},
    "src-63159b20389c": {"add": "The left colonnade has EXACTLY six carved pillars. Counting from the camera, the THIRD pillar is missing from its plinth (the empty plinth is visible) and lies broken across the paving right in front of that empty plinth; pillars 1, 2, 4, 5 and 6 stand. The stone chariot stands on the central axis between the camera and the gopuram doorway, in the middle of the open paving, clear of the tank. A clean stylised 3D game render, no ink outlines."},
    "src-79953471ac9b": {"add": "Exactly SEVEN distinct lit clay diyas in one shallow arc directly in front of the jar, all in sharp focus and evenly spaced. The background is abstract soft round warm bokeh dots only: no recognisable diyas, bowls or flames in the background. Keep every text string exactly as written above."},
    "src-844edf2ee40f": {"add": "A true front-right three-quarter view: the motorcycle's nose points to frame RIGHT and slightly toward the camera, so we see the headlamp and the bike's RIGHT side. A clearly concave dent on that visible right flank of the fuel tank, breaking the paint's reflection. The red thread is wound several times around the LEFT handlebar grip (the far grip, beyond the tank, under the rider's left hand), its tassel ends hanging free and visible; nothing red on the right grip or bar centre. The near (right) olive saddlebag is visible. The road runs behind to a vanishing point at frame left, with heat shimmer."},
    "src-903a8520685a": {"add": "Medium framing that keeps the bench, path and the single far jogger on the right in frame. She raises her left wrist forward with the watch face square to the camera and large in frame, clearly showing crisp white digits 06:45 with a visible colon. The strap is clearly sage green silicone. The low golden sun is at frame RIGHT: glow in the upper right, warm light on the right side of her face and the wrist, shadows falling toward frame left."},
    "src-927f13e08dcf": {"add": "She faces the camera. Gold stud in HER LEFT nostril, which is on the PICTURE'S RIGHT. Small mole just above HER RIGHT upper-lip corner, which is on the PICTURE'S LEFT, so the stud and the mole are on opposite sides of her face. No other moles, spots or marks anywhere on the face or chin. The braid hangs over HER LEFT shoulder, on the PICTURE'S RIGHT."},
    "src-9f9a52f75857": {"add": "The room contains only: a large sofa nearest the camera; a low wooden sideboard against the far wall with exactly three framed pictures hung in a row above it and no other pictures anywhere; a coffee table on a rug with a clearly visible pattern; a bookcase at the LEFT frame edge and a floor lamp at the RIGHT frame edge; windows on the right wall with daylight from the right. No TV, no fireplace, no armchairs, no doorway, no people, no text. Shot from one corner on an ultra-wide lens with strong depth exaggeration and stretched edge objects, filling the whole rectangle (no black circular vignette)."},
    "src-b626ac833a5f": {"add": "She faces the camera. The white mulmul dupatta goes over HER LEFT shoulder, on the PICTURE'S RIGHT, and is visibly crooked: twisted and pulled at an angle across her front, one end hanging clearly lower than the other. The small safety pin is clearly visible and uncovered at the kurta hem on HER RIGHT side, on the PICTURE'S LEFT. Exactly five white buttons on the placket."},
    "src-be34763ea55c": {"add": "She stands nearly facing the camera, turned slightly toward frame left. She holds the sword in her RIGHT hand, the hand on FRAME LEFT, blade pointing down. Her LEFT hand, on FRAME RIGHT, is empty and relaxed, back of the glove toward the camera, with the glowing teal rune ON THE BACK OF THAT GLOVE. The sword and the rune are on different hands. The red sash-cape hangs only from her RIGHT shoulder (frame left); her left shoulder shows only its copper plate. Exactly three separate gold rings on the ponytail. No text, letters or legible signage anywhere."},
    "src-c04ebb6fe951": {"mirror": True},
    # second pass: the corrected elephant came back with BOTH one-sided details
    # exactly reversed and no text, so a mirror fixes it (user, 2026-10-09)
    "src-8b76516f57b5": {"mirror": True},
    "src-d908dab9eee7": {"drop_guide": True, "add": "Seven black-framed abstract prints on a plain off-white wall, one (lower right) hanging crooked by about 20 degrees, no furniture, no text. Shot OBLIQUELY with the camera about 30-35 degrees to one side of the wall, so the wall recedes: frame tops and bottoms visibly converge, and two frames of the same real size on opposite sides look clearly different in size. Show at least one straight wall boundary (the wall's end or its top and bottom edges). Warm raking light from one side casting angled shadows from each frame."},
    "src-e0e34cd0f2dd": {"add": "The tiger walks toward frame LEFT, so its OWN LEFT flank faces the camera, with the small leather saddle bag on that visible left flank. Its head is turned three-quarters toward the viewer so both ears and both eyes show. Its OWN LEFT ear (the near ear, on the frame-right side of the head) has a large, obvious notch torn out of the tip; its own right ear is intact. The white scar runs vertically across its OWN RIGHT eye, on the frame-left side of the face. A bold dark V of stripes in the centre of the forehead. The sun is at frame RIGHT and the long shadow falls toward frame left."},
    "src-e6bb69d0e3ca": {"add": "Squared to the camera, both arms relaxed at his sides, BOTH wrists inside the frame. The small pale scar cuts vertically through his OWN RIGHT eyebrow, which is on the PICTURE'S LEFT. The single steel kada is on his OWN RIGHT wrist, on the PICTURE'S LEFT; his left wrist (picture's right) is bare. His hair has a clearly visible side part on his OWN LEFT, on the picture's right."},
    "src-ea6539200b43": {"add": "Front view. The cracked bronze half-mask covers his OWN LEFT side of the face, which is on the RIGHT side of the picture. He holds the hourglass staff in his OWN LEFT hand, on the RIGHT side of the picture. A high stand-up coat collar is clearly visible. The third eye is a short vertical glowing slit on the forehead only. The small wordless arrow points at the mask. No lettering, name or title anywhere on the page."},
    "src-f2c5138d1410": {"add": "A full-bleed 16:9 landscape photograph filling the frame edge to edge: no borders, no bars, no blurred side panels. The tower is large in frame (about 70% of the frame height), slightly left of centre. The plain east face (frame right) is one single flat concrete plane, about a quarter of the frame width, with one narrow vertical strip of small windows down its middle. Harsh overhead noon sun, crisp short shadows, clear air."},
    "src-fe3bc324eb97": {"add": "Each product card shows a real product photo on a plain light background: card 1 a grey wool coat, card 2 a navy knit jumper, card 3 a pair of tan leather ankle boots. The three cards are equal width and evenly spaced across the full content width. The footer text is exactly: Free returns within 30 days"},
}

def source_key(prompt: str, aspect: str) -> str:
    return "src-" + hashlib.sha256(f"{aspect}\n{prompt}".encode()).hexdigest()[:12]


def plan_jobs(plan: dict, v2: dict, v3: dict,
              site_v2_dir: str | None = None) -> tuple[list[dict], dict]:
    """Unique images to make, plus scenario -> [{role, file}] for every edit."""
    jobs: dict[str, dict] = {}
    uses: dict[str, list[dict]] = {}
    for row in plan["img"]:
        sid = row["id"]
        if row["src"] == "v3 bank":
            for i, src in enumerate(v3[sid].get("sources") or []):
                key = source_key(src["prompt"], src["aspect"])
                jobs.setdefault(key, {"key": key, "prompt": src["prompt"],
                                      "aspect": src["aspect"], "scenarios": []})
                jobs[key]["scenarios"].append(sid)
                uses.setdefault(sid, []).append(
                    {"role": src["role"], "note": src.get("note"), "order": i,
                     "file": f"sources/{key}.png"})
        elif row["t"] == "Edit":
            desc = v2[sid].get("sourceDescription") or plan["det"][sid]["sourceDescription"]
            aspect = v2[sid].get("format") or "16:9"
            guide = Path(site_v2_dir) / f"{sid}.png" if site_v2_dir else None
            has_guide = bool(guide and guide.exists())
            prompt = desc + (" " + V2_TEXT[sid] if sid in V2_TEXT else "")
            prompt += " Sharp, detailed, full resolution. No watermark, no caption."
            if has_guide:
                prompt = ("The attached picture is a small low-resolution preview. Recreate it "
                          "as a sharp, full-resolution image, keeping its composition, "
                          "subjects, positions, colours and lighting. " + prompt)
            key = source_key(prompt + ("|guide" if has_guide else ""), aspect)
            jobs.setdefault(key, {"key": key, "prompt": prompt, "aspect": aspect,
                                  "guide": str(guide) if has_guide else None,
                                  "scenarios": []})
            jobs[key]["scenarios"].append(sid)
            uses[sid] = [{"role": "Image to edit", "order": 0,
                          "file": f"sources/{key}.png"}]
    return _apply_fixes(list(jobs.values()), uses)


def _apply_fixes(jobs: list[dict], uses: dict) -> tuple[list[dict], dict]:
    """Re-key every corrected image; scenarios follow it to the new file.
    Fixes chain: a corrected image may itself be fixed again (e.g. mirrored)."""
    final: dict[str, str] = {}
    for job in jobs:
        first = job["key"]
        for _ in range(5):                       # a chain is at most a few steps
            fix = SOURCE_FIXES.get(job["key"])
            if not fix:
                break
            old_key = job["key"]
            if fix.get("mirror"):
                job.update({"mirror_of": old_key, "key": old_key + "-mirrored",
                            "guide": None})
            else:
                prompt = job["prompt"]
                if fix.get("drop_guide") and job.get("guide"):
                    job["guide"] = None
                    prompt = prompt.split("lighting. ", 1)[-1]
                job["prompt"] = (prompt + "\n\nExact requirements (these take priority "
                                 "over anything above): " + fix["add"])
                job["key"] = source_key(job["prompt"] + "|fix1", job["aspect"])
            job["fixed_from"] = old_key
        if job["key"] != first:
            final[first] = job["key"]
    for rows in uses.values():
        for u in rows:
            k = u["file"].split("/")[-1][:-4]
            if k in final:
                u["file"] = f"sources/{final[k]}.png"
    return jobs, uses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--bank-v2", required=True)
    ap.add_argument("--bank-v3", required=True)
    ap.add_argument("--site-v2-dir", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text())
    v2 = {s["id"]: s for s in json.loads(Path(args.bank_v2).read_text())["scenarios"]}
    v3 = {s["id"]: s for s in json.loads(Path(args.bank_v3).read_text())["scenarios"]}
    jobs, uses = plan_jobs(plan, v2, v3, args.site_v2_dir)

    (OUT / "sources").mkdir(parents=True, exist_ok=True)
    (OUT / "inputs.json").write_text(json.dumps(uses, indent=1, ensure_ascii=False))

    todo = [j for j in jobs if not (OUT / "sources" / f"{j['key']}.png").exists()]
    mf = load_models(ROOT / "configs" / "models.yaml")
    model = next(m for m in mf.image if m.id == SOURCE_MODEL_ID)
    est = len(todo) * model.price.usd
    print(f"{len(jobs)} unique sources ({len(todo)} to make, ~${est:.2f} at "
          f"${model.price.usd}/image with {model.provider_model}); "
          f"{sum(1 for j in jobs if j.get('guide'))} v2 remakes use the site thumbnail as a guide")
    if args.dry_run or not todo:
        return 0

    adapter = build(model, timeout_s=300)
    log = OUT / "sources.jsonl"
    lock = threading.Lock()
    spent = [0]

    def make(job: dict) -> str:
        if job.get("mirror_of"):
            from PIL import Image, ImageOps
            src = OUT / "sources" / f"{job['mirror_of']}.png"
            with Image.open(src) as im:
                ImageOps.mirror(im).save(OUT / "sources" / f"{job['key']}.png", format="PNG")
            with lock, log.open("a") as fh:
                fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                     "key": job["key"], "scenarios": job["scenarios"],
                                     "aspect": job["aspect"], "mirror_of": job["mirror_of"],
                                     "cost": {"micro_usd": 0}}) + "\n")
            return f"ok   {job['key']} mirrored from {job['mirror_of']}"
        guide = ([Asset(role="reference", path=Path(job["guide"]), mime="image/png",
                        sha256="")] if job.get("guide") else [])
        req = GenRequest(task="text_to_image", text=job["prompt"], inputs=guide,
                         params={"aspect_ratio": job["aspect"], "size": "1024x1024"})
        for attempt in range(1, 6):
            try:
                t0 = time.time()
                res = adapter.run(req)
                from PIL import Image
                with Image.open(io.BytesIO(res.data)) as im:
                    w, h = im.size
                    blank = max(im.convert("L").getextrema()) - min(im.convert("L").getextrema()) < 8
                if blank:
                    raise ValueError("blank image")
                path = OUT / "sources" / f"{job['key']}.png"
                if res.mime != "image/png":
                    with Image.open(io.BytesIO(res.data)) as im:
                        buf = io.BytesIO()
                        im.save(buf, format="PNG")
                        path.write_bytes(buf.getvalue())
                else:
                    path.write_bytes(res.data)
                cost = compute_cost(model.price, res.usage)
                with lock:
                    spent[0] += cost["micro_usd"]
                    with log.open("a") as fh:
                        fh.write(json.dumps({
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "key": job["key"], "scenarios": job["scenarios"],
                            "aspect": job["aspect"], "prompt": job["prompt"],
                            "guide": job.get("guide"), "fixed_from": job.get("fixed_from"),
                            "model": model.provider_model,
                            "provider_version": res.provider_version,
                            "size": [w, h], "latency_s": round(time.time() - t0, 1),
                            "usage": res.usage, "cost": cost, "attempt": attempt},
                            ensure_ascii=False) + "\n")
                return f"ok   {job['key']} {w}x{h} ({', '.join(job['scenarios'][:3])})"
            except RateLimited:
                time.sleep(min(60, 10 * attempt))
            except Exception as e:  # noqa: BLE001 — report and retry
                err = f"{type(e).__name__}: {e}"
                if attempt == 5:
                    return f"FAIL {job['key']} ({job['scenarios'][0]}): {err[:200]}"
                time.sleep(5 * attempt)
        return f"FAIL {job['key']}: rate limited 5 times"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, fut in enumerate(as_completed([pool.submit(make, j) for j in todo]), 1):
            print(f"[{i}/{len(todo)}] {fut.result()}  (spent ${spent[0] / 1e6:.2f})",
                  flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
