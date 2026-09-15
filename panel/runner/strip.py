"""
Strip the metadata that could name a model, without decoding the media.

WHY THIS IS PART OF BLINDING. Renaming a file to `item-0a3f.png` hides the
model from the URL. It does not hide it from the file: GPT Image output
carries a C2PA manifest (a `caBX` PNG chunk) that any content-credentials
inspector renders as the producer's name, and encoders write their own name
into tEXt chunks, EXIF, RIFF INFO lists and ID3 tags. A reviewer who saves
the file and looks is unblinded, silently, with the vote still looking valid.

WHAT IS KEPT. Only the chunks the decoder needs to render the pixels or
samples. Nothing is re-encoded - the bytes the model produced reach the
reviewer untouched, which is what a fair comparison requires.

MP4 IS NEUTRALISED IN PLACE, NOT SHRUNK. Its metadata lives in `udta` and
`meta` boxes (ffmpeg's `©too`, a provider's title/comment). Removing bytes
before `mdat` would shift the sample offsets in `stco`/`co64` and break
playback, so those boxes are renamed to `free` and their payload zeroed -
same size, same offsets, and every parser skips a `free` box. WebM and MOV
fall through unchanged and the export summary says so.
"""

from __future__ import annotations

import struct

MP4_CONTAINERS = {b"moov", b"trak"}
MP4_DROP = {b"udta", b"meta"}


def strip_mp4(data: bytes) -> bytes:
    if len(data) < 12 or data[4:8] != b"ftyp":
        return data
    buf = bytearray(data)

    def walk(start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            size = struct.unpack(">I", buf[pos:pos + 4])[0]
            typ = bytes(buf[pos + 4:pos + 8])
            hdr = 8
            if size == 1 and pos + 16 <= end:
                size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
                hdr = 16
            elif size == 0:
                size = end - pos
            if size < hdr or pos + size > end:
                break
            if typ in MP4_DROP:
                buf[pos + 4:pos + 8] = b"free"
                buf[pos + hdr:pos + size] = bytes(size - hdr)
            elif typ in MP4_CONTAINERS:
                walk(pos + hdr, pos + size)
            pos += size

    walk(0, len(buf))
    return bytes(buf)

PNG_SIG = b"\x89PNG\r\n\x1a\n"
# Critical chunks plus the ancillary ones that change how pixels render.
PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM",
            b"sRGB", b"iCCP", b"sBIT", b"bKGD", b"pHYs", b"acTL", b"fcTL", b"fdAT"}


def strip_png(data: bytes) -> bytes:
    if not data.startswith(PNG_SIG):
        return data
    out = bytearray(PNG_SIG)
    pos = len(PNG_SIG)
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            break
        if ctype in PNG_KEEP:
            out += data[pos:end]
        pos = end
        if ctype == b"IEND":
            break
    return bytes(out)


def strip_jpeg(data: bytes) -> bytes:
    """Drop APP1..APP15 (EXIF, XMP, C2PA's APP11) and COM segments. APP0
    (JFIF) and APP14 (Adobe colour transform) stay - decoders read them."""
    if not data.startswith(b"\xff\xd8"):
        return data
    out = bytearray(b"\xff\xd8")
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xD9:                     # EOI
            out += data[pos:pos + 2]
            return bytes(out)
        if marker == 0xDA:                     # SOS: entropy data follows to EOI
            out += data[pos:]
            return bytes(out)
        seglen = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        end = pos + 2 + seglen
        drop = (0xE1 <= marker <= 0xEF and marker != 0xEE) or marker == 0xFE
        if not drop:
            out += data[pos:end]
        pos = end
    out += data[pos:]
    return bytes(out)


def strip_wav(data: bytes) -> bytes:
    """Keep `fmt `, `data` and the chunks a decoder may need (`fact`, `cue `);
    drop `LIST` (INFO: software, artist), `id3 `, `bext` and anything else."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    keep = {b"fmt ", b"data", b"fact", b"cue ", b"smpl", b"inst"}
    body = bytearray()
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        end = pos + 8 + size + (size & 1)
        if cid in keep:
            body += data[pos:min(end, len(data))]
        pos = end
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + bytes(body)


def strip_mp3(data: bytes) -> bytes:
    """Remove an ID3v2 header block and an ID3v1 trailer. Frames untouched."""
    if data[:3] == b"ID3" and len(data) >= 10:
        flags = data[5]
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
               ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        start = 10 + size + (10 if flags & 0x10 else 0)
        data = data[start:]
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        data = data[:-128]
    return data


STRIPPERS = {
    ".png": strip_png,
    ".jpg": strip_jpeg, ".jpeg": strip_jpeg,
    ".wav": strip_wav,
    ".mp3": strip_mp3,
    ".mp4": strip_mp4,
}


def strip_bytes(data: bytes, suffix: str) -> tuple[bytes, bool]:
    """Returns (bytes, stripped?). False means the format is passed through."""
    fn = STRIPPERS.get(suffix.lower())
    if fn is None:
        return data, False
    return fn(data), True
