"""The pure-python MP4 container parser and the video check suite: gates
(decode, duration band, minimum dimensions) and measures (duration, dims,
declared bounds, target dims) — all against synthetic movies built in code
by conftest.minimal_mp4, so the box walk is exercised for real."""
import pytest

from runner.loaders import Scenario
from runner.video.checks import (Mp4ParseError, check_video, parse_mp4,
                                 _target_dims)
from tests.conftest import broken_mp4, minimal_mp4


def scenario(**overrides):
    base = dict(id="t-001", modality="video", task="text_to_video",
                prompt="p", expected="e",
                params={"duration_s": 8, "resolution": "1080p",
                        "aspect_ratio": "16:9", "audio": False},
                checks={"min_duration_s": 7.0, "max_duration_s": 9.0,
                        "min_width": 1280, "min_height": 720})
    base.update(overrides)
    return Scenario(**base)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def gate(outcome, name):
    return next(g for g in outcome.gates if g["gate"] == name)


# ---- parser ---------------------------------------------------------------

def test_parser_roundtrips_synthetic_mp4(tmp_path):
    p = write(tmp_path, "a.mp4", minimal_mp4(duration_s=8.0, width=1920, height=1080))
    info = parse_mp4(p)
    assert info["duration_s"] == pytest.approx(8.0)
    assert (info["width"], info["height"]) == (1920, 1080)


def test_parser_fractional_duration(tmp_path):
    p = write(tmp_path, "a.mp4", minimal_mp4(duration_s=7.25))
    assert parse_mp4(p)["duration_s"] == pytest.approx(7.25)


def test_parser_skips_audio_track_for_dimensions(tmp_path):
    p = write(tmp_path, "a.mp4",
              minimal_mp4(width=1280, height=720, audio_track=True))
    info = parse_mp4(p)
    assert (info["width"], info["height"]) == (1280, 720)


def test_parser_sums_fragmented_movie_duration(tmp_path):
    # stock-footage encoders write moov durations of 0 and put the real
    # timing in moof/traf/trun — the parser must add the fragments up
    from tests.conftest import fragmented_mp4
    p = write(tmp_path, "f.mp4", fragmented_mp4(duration_s=8.0, fragments=3))
    info = parse_mp4(p)
    assert info["duration_s"] == pytest.approx(8.0, abs=0.05)
    assert (info["width"], info["height"]) == (1280, 720)


def test_parser_reads_per_sample_fragment_durations(tmp_path):
    from tests.conftest import fragmented_mp4
    p = write(tmp_path, "f.mp4", fragmented_mp4(duration_s=5.0, fragments=2,
                                                 per_sample_durations=True))
    assert parse_mp4(p)["duration_s"] == pytest.approx(5.0, abs=0.05)


def test_fragmented_clip_passes_duration_gate(tmp_path):
    from tests.conftest import fragmented_mp4
    out = check_video(scenario(checks={"min_duration_s": 7.0, "max_duration_s": 9.0,
                                       "min_width": 1280, "min_height": 720}),
                      write(tmp_path, "f.mp4", fragmented_mp4(duration_s=8.0,
                                                               width=1920, height=1080)))
    assert out.passed and out.measures["duration_s"] == pytest.approx(8.0, abs=0.05)


def test_parser_rejects_garbage(tmp_path):
    with pytest.raises(Mp4ParseError):
        parse_mp4(write(tmp_path, "a.mp4", broken_mp4()))


def test_parser_rejects_truncated_movie(tmp_path):
    full = minimal_mp4()
    with pytest.raises(Mp4ParseError):
        parse_mp4(write(tmp_path, "a.mp4", full[:len(full) // 2]))


def test_parser_rejects_movie_without_moov(tmp_path):
    # a valid ftyp box alone is a container, not a movie
    ftyp = minimal_mp4()[:32]
    size = int.from_bytes(ftyp[:4], "big")
    with pytest.raises(Mp4ParseError, match="moov"):
        parse_mp4(write(tmp_path, "a.mp4", ftyp[:size]))


# ---- gates ----------------------------------------------------------------

def test_valid_clip_passes_gates(tmp_path):
    out = check_video(scenario(),
                      write(tmp_path, "a.mp4",
                            minimal_mp4(duration_s=8.0, width=1920, height=1080)))
    assert out.passed
    assert out.measures["duration_s"] == pytest.approx(8.0)
    assert out.measures["width"] == 1920 and out.measures["height"] == 1080


def test_garbage_fails_decode_gate_only(tmp_path):
    out = check_video(scenario(), write(tmp_path, "a.mp4", broken_mp4()))
    assert not out.passed
    assert out.gates == [out.gates[0]]      # only the decode gate ran
    assert not out.gates[0]["passed"]


def test_short_clip_fails_duration_gate(tmp_path):
    out = check_video(scenario(),
                      write(tmp_path, "a.mp4", minimal_mp4(duration_s=4.0,
                                                           width=1920, height=1080)))
    assert not gate(out, "duration")["passed"]
    assert gate(out, "decodes")["passed"]


def test_overlong_clip_fails_duration_gate(tmp_path):
    out = check_video(scenario(),
                      write(tmp_path, "a.mp4", minimal_mp4(duration_s=12.0,
                                                           width=1920, height=1080)))
    assert not gate(out, "duration")["passed"]


def test_small_clip_fails_dimension_gate(tmp_path):
    out = check_video(scenario(),
                      write(tmp_path, "a.mp4", minimal_mp4(duration_s=8.0,
                                                           width=640, height=360)))
    assert not gate(out, "dimensions")["passed"]


def test_720p_delivery_passes_gates_against_1080p_brief(tmp_path):
    # the gate is the validity floor; the 1080p shortfall is graded by the
    # measured technical_compliance criterion, not treated as invalid
    out = check_video(scenario(),
                      write(tmp_path, "a.mp4", minimal_mp4(duration_s=8.0,
                                                           width=1280, height=720)))
    assert out.passed
    assert out.measures["target_width"] == 1920
    assert out.measures["target_height"] == 1080


# ---- measures -------------------------------------------------------------

def test_measures_carry_declared_bounds(tmp_path):
    out = check_video(scenario(),
                      write(tmp_path, "a.mp4", minimal_mp4(duration_s=8.0,
                                                           width=1920, height=1080)))
    assert out.measures["min_duration_s"] == 7.0
    assert out.measures["max_duration_s"] == 9.0
    assert out.measures["target_width"] == 1920


def test_no_declared_checks_still_measures(tmp_path):
    out = check_video(scenario(checks={}, params={}),
                      write(tmp_path, "a.mp4", minimal_mp4()))
    assert out.passed                       # decode gate only
    assert out.measures["duration_s"] == pytest.approx(4.0)
    assert "target_width" not in out.measures


def test_target_dims_mapping():
    assert _target_dims({"resolution": "1080p", "aspect_ratio": "16:9"}) == (1920, 1080)
    assert _target_dims({"resolution": "720p", "aspect_ratio": "16:9"}) == (1280, 720)
    assert _target_dims({"resolution": "1080p", "aspect_ratio": "9:16"}) == (1080, 1920)
    assert _target_dims({"resolution": "", "aspect_ratio": "16:9"}) == (None, None)
    assert _target_dims({}) == (None, None)
