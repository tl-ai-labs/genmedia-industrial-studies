"""
Deterministic audio gates, exercised on synthesised audio.

No key, no network, no provider - every gate is proven against a file this
test wrote, so a broken gate is caught before it silently passes a broken
clip in a real run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from runner.checks import decode, run_checks
from runner.mos import SignalQualityPredictor

RATE = 24000


@dataclass
class FakeScenario:
    id: str = "voi-test"
    text: str = "hello there this is a test of the voice lane"
    task: str = "text_to_speech"
    language: str | None = None
    style: str | None = None
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def max_wer(self):
        v = self.checks.get("max_wer")
        return float(v) if v is not None else None


def speechlike(seconds: float, rate: int = RATE, amp: float = 0.25) -> np.ndarray:
    """A band-limited, amplitude-modulated tone - loud enough and not silent."""
    t = np.linspace(0, seconds, int(seconds * rate), endpoint=False)
    carrier = np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 420 * t)
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 3.0 * t)
    return (amp * carrier * envelope).astype(np.float64)


def write(tmp: Path, name: str, x: np.ndarray, rate: int = RATE) -> Path:
    p = tmp / name
    sf.write(str(p), x, rate, subtype="PCM_16")
    return p


@pytest.fixture
def predictor():
    return SignalQualityPredictor()


def test_decode_measures_duration_and_loudness(tmp_path):
    p = write(tmp_path, "a.wav", speechlike(3.0))
    facts = decode(p)
    assert 2.9 < facts.duration_s < 3.1
    assert facts.sample_rate == RATE
    assert -45 < facts.rms_dbfs < -6


def test_non_audio_file_fails_the_decode_gate(tmp_path, predictor):
    bad = tmp_path / "not-audio.wav"
    bad.write_bytes(b"this is not a wav file at all")
    report = run_checks(FakeScenario(), "m1", bad, None, None, predictor)
    assert not report.passed
    assert "decodes" in report.failed_gates
    # Nothing downstream should have run.
    assert report.wer is None


def test_duration_outside_the_declared_range_fails(tmp_path, predictor):
    p = write(tmp_path, "short.wav", speechlike(1.0))
    scenario = FakeScenario(checks={"duration_s": {"min": 8, "max": 30}})
    report = run_checks(scenario, "m1", p, None, None, predictor)
    assert "duration_in_range" in report.failed_gates


def test_silent_clip_fails_not_silent(tmp_path, predictor):
    p = write(tmp_path, "silent.wav", np.zeros(RATE * 2))
    report = run_checks(FakeScenario(), "m1", p, None, None, predictor)
    assert "not_silent" in report.failed_gates


def test_long_internal_silence_is_caught(tmp_path, predictor):
    x = np.concatenate([speechlike(2.0), np.zeros(int(RATE * 3.0)), speechlike(2.0)])
    p = write(tmp_path, "gap.wav", x)
    scenario = FakeScenario(checks={"max_silence_s": 2.0})
    report = run_checks(scenario, "m1", p, None, None, predictor)
    assert "silence_within_bounds" in report.failed_gates
    assert report.measurements["max_internal_silence_s"] > 2.0


def test_trailing_silence_is_caught_too(tmp_path, predictor):
    x = np.concatenate([speechlike(2.0), np.zeros(int(RATE * 4.0))])
    p = write(tmp_path, "trail.wav", x)
    report = run_checks(FakeScenario(checks={"max_silence_s": 2.0}), "m1", p, None, None, predictor)
    assert "silence_within_bounds" in report.failed_gates


def test_sustained_clipping_is_caught(tmp_path, predictor):
    x = speechlike(2.0, amp=0.3)
    x[RATE : RATE + 2000] = 1.0  # a long hard-clipped run
    p = write(tmp_path, "clip.wav", x)
    report = run_checks(FakeScenario(checks={"no_clipping": True}), "m1", p, None, None, predictor)
    assert "no_clipping" in report.failed_gates


def test_clean_clip_passes_every_gate(tmp_path, predictor):
    p = write(tmp_path, "clean.wav", speechlike(4.0))
    scenario = FakeScenario(checks={"duration_s": {"min": 2, "max": 10}, "max_silence_s": 2.0, "no_clipping": True})
    report = run_checks(scenario, "m1", p, None, None, predictor)
    assert report.passed, report.failed_gates


def test_wer_gate_runs_when_a_transcript_is_supplied(tmp_path, predictor):
    p = write(tmp_path, "ok.wav", speechlike(4.0))
    scenario = FakeScenario(
        text="the reference number is 4-8-2-9-1-6",
        checks={"duration_s": {"min": 2, "max": 10}, "max_wer": 0.10},
    )
    good = run_checks(scenario, "m1", p, "The reference number is 482916.", None, predictor)
    assert good.passed
    assert good.measurements["normalized_wer"] == 0.0

    bad = run_checks(scenario, "m1", p, "The reference number is 999999.", None, predictor)
    assert not bad.passed
    assert "wer_within_max" in bad.failed_gates


def test_wer_reference_override_supports_a_negative_control(tmp_path, predictor):
    p = write(tmp_path, "nc.wav", speechlike(4.0))
    scenario = FakeScenario(
        text="tracking number 5-5-1-2-0-8 arriving friday",
        checks={
            "duration_s": {"min": 2, "max": 10},
            "max_wer": 0.10,
            "wer_reference": "tracking number 9-9-4-3-7-1 arriving tuesday",
        },
    )
    # The transcript matches what was SPOKEN, but the gate measures against
    # the deliberately different reference - so it must fail.
    report = run_checks(scenario, "m1", p, "tracking number 551208 arriving friday", None, predictor)
    assert not report.passed
    assert "wer_within_max" in report.failed_gates
    assert report.measurements["wer_reference_is_script"] is False


def test_asr_failure_leaves_text_accuracy_unmeasured_never_zero(tmp_path, predictor):
    p = write(tmp_path, "noasr.wav", speechlike(4.0))
    scenario = FakeScenario(checks={"duration_s": {"min": 2, "max": 10}, "max_wer": 0.05})
    report = run_checks(scenario, "m1", p, None, "ASR timed out three times", predictor)
    # The cell is still valid - the model did its job; our measurement failed.
    assert report.passed
    assert report.measurements["text_accuracy_unmeasured"] is True
    assert "normalized_wer" not in report.measurements


def test_quality_measurement_is_labelled_as_not_a_mos(tmp_path, predictor):
    p = write(tmp_path, "q.wav", speechlike(3.0))
    report = run_checks(FakeScenario(), "m1", p, None, None, predictor)
    assert 1.0 <= report.measurements["audio_quality_1_5"] <= 5.0
    assert report.measurements["audio_quality_is_mos"] is False


def test_check_record_carries_the_normalized_pair_for_replay(tmp_path, predictor):
    p = write(tmp_path, "r.wav", speechlike(3.0))
    scenario = FakeScenario(text="press 3 for support", checks={"max_wer": 0.5})
    rec = run_checks(scenario, "m1", p, "Press three for support.", None, predictor).as_record
    assert rec["wer"]["normalized_script"] == "press three for support"
    assert rec["wer"]["normalized_transcript"] == "press three for support"
    assert rec["transcript_raw"] == "Press three for support."


# --------------------------------------------------------------------------
# Digit extraction. Fixed 2026-09-01 after a real run: a LONE digit word in
# ordinary prose was being counted as a digit.
# --------------------------------------------------------------------------

def test_a_lone_digit_word_in_prose_is_not_a_digit():
    """
    "read this one time code ... seven three nine one five two" yielded
    '1739152' - the "one" from "one time code". The substring check happened
    to survive it, which is how a bug like this stays hidden.
    """
    from runner.checks import extract_digit_sequence

    assert extract_digit_sequence("read this one time code digit by digit seven three nine one five two") == "739152"
    assert extract_digit_sequence("state only the last four digits four four one nine") == "4419"
    assert extract_digit_sequence("there is one apple and four pears") == ""


def test_both_written_forms_still_reduce_identically():
    from runner.checks import extract_digit_sequence

    for t in ("4471 8802 3915", "447188023915",
              "four four seven one eight eight zero two three nine one five",
              "Four four seven one, eight eight oh two, three nine one five."):
        assert extract_digit_sequence(t) == "447188023915"


def test_a_single_raw_number_still_counts():
    from runner.checks import extract_digit_sequence

    assert extract_digit_sequence("the code is 4419") == "4419"


def test_longest_run_ignores_prose_words_too():
    from runner.checks import longest_digit_run

    # "last four digits" must not extend the run past the four card digits.
    assert longest_digit_run("card ending four four one nine state only the last four digits") == 4
    assert longest_digit_run("your card number is 4111 1111 1111 4419") == 16


# --------------------------------------------------------------------------
# The ASR repeat guard. Added 2026-09-02 from a real failure: gemini-2.5-flash
# transcribed a 48.8s clip twice, reproducibly, fabricating a WER of 1.02 from
# audio that a half-clip transcription proved contained ONE clean read.
# --------------------------------------------------------------------------

# The genuine transcript from that run, truncated to its two halves.
_HALF = ("I found two options that match your requirements for a lightweight laptop "
         "under 75,000 rupees. The Asus VivoBook S14 has 16 GB of RAM, a 512 GB SSD, "
         "and a 14-inch OLED display. It is currently priced at 69,999 rupees. Based "
         "on your preference for battery life and a larger display, I would recommend "
         "the Lenovo.")


def test_a_doubled_transcript_is_collapsed_to_one_pass():
    from runner.asr import collapse_repeated_transcript

    out, collapsed = collapse_repeated_transcript(_HALF + _HALF)
    assert collapsed is True
    assert out.count("I found two options") == 1
    assert len(out.split()) == pytest.approx(len(_HALF.split()), abs=2)


def test_a_normal_transcript_is_left_completely_alone():
    from runner.asr import collapse_repeated_transcript

    out, collapsed = collapse_repeated_transcript(_HALF)
    assert collapsed is False and out == _HALF


def test_short_transcripts_are_never_collapsed():
    """A short utterance that repeats a phrase must survive intact."""
    from runner.asr import collapse_repeated_transcript

    text = "press one press one"
    out, collapsed = collapse_repeated_transcript(text)
    assert collapsed is False and out == text


def test_a_script_that_genuinely_repeats_a_long_line_is_not_truncated():
    """
    The guard must be conservative. Two DIFFERENT long halves stay whole even
    though the transcript is long enough to be a candidate.
    """
    from runner.asr import collapse_repeated_transcript

    a = " ".join(f"alpha{i}" for i in range(40))
    b = " ".join(f"beta{i}" for i in range(40))
    out, collapsed = collapse_repeated_transcript(a + " " + b)
    assert collapsed is False and out.endswith("beta39")


def test_collapsing_turns_the_fabricated_wer_back_into_a_real_one():
    """The point of the whole guard, end to end."""
    from runner.asr import collapse_repeated_transcript
    from runner.normalize import normalized_wer

    script = _HALF
    fabricated = normalized_wer(script, _HALF + _HALF).wer
    repaired = normalized_wer(script, collapse_repeated_transcript(_HALF + _HALF)[0]).wer
    assert fabricated > 0.9          # a perfect clip scored as a total failure
    # Near-zero, not exactly zero: the two halves join without a space
    # ("...the Lenovo.I found two...") exactly as the real transcript did, so
    # one boundary token survives. That is a rounding error next to a
    # fabricated 1.02, and it stays comfortably inside any sane gate.
    assert repaired < 0.05
