"""
Offline fixtures: three fake run folders that follow the lanes' contract.

Nothing here touches a real run or a provider. The media are the smallest
valid files of each kind, each carrying a metadata chunk that NAMES ITS
MODEL - so the blinding tests can prove the exporter removed it.
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GEM = "gemini-3-pro-image-vertex"
GPT = "gpt-image-2-high"
OMNI = "omni-flash-vertex"
SEED = "seedance-2-5"
GTTS = "gemini-3-1-flash-tts"
ELEV = "elevenlabs-multilingual-v2"
INDUSTRY = {"IMG-TXT-01": "Ads", "IMG-BRAND-04": "Ecommerce & Retail", "VID-CIN-01": "Studios",
            "vr-ecom-06": "Ecommerce & Retail"}


# ----------------------------------------------------------------- media

def png_with_text(text: str, w: int = 2, h: int = 2) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Software\x00" + text.encode())
            + chunk(b"caBX", b"c2pa:" + text.encode())
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def jpeg_with_exif(text: str) -> bytes:
    app1 = b"Exif\x00\x00" + text.encode()
    seg = lambda m, d: b"\xff" + bytes([m]) + struct.pack(">H", len(d) + 2) + d  # noqa: E731
    return (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
            + seg(0xE1, app1) + seg(0xFE, b"comment " + text.encode())
            + seg(0xDB, b"\x00" + bytes(64)) + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
            + b"\x12\x34" + b"\xff\xd9")


def wav_with_info(text: str) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    data = bytes(64)
    info = b"INFO" + b"ISFT" + struct.pack("<I", len(text) + 1) + text.encode() + b"\x00"
    if len(info) % 2:
        info += b"\x00"
    body = (b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"LIST" + struct.pack("<I", len(info)) + info
            + b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def mp3_with_id3(text: str) -> bytes:
    frame = b"TSSE\x00\x00\x00" + bytes([len(text) + 1]) + b"\x00\x00\x00" + text.encode()
    size = len(frame)
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    header = b"ID3\x04\x00\x00" + syncsafe + frame
    frames = b"\xff\xfb\x90\x00" + bytes(100)
    tag = b"TAG" + text.encode().ljust(125, b"\x00")
    return header + frames + tag


def mp4_stub(text: str, payload: bytes = b"samples") -> bytes:
    """Valid box structure, not decodable video: ftyp, moov(mvhd, udta(text)),
    mdat(payload). The model name sits where ffmpeg/providers put theirs."""
    def box(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", 8 + len(d)) + t + d
    ilst = box(b"\xa9too", box(b"data", bytes(8) + text.encode()))
    meta = box(b"meta", bytes(4) + box(b"hdlr", bytes(24)) + box(b"ilst", ilst))
    moov = box(b"moov", box(b"mvhd", bytes(100)) + box(b"udta", meta))
    return box(b"ftyp", b"isom" + bytes(4)) + moov + box(b"mdat", payload)


# ------------------------------------------------------------------ runs

def _write_run(root: Path, modality: str, run_id: str, models: tuple[str, str],
               scenarios: list[dict], media: dict[tuple[str, str], tuple[str, bytes]],
               scores: dict[tuple[str, str], float | None],
               inputs: dict[str, bytes] | None = None) -> Path:
    run = root / modality / "runs" / run_id
    (run / "scenarios").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"run_id": run_id, "modality": modality, "state": "reported"}))
    import yaml
    for s in scenarios:
        (run / "scenarios" / f"{s['id']}.yaml").write_text(yaml.safe_dump(s))
    for (sid, mid), (ext, data) in media.items():
        d = run / "outputs" / modality / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{mid}.{ext}").write_bytes(data)
    with (run / "scores.jsonl").open("w") as f:
        for (sid, mid), score in scores.items():
            f.write(json.dumps({"run_id": run_id, "scenario_id": sid, "model_id": mid,
                                "status": "scored" if score is not None else "failed",
                                "score": score}) + "\n")
    for rel, data in (inputs or {}).items():
        p = root / modality / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    imap = root / modality / "configs" / "industry_map.yaml"
    imap.parent.mkdir(parents=True, exist_ok=True)
    imap.write_text(yaml.safe_dump({"scenarios": {
        s["id"]: {"primary": INDUSTRY.get(s["id"], "Ecommerce & Retail")} for s in scenarios}}))
    return run


@pytest.fixture
def fake_repo(tmp_path: Path) -> dict:
    """{'root', 'image', 'video', 'voice'} - three runs, two models each."""
    root = tmp_path / "repo"

    image = _write_run(
        root, "image", "2026-09-01_000000_image", (GEM, GPT),
        scenarios=[
            {"id": "IMG-TXT-01", "modality": "image", "task": "text_to_image",
             "title": "Exact headline poster", "prompt": "Design an event poster."},
            {"id": "IMG-BRAND-04", "modality": "image", "task": "image_edit",
             "title": "Logo on packaging", "prompt": "Apply the supplied logo.",
             "inputs": {"source": "assets/bank/IMG-BRAND-04-source.png",
                        "reference": "assets/bank/IMG-BRAND-04-reference.png",
                        "bbox": "10,10,50,50"}},
            {"id": "IMG-FAIL-01", "modality": "image", "task": "text_to_image",
             "title": "One arm failed", "prompt": "Anything."},
        ],
        media={("IMG-TXT-01", GEM): ("png", png_with_text(GEM)),
               ("IMG-TXT-01", GPT): ("png", png_with_text(GPT)),
               ("IMG-BRAND-04", GEM): ("png", png_with_text(GEM)),
               ("IMG-BRAND-04", GPT): ("jpg", jpeg_with_exif(GPT)),
               ("IMG-FAIL-01", GEM): ("png", png_with_text(GEM))},
        scores={("IMG-TXT-01", GEM): 8.0, ("IMG-TXT-01", GPT): 9.0,
                ("IMG-BRAND-04", GEM): 9.1, ("IMG-BRAND-04", GPT): 8.9,
                ("IMG-FAIL-01", GEM): 7.0, ("IMG-FAIL-01", GPT): None},
        inputs={"assets/bank/IMG-BRAND-04-source.png": png_with_text("source"),
                "assets/bank/IMG-BRAND-04-reference.png": png_with_text("reference")},
    )

    video = _write_run(
        root, "video", "2026-09-03_000000_video", (OMNI, SEED),
        scenarios=[{"id": "VID-CIN-01", "modality": "video", "task": "text_to_video",
                    "title": "Slow dolly-in", "prompt": "A slow dolly-in."}],
        media={("VID-CIN-01", OMNI): ("mp4", mp4_stub(OMNI)),
               ("VID-CIN-01", SEED): ("mp4", mp4_stub(SEED))},
        scores={("VID-CIN-01", OMNI): 7.45, ("VID-CIN-01", SEED): 9.0},
    )
    # A preview beside the run wins over the original.
    (video / "previews").mkdir()
    (video / "previews" / f"VID-CIN-01--{OMNI}.mp4").write_bytes(mp4_stub(OMNI, b"preview-a"))
    (video / "previews" / f"VID-CIN-01--{SEED}.mp4").write_bytes(mp4_stub(SEED, b"preview-b"))

    voice = _write_run(
        root, "voice", "2026-09-05_000000_voice", (GTTS, ELEV),
        scenarios=[{"id": "vr-ecom-06", "modality": "voice", "task": "styled_tts",
                    "title": "KYC readback", "input": {"script": "Your reference is A B C."}}],
        media={("vr-ecom-06#bare", GTTS): ("wav", wav_with_info(GTTS)),
               ("vr-ecom-06#bare", ELEV): ("mp3", mp3_with_id3(ELEV))},
        scores={("vr-ecom-06#bare", GTTS): 0.80, ("vr-ecom-06#bare", ELEV): 0.82},
    )
    return {"root": root, "image": image, "video": video, "voice": voice}


ALL_MODELS = (GEM, GPT, OMNI, SEED, GTTS, ELEV)
