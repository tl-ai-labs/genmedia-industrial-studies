"""Video checks: gates (valid at all?) and measures (facts with a right
answer) — pure Python, no ffmpeg/opencv. A ~100-line ISO BMFF (MP4) box walk
reads everything the gates need straight from the container headers:

  * mvhd (movie header)  -> duration = duration / timescale
  * trak/tkhd            -> track width x height (16.16 fixed point); the
                            video track is the one with nonzero dimensions

Gates: decodes (well-formed mp4 with a movie header and a sized video
track), duration within the scenario's min/max_duration_s band, minimum
dimensions. Measures: duration_s, width, height, plus the declared bounds
and the target dimensions the brief implies — scoring.py maps those to the
measured technical_compliance criterion.

Phase B (deliberately NOT here): the sheet's optical-flow, shot-boundary,
frame-similarity and particle/colour-stability checks all need decoded
frames, i.e. a real video toolchain (ffmpeg/pyav + opencv). They join as
additional measures when that dependency is accepted; the container-level
gates above stay valid unchanged.

Registered in runner/checks.py CHECK_SUITES; the CheckOutcome contract and
suite selection live there.
"""
from __future__ import annotations

from pathlib import Path

from ..checks import CheckOutcome, _gate

# resolution label -> the short side of the frame
_RES_SHORT_SIDE = {"720p": 720, "1080p": 1080, "4k": 2160}


# --------------------------------------------------------------------------
# MP4 box parsing
# --------------------------------------------------------------------------

class Mp4ParseError(ValueError):
    pass


def _iter_boxes(buf: bytes, start: int, end: int):
    """Yield (type, body_start, box_end) for each box in buf[start:end]."""
    off = start
    while off + 8 <= end:
        size = int.from_bytes(buf[off:off + 4], "big")
        btype = buf[off + 4:off + 8]
        header = 8
        if size == 1:                       # 64-bit largesize
            if off + 16 > end:
                raise Mp4ParseError(f"truncated largesize box at {off}")
            size = int.from_bytes(buf[off + 8:off + 16], "big")
            header = 16
        elif size == 0:                     # box runs to the end of the file
            size = end - off
        if size < header or off + size > end:
            raise Mp4ParseError(f"malformed box {btype!r} at {off} (size {size})")
        yield btype, off + header, off + size
        off += size


def _u32(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 4], "big")


def _parse_mvhd(body: bytes) -> tuple[int, int]:
    """(timescale, duration ticks). Duration is 0 in fragmented movies."""
    if body[0] == 1:
        return _u32(body, 20), int.from_bytes(body[24:32], "big")
    return _u32(body, 12), _u32(body, 16)


def _parse_tkhd(body: bytes) -> tuple[int, int, int]:
    """(track_id, width, height); 16.16 fixed-point dims."""
    version = body[0]
    track_id = _u32(body, 20 if version == 1 else 12)
    off = 88 if version == 1 else 76        # fixed-layout fields before width
    return track_id, _u32(body, off) >> 16, _u32(body, off + 4) >> 16


def _parse_mdhd(body: bytes) -> tuple[int, int]:
    """(timescale, duration ticks) of one track's media."""
    if body[0] == 1:
        return _u32(body, 20), int.from_bytes(body[24:32], "big")
    return _u32(body, 12), _u32(body, 16)


def _parse_trex(body: bytes) -> tuple[int, int]:
    """(track_id, default_sample_duration) — the fragment defaults."""
    return _u32(body, 4), _u32(body, 12)


def _fragment_ticks(buf: bytes, body_start: int, box_end: int,
                    trex_default: dict[int, int]) -> dict[int, int]:
    """Sum sample durations (in media-timescale ticks) per track for one
    moof: tfhd carries the track and an optional default, trun carries the
    sample count and optionally per-sample durations."""
    ticks: dict[int, int] = {}
    for ttype, tstart, tend in _iter_boxes(buf, body_start, box_end):
        if ttype != b"traf":
            continue
        track_id, default = None, None
        for ftype, fstart, fend in _iter_boxes(buf, tstart, tend):
            body = buf[fstart:fend]
            if ftype == b"tfhd":
                flags = int.from_bytes(body[1:4], "big")
                track_id = _u32(body, 4)
                off = 8
                if flags & 0x1:       # base-data-offset (64-bit)
                    off += 8
                if flags & 0x2:       # sample-description-index
                    off += 4
                if flags & 0x8:       # default-sample-duration
                    default = _u32(body, off)
            elif ftype == b"trun" and track_id is not None:
                flags = int.from_bytes(body[1:4], "big")
                count = _u32(body, 4)
                off = 8
                if flags & 0x1:       # data-offset
                    off += 4
                if flags & 0x4:       # first-sample-flags
                    off += 4
                if flags & 0x100:     # per-sample durations present
                    step = 4 * sum(1 for bit in (0x100, 0x200, 0x400, 0x800) if flags & bit)
                    total = sum(_u32(body, off + i * step) for i in range(count))
                else:
                    per = default if default is not None else trex_default.get(track_id, 0)
                    total = count * per
                ticks[track_id] = ticks.get(track_id, 0) + total
    return ticks


def parse_mp4(path: Path) -> dict:
    """{"duration_s", "width", "height"} from the container headers.

    Duration comes from the first of: mvhd (normal movies); the sum of the
    fragments' sample durations over the video track's media timescale
    (fragmented MP4s — moof/mdat runs after a moov whose durations are 0,
    common in stock footage); the video track's mdhd. Raises Mp4ParseError
    on anything that is not a well-formed movie."""
    buf = Path(path).read_bytes()
    if len(buf) < 16:
        raise Mp4ParseError(f"file too small to be an mp4 ({len(buf)} bytes)")

    saw_ftyp = saw_moov = False
    movie_ts = movie_dur = 0
    tracks: dict[int, dict] = {}          # track_id -> {w, h, ts, dur}
    trex_default: dict[int, int] = {}
    frag_ticks: dict[int, int] = {}
    for btype, body_start, box_end in _iter_boxes(buf, 0, len(buf)):
        if btype == b"ftyp":
            saw_ftyp = True
        elif btype == b"moov":
            saw_moov = True
            for mtype, mstart, mend in _iter_boxes(buf, body_start, box_end):
                if mtype == b"mvhd":
                    movie_ts, movie_dur = _parse_mvhd(buf[mstart:mend])
                elif mtype == b"trak":
                    tid, w, h, ts, dur = None, 0, 0, 0, 0
                    for ttype, tstart, tend in _iter_boxes(buf, mstart, mend):
                        if ttype == b"tkhd":
                            tid, w, h = _parse_tkhd(buf[tstart:tend])
                        elif ttype == b"mdia":
                            for dtype, dstart, dend in _iter_boxes(buf, tstart, tend):
                                if dtype == b"mdhd":
                                    ts, dur = _parse_mdhd(buf[dstart:dend])
                    if tid is not None:
                        tracks[tid] = {"w": w, "h": h, "ts": ts, "dur": dur}
                elif mtype == b"mvex":
                    for xtype, xstart, xend in _iter_boxes(buf, mstart, mend):
                        if xtype == b"trex":
                            tid, default = _parse_trex(buf[xstart:xend])
                            trex_default[tid] = default
        elif btype == b"moof":
            for tid, t in _fragment_ticks(buf, body_start, box_end, trex_default).items():
                frag_ticks[tid] = frag_ticks.get(tid, 0) + t
    if not saw_ftyp:
        raise Mp4ParseError("no ftyp box — not an mp4 container")
    if not saw_moov:
        raise Mp4ParseError("no moov/mvhd box — movie header missing")
    # the video track is the one with real dimensions; audio traks are 0x0
    sized = {tid: t for tid, t in tracks.items() if t["w"] > 0 and t["h"] > 0}
    if not sized:
        raise Mp4ParseError("no track with nonzero dimensions — no video track")
    vid = max(sized, key=lambda tid: sized[tid]["w"] * sized[tid]["h"])
    v = sized[vid]

    if movie_ts > 0 and movie_dur > 0:
        duration_s = movie_dur / movie_ts
    elif frag_ticks.get(vid) and v["ts"] > 0:
        duration_s = frag_ticks[vid] / v["ts"]
    elif v["dur"] > 0 and v["ts"] > 0:
        duration_s = v["dur"] / v["ts"]
    else:
        raise Mp4ParseError("no duration: mvhd is zero, no fragments, no mdhd duration")
    return {"duration_s": duration_s, "width": v["w"], "height": v["h"]}


# --------------------------------------------------------------------------
# The check suite
# --------------------------------------------------------------------------

def _target_dims(params: dict) -> tuple[int | None, int | None]:
    """The frame size the brief's resolution + aspect ratio imply."""
    short = _RES_SHORT_SIDE.get(str(params.get("resolution", "")).lower())
    if short is None:
        return None, None
    try:
        num, den = (int(x) for x in str(params.get("aspect_ratio", "16:9")).split(":"))
    except ValueError:
        return None, None
    if num >= den:                          # landscape: height is the short side
        return round(short * num / den), short
    return short, round(short * den / num)  # portrait: width is the short side


def check_video(scenario, output_path: Path, assets: dict | None = None) -> CheckOutcome:
    out = CheckOutcome()
    checks = scenario.checks or {}

    try:
        info = parse_mp4(Path(output_path))
    except Exception as e:
        out.gates.append(_gate("decodes", False, f"{type(e).__name__}: {e}"))
        return out
    width, height = info["width"], info["height"]
    duration = info["duration_s"]
    out.gates.append(_gate("decodes", True,
                           f"mp4 {width}x{height}, {duration:.2f}s"))
    out.measures["duration_s"] = round(duration, 3)
    out.measures["width"], out.measures["height"] = width, height

    lo, hi = checks.get("min_duration_s"), checks.get("max_duration_s")
    if lo is not None or hi is not None:
        ok = (lo is None or duration >= lo) and (hi is None or duration <= hi)
        out.gates.append(_gate("duration", ok,
                               f"{duration:.2f}s (allowed {lo}-{hi}s)"))
        if lo is not None:
            out.measures["min_duration_s"] = lo
        if hi is not None:
            out.measures["max_duration_s"] = hi

    min_w, min_h = checks.get("min_width"), checks.get("min_height")
    if min_w or min_h:
        ok = width >= (min_w or 0) and height >= (min_h or 0)
        out.gates.append(_gate("dimensions", ok, f"{width}x{height}"))

    target_w, target_h = _target_dims(scenario.params or {})
    if target_w and target_h:
        # feeds the measured technical_compliance criterion: delivered dims
        # graded against what the brief asked for (scoring.py owns the map)
        out.measures["target_width"] = target_w
        out.measures["target_height"] = target_h

    return out
