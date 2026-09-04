"""
Clips into a self-contained HTML file: the one place that encodes for delivery.

WHY THIS IS ITS OWN MODULE, and the same reason `normalize.py` and
`voiceprint.py` are: the encode settings ARE the evidence. A listener judging
voice quality off a 32 kbps encode is hearing our encoder, not the model - the
exact instrument-for-model confusion this project keeps digging out of its own
results. Putting the settings in one place means there is one answer to "what
did they actually hear", and it is recorded rather than remembered.

FORMAT: MP3. Opus is roughly twice as efficient for speech (all 242 clips are
13 MB against MP3's 22 MB, measured 2026-09-04), and it was still the wrong
choice here. Ogg/Opus needs Safari 17.4+, the file goes to an outside team
whose browsers we cannot survey, and a silent player is indistinguishable from
a bad model. MP3 plays everywhere. The size was only ever a constraint while
the delivery was a published artifact with a 16 MB cap; delivering a file
removed the cap, so the compatible format wins outright.

BITRATE. libsndfile's default lands near 61 kbps on this bank's speech, which
is the shipped default. `compression_level` runs 0.0 (best, ~95 kbps) to
0.8 (~40 kbps); higher is smaller. The knob exists because a mail server is a
real constraint, not because a smaller file is a better one.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import soundfile as sf

# Above this, something has gone wrong with the inputs rather than the
# settings - fail rather than write a file nobody can send.
MAX_FILE_BYTES = 60 * 1024 * 1024


class AudioTooLarge(RuntimeError):
    """The rendered page exceeded what anyone can reasonably share."""


def clip_data_uri(path: Path, compression_level: float | None = None) -> str:
    """
    One clip as an `audio/mpeg` data URI, mixed to mono.

    A data URI rather than a relative path on purpose: the deliverable is a
    SINGLE file that gets mailed, dropped in Drive and opened from a desktop.
    A relative path survives none of that, and a player pointing at a moved
    file fails exactly the way a broken player does - silently.
    """
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    buf = io.BytesIO()
    kwargs = {} if compression_level is None else {"compression_level": compression_level}
    sf.write(buf, x.mean(axis=1), sr, format="MP3", subtype="MPEG_LAYER_III", **kwargs)
    return "data:audio/mpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def clip_bytes(path: Path, compression_level: float | None = None) -> bytes:
    """The encoded clip itself, for writing beside the page rather than into it."""
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    buf = io.BytesIO()
    kwargs = {} if compression_level is None else {"compression_level": compression_level}
    sf.write(buf, x.mean(axis=1), sr, format="MP3", subtype="MPEG_LAYER_III", **kwargs)
    return buf.getvalue()


def safe_name(*parts: str) -> str:
    """
    A filename that survives every filesystem and every URL.

    `#` IS THE ONE THAT MATTERS. Variant clips are named `parent#variant`, and
    a `#` in a URL opens the fragment - which silently killed 192 of 242
    players on the internal board on 2026-09-04. The audio folder must not be
    able to reproduce that, so the separator never reaches a filename.
    """
    import re

    slug = "--".join(str(p) for p in parts if p)
    slug = slug.replace("#", "-").replace("/", "-")
    return re.sub(r"[^A-Za-z0-9._-]", "-", slug)


def fmt_size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def guard_size(n_bytes: int, path: Path) -> None:
    """Refuse to hand over a file that cannot be delivered."""
    if n_bytes > MAX_FILE_BYTES:
        raise AudioTooLarge(
            f"{path.name} is {fmt_size(n_bytes)}, over the {fmt_size(MAX_FILE_BYTES)} "
            f"ceiling. Raise --audio-quality (higher is smaller, 0.8 is about 40 kbps) "
            f"or ship the clips as a folder beside the HTML instead of inlining them."
        )
