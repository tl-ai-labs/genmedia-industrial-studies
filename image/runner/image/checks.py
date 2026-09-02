"""Image checks: gates (valid at all?) and measures (facts with a right
answer). Free local tools do everything code can measure — the AI judge
scores only what code cannot (plan §11).

  * validity: Pillow + numpy (decode, dimensions, not-blank)
  * text-in-image: OCR via rapidocr (fallback: pytesseract) + fuzzy match
  * edit preservation: imagehash pHash (global bound) and scikit-image SSIM
    scoped outside a supplied mask/bbox — NO automatic segmentation, ever

Registered in runner/checks.py CHECK_SUITES; the CheckOutcome contract and
suite selection live there.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

from ..checks import CheckOutcome, _gate

NOT_BLANK_STD_FLOOR = 4.0        # grayscale stddev below this = near-uniform
OCR_GATE_THRESHOLD = 0.85        # plan §6: must_read_text fuzzy match at 0.85


def check_image(scenario, output_path: Path, assets: dict | None = None) -> CheckOutcome:
    out = CheckOutcome()
    checks = scenario.checks or {}

    try:
        with Image.open(output_path) as im:
            im.load()
            fmt = (im.format or "").lower()
            width, height = im.size
            gray = np.asarray(im.convert("L"), dtype=np.float64)
    except Exception as e:
        out.gates.append(_gate("decodes", False, f"{type(e).__name__}: {e}"))
        return out
    out.gates.append(_gate("decodes", True, fmt))

    formats = [f.lower() for f in checks.get("formats", [])]
    if formats:
        out.gates.append(_gate("format", fmt in formats,
                               f"got {fmt}, allowed {formats}"))

    min_w, min_h = checks.get("min_width"), checks.get("min_height")
    if min_w or min_h:
        ok = width >= (min_w or 0) and height >= (min_h or 0)
        out.gates.append(_gate("dimensions", ok, f"{width}x{height}"))
    out.measures["width"], out.measures["height"] = width, height

    if checks.get("not_blank"):
        std = float(gray.std())
        out.gates.append(_gate("not_blank", std > NOT_BLANK_STD_FLOOR,
                               f"pixel stddev {std:.2f} (floor {NOT_BLANK_STD_FLOOR})"))

    required = checks.get("must_read_text")
    if required:
        # a single string = one contiguous phrase (a headline); a list = each
        # string matched independently (UI labels interleaved with other text)
        items = required if isinstance(required, list) else [required]
        fragments = _get_ocr()(output_path)
        joined = _normalize_ocr(" ".join(fragments))
        per_item = {item: _match_ratio(joined, item) for item in items}
        out.measures["ocr_text"] = " ".join(fragments)
        out.measures["ocr_required"] = required
        out.measures["ocr_match"] = round(sum(per_item.values()) / len(per_item), 4)
        if len(per_item) > 1:
            out.measures["ocr_match_per_item"] = per_item
        # OCR is a measured fact feeding the text_accuracy criterion, not a
        # validity gate (§4: it belongs to the `measured` stage) — a wrong
        # label is a scored failure, not an invalid file.

    if scenario.task in ("image_edit", "inpaint_mask"):
        _check_preservation(scenario, output_path, assets or {}, out)

    return out


# --------------------------------------------------------------------------
# OCR — rapidocr preferred, pytesseract fallback; both free and local
# --------------------------------------------------------------------------

_OCR_ENGINE = None


class OCRUnavailable(RuntimeError):
    pass


def _get_ocr():
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()

        def run(path: Path) -> list[str]:
            result, _ = engine(str(path))
            return [item[1] for item in (result or [])]

        _OCR_ENGINE = run
        return run
    except ImportError:
        pass
    try:
        import pytesseract
        pytesseract.get_tesseract_version()

        def run(path: Path) -> list[str]:
            text = pytesseract.image_to_string(Image.open(path))
            return [line for line in text.splitlines() if line.strip()]

        _OCR_ENGINE = run
        return run
    except Exception:
        raise OCRUnavailable(
            "no OCR engine: pip install rapidocr-onnxruntime, or install "
            "tesseract + pytesseract. A scenario with must_read_text cannot "
            "be checked without one.")


def _normalize_ocr(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", text.upper())).strip()


def ocr_match(image_path: Path, required: str) -> tuple[str, float]:
    """Returns (all text found, fuzzy match ratio 0..1 for the required text)."""
    fragments = _get_ocr()(image_path)
    joined = _normalize_ocr(" ".join(fragments))
    return " ".join(fragments), _match_ratio(joined, required)


def _match_ratio(joined_normalized: str, required: str) -> float:
    target = _normalize_ocr(required)
    joined = joined_normalized
    if not target:
        return 1.0
    if not joined:
        return 0.0
    if target in joined or target.replace(" ", "") in joined.replace(" ", ""):
        # OCR often drops/adds spaces — a formatting convention, not an error
        # in the rendered text (same principle as the WER normalization, §13)
        return 1.0
    # best window of the target's length over the joined text
    best = 0.0
    window, step = len(target), max(1, len(target) // 8)
    for i in range(0, max(1, len(joined) - window + step), step):
        best = max(best, SequenceMatcher(None, target, joined[i:i + window]).ratio())
        if best >= 0.999:
            break
    return round(best, 4)


# --------------------------------------------------------------------------
# Edit preservation — global pHash bound; mask-scoped SSIM ONLY when the
# scenario supplies the region. No automatic segmentation, ever.
# --------------------------------------------------------------------------

def phash_distance(a: Path, b: Path) -> int:
    import imagehash
    with Image.open(a) as ia, Image.open(b) as ib:
        return int(imagehash.phash(ia) - imagehash.phash(ib))  # numpy int -> JSON-safe


def ssim_outside_region(source: Path, result: Path,
                        mask: Path | None = None,
                        bbox: list | tuple | None = None) -> float:
    """SSIM between source and result over the pixels OUTSIDE the declared
    edit region (mask image: nonzero = edited; bbox: [x0, y0, x1, y1])."""
    from skimage.metrics import structural_similarity

    with Image.open(source) as im:
        src = np.asarray(im.convert("L"), dtype=np.float64)
    with Image.open(result) as im:
        res_img = im.convert("L")
        if res_img.size != (src.shape[1], src.shape[0]):
            res_img = res_img.resize((src.shape[1], src.shape[0]))
        res = np.asarray(res_img, dtype=np.float64)

    _, ssim_map = structural_similarity(src, res, data_range=255.0, full=True)

    if mask is not None:
        with Image.open(mask) as im:
            m_img = im.convert("L")
            if m_img.size != (src.shape[1], src.shape[0]):
                m_img = m_img.resize((src.shape[1], src.shape[0]))
            edited = np.asarray(m_img) > 127
    elif bbox is not None:
        edited = np.zeros(src.shape, dtype=bool)
        x0, y0, x1, y1 = [int(v) for v in bbox]
        edited[y0:y1, x0:x1] = True
    else:
        raise ValueError("ssim_outside_region needs a mask or bbox — region "
                         "scoping exists only when the scenario declares it")

    outside = ~edited
    if not outside.any():
        raise ValueError("mask/bbox covers the whole image; nothing to preserve")
    return float(ssim_map[outside].mean())


def _check_preservation(scenario, output_path: Path, assets: dict, out: CheckOutcome) -> None:
    cfg = (scenario.checks or {}).get("preservation")
    if not cfg:
        return
    source = assets.get("source")
    if source is None:
        out.gates.append(_gate("preservation", False, "no frozen source asset"))
        return
    method = cfg.get("method", "phash")
    mask, bbox = assets.get("mask"), scenario.inputs.get("bbox")
    if method == "ssim" and (mask is not None or bbox is not None):
        score = ssim_outside_region(Path(source), output_path, mask=mask, bbox=bbox)
        ok = score >= cfg.get("min", 0.92)
        out.measures["preservation_ssim_outside"] = round(score, 4)
        out.measures["preservation_ssim_min"] = cfg.get("min", 0.92)
        out.gates.append(_gate("preservation", ok,
                               f"SSIM outside region {score:.4f} (min {cfg.get('min', 0.92)})"))
    else:
        # GLOBAL bound — the default, and the only option without a declared region
        dist = phash_distance(Path(source), output_path)
        ok = dist <= cfg.get("max_distance", 12)
        out.measures["preservation_phash_distance"] = dist
        out.measures["preservation_phash_max"] = cfg.get("max_distance", 12)
        out.gates.append(_gate("preservation", ok,
                               f"pHash distance {dist} (max {cfg.get('max_distance', 12)})"))
