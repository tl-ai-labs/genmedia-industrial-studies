"""Make the input images for the November run, from assets/bank-nov/source-jobs.json.

The jobs come from tools/import_nov_list.py. As in October, every made source
comes from one model, Nano Banana Pro, which is not an arm under test. Images
that bank-oct already holds are copied, not remade. Re-running skips any file
that already exists, so a stopped run resumes for free.

Usage (from image/):
    python tools/make_nov_sources.py --dry-run     # counts and cost, spends nothing
    python tools/make_nov_sources.py               # spends about $11 before any fix round
    python tools/make_nov_sources.py --export      # after review: JPEG inputs for the run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from make_oct_sources import SOURCE_MODEL_ID, make_images  # noqa: E402
from runner.loaders import load_models  # noqa: E402

OUT = ROOT / "assets" / "bank-nov"
# 2K: the same price as 1K for this model, and small marks stay sharp in the edit
SOURCE_SIZE = "2048x2048"


def export_inputs(jobs: list[dict]) -> int:
    """The run's inputs: same pixels as the PNG masters at a fifth of the size,
    so a request with two 2K inputs stays well inside the providers' limits."""
    from PIL import Image
    (OUT / "inputs").mkdir(exist_ok=True)
    total = 0
    for j in jobs:
        dest = OUT / "inputs" / f"{j['key']}.jpg"
        with Image.open(OUT / "sources" / f"{j['key']}.png") as im:
            im.convert("RGB").save(dest, format="JPEG", quality=95, subsampling=0, optimize=True)
        total += dest.stat().st_size
    print(f"exported {len(jobs)} inputs, {total / 1e6:.0f} MB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="make only the first N, to check a few before the rest")
    ap.add_argument("--export", action="store_true",
                    help="write the reviewed sources to inputs/<key>.jpg for the run; "
                         "spends nothing")
    args = ap.parse_args()

    jobs = json.loads((OUT / "source-jobs.json").read_text())
    if args.export:
        return export_inputs(jobs)
    missing = [j for j in jobs if not (OUT / "sources" / f"{j['key']}.png").exists()]
    reuse = [j for j in missing if j.get("reuse")]
    todo = [j for j in missing if not j.get("reuse")]
    model = next(m for m in load_models(ROOT / "configs" / "models.yaml").image
                 if m.id == SOURCE_MODEL_ID)
    print(f"{len(jobs)} sources: {len(missing)} missing, {len(reuse)} to copy, {len(todo)} to "
          f"make (~${len(todo) * model.price.usd:.2f} at ${model.price.usd}/image with "
          f"{model.provider_model}); {sum(1 for j in todo if j.get('guide'))} use a site "
          "thumbnail as a guide")
    if args.dry_run:
        return 0

    (OUT / "sources").mkdir(parents=True, exist_ok=True)
    for j in reuse:
        shutil.copy2(ROOT / j["reuse"], OUT / "sources" / f"{j['key']}.png")
    if todo:
        make_images(todo[:args.limit], model, OUT, args.workers, size=SOURCE_SIZE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
