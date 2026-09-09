"""Build the frozen input assets for the asset-fed video scenarios (I2V, AD,
AVA) the way the image project built its bank: synthetic, licence-clean,
reproducible, every file carrying a JSON sidecar with provenance + sha256.

Two sources, in this order of preference:
  * REUSE  — byte-for-byte copies of the image project's frozen bank assets
             (the fictional AURELO brand kit, synthetic portraits, an interior,
             a landscape, a style plate). Sidecar records the origin file and
             its original generation prompt.
  * GENERATE — gemini-3.1-flash-image on Vertex (same model/route the image
             project used), only for briefs nothing on disk satisfies. Frame
             pairs/triples are made by editing the first frame so the scene
             stays consistent (sidecar records the input image).

Idempotent: an existing output is never regenerated, so re-running never
re-bills. Sequential with a pause — the Vertex project's image quota 429s on
parallel calls (image lane note, 2026-09-01).

    python assets/make_bank_assets.py            # build everything missing
    python assets/make_bank_assets.py --dry-run  # print the plan, call nothing
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

VIDEO = Path(__file__).resolve().parent.parent
REPO = VIDEO.parent
IMAGE_BANK = REPO / "image" / "assets" / "bank"
OUT = VIDEO / "assets" / "bank"
sys.path.insert(0, str(VIDEO))

GEN_MODEL = "gemini-3.1-flash-image"
PAUSE_S = 32                      # ~2 rpm, the lane's proven quota posture
STYLE = ("Photorealistic, natural light, no watermark, no caption text. ")

# ---- plan ------------------------------------------------------------------
# (output name, kind, source-or-prompt, optional input image for edits)
REUSE = [
    # product references — the AURELO CACAO amber jar is the bank's hero product
    ("VID-AD-01-reference.png",  "IMG-BRAND-01-source.png"),
    ("VID-AD-02-reference.png",  "IMG-BRAND-01-source.png"),
    ("VID-AD-03-reference.png",  "IMG-BRAND-01-source.png"),
    ("VID-AD-06-reference.png",  "IMG-BRAND-01-source.png"),
    ("VID-AD-07-reference.png",  "IMG-BRAND-01-source.png"),
    ("VID-I2V-05-reference.png", "IMG-BRAND-01-source.png"),
    ("VID-AVA-06-product.png",   "IMG-BRAND-01-source.png"),
    # three variants of the same brand for the SKU line-up
    ("VID-AD-08-reference1.png", "IMG-BRAND-01-source.png"),
    ("VID-AD-08-reference2.png", "IMG-BRAND-05-reference.png"),
    ("VID-AD-08-reference3.png", "IMG-BRAND-06-source.png"),
    # the fictional brand's logo (teal circle, white A, wordmark)
    ("VID-AD-05-logo.png",       "IMG-BRAND-04-reference.png"),
    ("VID-AD-10-logo.png",       "IMG-BRAND-04-reference.png"),
    ("VID-I2V-09-logo.png",      "IMG-BRAND-04-reference.png"),
    # synthetic people — never a real individual
    ("VID-I2V-01-photo.png",     "IMG-CHAR-03-source.png"),
    ("VID-I2V-04-character.png", "IMG-CHAR-05-reference.png"),
    ("VID-I2V-07-character.png", "IMG-CHAR-05-reference.png"),
    ("VID-AVA-01-presenter.png", "IMG-CHAR-05-reference.png"),
    ("VID-AVA-04-presenter.png", "IMG-CHAR-05-reference.png"),   # "the same presenter"
    ("VID-AVA-02-presenter1.png", "IMG-CHAR-05-reference.png"),
    ("VID-AVA-02-presenter2.png", "IMG-CHAR-01-source.png"),
    # places and plates
    ("VID-I2V-07-environment.png", "IMG-STY-05-source.png"),     # living-room interior
    ("VID-I2V-06-landscape.png",   "IMG-STY-01-source.png"),     # lighthouse cape: fg rocks / bg sea
    ("VID-I2V-08-style.png",       "IMG-STY-03-reference.png"),  # fauvist plate: palette + texture
]

GENERATE = [
    ("VID-AD-04-reference.png", STYLE +
     "A white plastic spray bottle of kitchen surface cleaner with a matte deep-teal "
     "label (hex 1F6F5C) reading \"AURELO HOME\" in clean white capitals, standing on "
     "a plain light-grey surface, front view, slightly above eye level.", None),
    ("VID-AD-10-reference.png", STYLE +
     "A matte kraft-paper coffee bag with a deep-teal (hex 1F6F5C) label reading "
     "\"AURELO COFFEE\" in clean white capitals, standing upright on a wooden counter, "
     "front view, studio lit.", None),
    ("VID-I2V-02-first_frame.png", STYLE +
     "A woman with shoulder-length dark hair in a grey sweater sits at a wooden table "
     "facing the camera, hands around a mug; a large window with soft daylight is to "
     "her left, out of focus. Medium shot, eye level, still and calm.", None),
    ("VID-I2V-03-first_frame.png", STYLE +
     "A plain wooden table by a bright window, an empty white ceramic cup on a saucer "
     "in the centre, morning light from the left, nothing else on the table.", None),
    ("VID-I2V-03-last_frame.png",
     "Edit this photograph minimally: the same table, cup and window from the same "
     "camera position, but the cup is now full of black coffee with a thin wisp of "
     "steam, and the light is slightly warmer. Change nothing else.",
     "VID-I2V-03-first_frame.png"),
    ("VID-I2V-10-still1.png", STYLE +
     "A man in a navy raincoat stands at a quiet bus stop on a wet city street, holding "
     "a closed black umbrella at his side, light rain, overcast, wide shot from across "
     "the street.", None),
    ("VID-I2V-10-still2.png",
     "Edit this photograph: same man, same bus stop, same camera position and weather, "
     "but he is now opening the black umbrella above his head. Change nothing else.",
     "VID-I2V-10-still1.png"),
    ("VID-I2V-10-still3.png",
     "Edit this photograph: same man, same street, same camera position and weather, "
     "but he now walks away from the bus stop along the pavement under the open black "
     "umbrella, seen from behind. Change nothing else.",
     "VID-I2V-10-still1.png"),
]

# AVA-04 needs the AVA-01 script in Hindi; kept as a frozen text asset too
HINDI_SCRIPT = ("वापसी पर स्वागत है। आज हम आपके बिजली के बिल को कम करने के तीन तरीक़े "
                "देख रहे हैं।")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sidecar(p: Path, data: dict) -> None:
    p.with_suffix(".json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def do_reuse(dry: bool) -> int:
    n = 0
    for out_name, src_name in REUSE:
        out, src = OUT / out_name, IMAGE_BANK / src_name
        if out.exists():
            continue
        if not src.exists():
            raise FileNotFoundError(f"reuse source missing: {src}")
        orig = json.loads(src.with_suffix(".json").read_text()) if src.with_suffix(".json").exists() else {}
        print(f"  reuse    {out_name:<30} <- image/assets/bank/{src_name}")
        if dry:
            continue
        shutil.copyfile(src, out)
        sidecar(out, {"reused_from": f"image/assets/bank/{src_name}",
                      "generated_by": orig.get("generated_by"), "route": orig.get("route"),
                      "prompt": orig.get("prompt"), "sha256": sha256(out), "ts": utcnow(),
                      "note": "byte-for-byte copy of the image project's frozen bank asset"})
        n += 1
    return n


def _client(timeout_s: float = 120):
    from google import genai
    from google.genai import types
    from runner.adapters.google_client import _make_client
    from runner.loaders import load_dotenv
    load_dotenv(VIDEO)
    cfg = SimpleNamespace(vertex=SimpleNamespace(project="ai-studies-console",
                                                 location="global"), auth_env=None)
    return genai, types, _make_client(genai, types, cfg, timeout_s)


def _generate_one(genai, types, client, prompt: str, input_png: Path | None) -> bytes:
    contents: list = []
    if input_png is not None:
        contents.append(types.Part.from_bytes(data=input_png.read_bytes(), mime_type="image/png"))
    contents.append(prompt)
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="16:9", image_size="1K"))
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(model=GEN_MODEL, contents=contents, config=cfg)
            for cand in (resp.candidates or []):
                for part in (getattr(cand.content, "parts", None) or []):
                    inline = getattr(part, "inline_data", None)
                    if inline is not None and getattr(inline, "data", None):
                        return inline.data
            raise RuntimeError("response contained no image data")
        except Exception as e:  # 429s on this project's quota are the known failure
            if attempt == 3 or "429" not in str(e):
                raise
            wait = 60 * attempt
            print(f"    429 — waiting {wait}s (attempt {attempt}/3)")
            time.sleep(wait)


def do_generate(dry: bool) -> tuple[int, int]:
    todo = [(o, p, i) for o, p, i in GENERATE if not (OUT / o).exists()]
    for o, p, i in todo:
        print(f"  generate {o:<30} {'(edit of ' + i + ')' if i else ''}")
    if dry or not todo:
        return len(todo), 0
    genai, types, client = _client()
    done = 0
    for out_name, prompt, input_name in todo:
        out = OUT / out_name
        input_png = OUT / input_name if input_name else None
        if input_png is not None and not input_png.exists():
            raise FileNotFoundError(f"edit input not built yet: {input_png}")
        t0 = time.time()
        data = _generate_one(genai, types, client, prompt, input_png)
        out.write_bytes(data)
        meta = {"generated_by": GEN_MODEL, "route": "vertex", "prompt": prompt,
                "sha256": sha256(out), "ts": utcnow()}
        if input_png is not None:
            meta["input_image"] = input_png.name
            meta["input_sha256"] = sha256(input_png)
        sidecar(out, meta)
        done += 1
        print(f"    ok {out_name} ({len(data) / 1e6:.2f} MB, {time.time() - t0:.0f}s)")
        if done < len(todo):
            time.sleep(PAUSE_S)
    return len(todo), done


def do_script(dry: bool) -> None:
    out = OUT / "VID-AVA-04-script.hi.txt"
    if out.exists():
        return
    print(f"  write    {out.name:<30} (Hindi script for AVA-04, in-house translation)")
    if dry:
        return
    out.write_text(HINDI_SCRIPT + "\n", encoding="utf-8")
    sidecar(out, {"kind": "script", "language": "hi",
                  "translation_of": "VID-AVA-01 prompt line: 'Welcome back. Today we are "
                                    "looking at three ways to cut your energy bill.'",
                  "source": "in-house translation (not from IndicVoices-R)",
                  "sha256": sha256(out), "ts": utcnow()})


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[assets] output: {OUT}  {'(DRY RUN)' if dry else ''}")
    r = do_reuse(dry)
    planned, made = do_generate(dry)
    do_script(dry)
    print(f"[assets] reused {r} · generated {made}/{planned} planned"
          + (f" · est. ${planned * 0.067:.2f} at 1K" if dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
