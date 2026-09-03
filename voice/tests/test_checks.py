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


# --------------------------------------------------------------------------
# Trimmed duration, delivery rate and the mastering spec. Added 2026-09-03
# for the real-use-case bank: exact-duration ad spots, dubbing fitted to a
# shot, fast compliance copy, and ACX audiobook submission.
# --------------------------------------------------------------------------

def padded(speech_s: float, lead_s: float, trail_s: float, quiet_amp: float = 0.0):
    """Speech with silence (or low-level room tone) either side."""
    def flat(seconds: float) -> np.ndarray:
        n = int(seconds * RATE)
        if not quiet_amp or n == 0:
            return np.zeros(n)
        t = np.linspace(0, seconds, n, endpoint=False)
        return quiet_amp * np.sin(2 * np.pi * 60 * t)

    return np.concatenate([flat(lead_s), speechlike(speech_s), flat(trail_s)])


def test_trimmed_duration_excludes_the_silence_either_side(tmp_path):
    p = write(tmp_path, "pad.wav", padded(3.0, lead_s=1.0, trail_s=2.0))
    facts = decode(p)
    assert 5.9 < facts.duration_s < 6.1
    assert 2.9 < facts.trimmed_duration_s < 3.1


def test_padding_with_silence_cannot_buy_a_duration_pass(tmp_path, predictor):
    """
    The whole reason the slot gate is on the TRIMMED length. A 3s read padded
    to 15s is a 3s read; an ad slot it fits only on paper.
    """
    p = write(tmp_path, "short-read.wav", padded(3.0, lead_s=6.0, trail_s=6.0))
    scenario = FakeScenario(checks={"trimmed_duration_s": {"min": 14.9, "max": 15.1}})
    report = run_checks(scenario, "m1", p, None, None, predictor)
    assert "trimmed_duration_in_range" in report.failed_gates
    # The raw file IS 15 seconds - which is exactly what would have passed.
    assert 14.9 <= report.measurements["duration_s"] <= 15.1


def test_a_read_that_fits_the_slot_passes(tmp_path, predictor):
    p = write(tmp_path, "spot.wav", padded(15.0, lead_s=0.1, trail_s=0.3))
    scenario = FakeScenario(checks={"trimmed_duration_s": {"min": 14.9, "max": 15.1}})
    report = run_checks(scenario, "m1", p, None, None, predictor)
    assert "trimmed_duration_in_range" not in report.failed_gates


def test_speech_rate_is_measured_from_script_words_over_the_trimmed_length(tmp_path, predictor):
    from runner.normalize import normalize

    script = "the annual percentage rate is variable and subject to change without notice"
    p = write(tmp_path, "rate.wav", padded(6.0, lead_s=0.5, trail_s=0.5))
    report = run_checks(FakeScenario(text=script), "m1", p, None, None, predictor)
    words = len(normalize(script).split())
    assert report.measurements["script_words"] == words
    # 6 seconds of speech -> words per minute is ten times the word count.
    assert report.measurements["speech_rate_wpm"] == pytest.approx(words * 10, rel=0.05)


def test_a_disclaimer_read_too_slowly_fails_the_rate_gate(tmp_path, predictor):
    script = " ".join(["word"] * 40)
    slow = write(tmp_path, "slow.wav", speechlike(30.0))   # 80 wpm
    fast = write(tmp_path, "fast.wav", speechlike(11.0))   # ~218 wpm
    checks = {"speech_rate_wpm": {"min": 200, "max": 240}}
    assert "speech_rate_in_range" in run_checks(
        FakeScenario(text=script, checks=checks), "m1", slow, None, None, predictor
    ).failed_gates
    assert "speech_rate_in_range" not in run_checks(
        FakeScenario(text=script, checks=checks), "m1", fast, None, None, predictor
    ).failed_gates


def test_noise_floor_is_measured_in_the_lead_and_trail(tmp_path):
    silent = decode(write(tmp_path, "clean.wav", padded(2.0, 0.5, 0.5)))
    assert silent.noise_floor_dbfs is not None and silent.noise_floor_dbfs < -100

    # Room tone quiet enough to still count as silence, loud enough to fail
    # a -60 dBFS spec.
    noisy = decode(write(tmp_path, "tone.wav", padded(2.0, 0.5, 0.5, quiet_amp=0.004)))
    assert noisy.noise_floor_dbfs is not None
    assert -60 < noisy.noise_floor_dbfs < -40


def test_acx_thresholds_gate_on_published_numbers(tmp_path, predictor):
    acx = {"rms_dbfs": {"min": -23.0, "max": -18.0}, "peak_dbfs_max": -3.0,
           "noise_floor_dbfs_max": -60.0}
    # amp 0.25 lands around -21 dBFS RMS with peak near -8 dBFS.
    ok = write(tmp_path, "acx-ok.wav", padded(3.0, 0.4, 0.4, quiet_amp=0.0))
    report = run_checks(FakeScenario(checks=acx), "m1", ok, None, None, predictor)
    assert report.passed, report.failed_gates

    quiet = write(tmp_path, "acx-quiet.wav", padded(3.0, 0.4, 0.4) * 0.05)
    assert "rms_in_range" in run_checks(
        FakeScenario(checks=acx), "m1", quiet, None, None, predictor
    ).failed_gates

    hot = write(tmp_path, "acx-hot.wav", np.clip(padded(3.0, 0.4, 0.4) * 3.5, -0.999, 0.999))
    assert "peak_below_max" in run_checks(
        FakeScenario(checks=acx), "m1", hot, None, None, predictor
    ).failed_gates


def test_an_unmeasurable_noise_floor_is_not_a_pass_and_says_so(tmp_path, predictor):
    """
    A clip that starts on the first syllable has no room tone. That is
    absent evidence, not a good result - the same third state the ASR path
    uses. The detail line has to say which one it is.
    """
    p = write(tmp_path, "nogap.wav", speechlike(3.0))
    report = run_checks(
        FakeScenario(checks={"noise_floor_dbfs_max": -60.0}), "m1", p, None, None, predictor
    )
    assert report.measurements["noise_floor_unmeasured"] is True
    assert "noise_floor_dbfs" not in report.measurements
    gate = next(g for g in report.gates if g.name == "noise_floor_below_max")
    assert "unmeasured" in gate.detail and "not passing" in gate.detail


def test_must_not_say_catches_the_wrong_convention(tmp_path, predictor):
    """
    The check must_say cannot express. Both transcripts state a true amount;
    only one states it the way an Indian customer hears it.
    """
    p = write(tmp_path, "amt.wav", speechlike(4.0))
    scenario = FakeScenario(
        text="Your refund of Rs 2,50,000 has been approved.",
        checks={
            "must_say": ["two lakh fifty thousand rupees"],
            "must_not_say": ["two hundred fifty thousand", "two hundred and fifty thousand"],
        },
    )
    good = run_checks(scenario, "m1", p, "Your refund of 2,50,000 rupees has been approved.",
                      None, predictor)
    assert good.passed, good.failed_gates

    wrong = run_checks(scenario, "m1", p,
                       "Your refund of two hundred fifty thousand rupees has been approved.",
                       None, predictor)
    assert not wrong.passed
    assert any(g.startswith("must_not_say") for g in wrong.failed_gates)


def test_must_say_any_accepts_a_legitimate_transcription_variant(tmp_path, predictor):
    """
    From the first real run of vr-game-02. Both models read the invented team
    name "Ironhaus" correctly; the ASR wrote "Ironhouse" for one and "Iron
    House" for the other, and a literal must_say failed BOTH for being right.
    """
    p = write(tmp_path, "npc.wav", speechlike(4.0))
    scenario = FakeScenario(
        text="and Ironhaus take it",
        checks={"must_say_any": [["Ironhaus", "Iron House", "Ironhouse"]]},
    )
    for heard in ("and Ironhouse take it", "and Iron House take it", "and Ironhaus take it"):
        r = run_checks(scenario, "m1", p, heard, None, predictor)
        assert r.passed, (heard, r.failed_gates)

    wrong = run_checks(scenario, "m1", p, "and Redline take it", None, predictor)
    assert any(g.startswith("must_say_any") for g in wrong.failed_gates)


def test_phrases_are_matched_as_whole_words_not_substrings():
    """
    `must_say: ["lakh"]` was a substring test, so it passed on "Lakhsmi" - a
    temple name satisfying a currency-convention gate.
    """
    from runner.checks import says
    from runner.normalize import normalize

    assert not says(normalize("the Lakhsmi temple"), "lakh")
    assert says(normalize("two lakh fifty thousand rupees"), "lakh")
    # Multi-word phrases must match contiguously, not scattered.
    assert says(normalize("fourteen Bengaluru branches"), "fourteen Bengaluru")
    assert not says(normalize("fourteen big Bengaluru branches"), "fourteen Bengaluru")


def test_must_not_say_also_matches_whole_words(tmp_path, predictor):
    """The negative gate must not fire on a word that merely contains it."""
    p = write(tmp_path, "w.wav", speechlike(3.0))
    scenario = FakeScenario(text="the Lakhsmi temple opens at nine",
                            checks={"must_not_say": ["lakh"]})
    r = run_checks(scenario, "m1", p, "the Lakhsmi temple opens at nine", None, predictor)
    assert r.passed, r.failed_gates


# --------------------------------------------------------------------------
# Whisper's own fabrications. Added 2026-09-03 after three runs of ONE
# unchanged passage produced WERs of 0.099, 0.211 and 0.479 - the audio was
# identical to within a second, and the difference was a FEMA public-service
# tagline in one transcript and a repeated middle section in another. Read at
# face value that is a model collapsing on long-form narration. It was the
# recogniser talking.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tail",
    ["Thanks for watching!", "Thank you for watching.", "Please subscribe.",
     "For more information, go to www.fema.gov.", "Subtitles by the Amara.org community"],
)
def test_subtitle_boilerplate_is_stripped_from_the_tail(tail):
    from runner.asr import strip_tail_hallucination

    real = "She would see what the sea had to say about it."
    body, removed = strip_tail_hallucination(f"{real} {tail}")
    assert body == real
    assert removed and tail.rstrip() in removed[0]


def test_stacked_boilerplate_is_all_removed():
    from runner.asr import strip_tail_hallucination

    real = "She would walk until the road ran out."
    body, removed = strip_tail_hallucination(f"{real} Thanks for watching. Please subscribe.")
    assert body == real and len(removed) == 2


def test_a_clean_transcript_is_untouched():
    from runner.asr import strip_tail_hallucination

    real = "The lamps along the causeway came on one at a time. Sarala counted them."
    body, removed = strip_tail_hallucination(real)
    assert body == real and removed == []


def test_boilerplate_words_mid_transcript_survive():
    """
    Only the TAIL is stripped. A scenario could legitimately say "thanks for
    watching" in the middle of a line, and removing it would be the harness
    editing evidence.
    """
    from runner.asr import strip_tail_hallucination

    real = "Thanks for watching the shop while I was away. I owe you one."
    body, removed = strip_tail_hallucination(real)
    assert body == real and removed == []


def test_the_repair_is_recorded_not_silent():
    """
    A repaired measurement that does not say it was repaired is a lie - the
    same rule the repeat-collapse guard follows.
    """
    from runner.asr import AsrResult
    from runner.cost import Cost

    cost = Cost(micro_usd=0, basis="local", price_as_of="2026-09-03",
                price_source="local model, no API", usage_source="estimated",
                usage_exact=True)
    r = AsrResult(text="x", provider_model="m", latency_ms=1, cost=cost, attempts=1,
                  tail_stripped=("Thanks for watching!",))
    assert r.tail_stripped == ("Thanks for watching!",)
    # and the default is empty, so a clean transcript claims no repair
    clean = AsrResult(text="x", provider_model="m", latency_ms=1, cost=cost, attempts=1)
    assert clean.tail_stripped == ()


# --------------------------------------------------------------------------
# Character-exact alphanumeric readback. Added 2026-09-03 for the KYC
# scenario: a verification reference with one wrong character is a failed
# call however good the clip sounds, and WER forgives one character in eight.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written",
    ["B 8 Q O Z 2 G 6", "b8qoz2g6", "B8QOZ2G6",
     "Bravo, eight, Quebec, Oscar, Zulu, two, Golf, six",
     "B as in Bravo, 8, Q as in Quebec, O as in Oscar, Z as in Zulu, 2, G as in Golf, 6"],
)
def test_every_way_a_reference_is_spelled_out_converges(written):
    from runner.checks import extract_alnum_sequence

    assert extract_alnum_sequence(written) == "b8qoz2g6"


def test_ordinary_prose_is_not_read_as_an_identifier():
    from runner.checks import extract_alnum_sequence

    # A product name is not a spelled reference; a long digit run still is.
    assert extract_alnum_sequence("order 481902773154") == "481902773154"
    assert "vivobook" not in extract_alnum_sequence("the ASUS Vivobook has 16 GB")


def test_confusions_are_reported_by_class_not_just_position():
    """
    "Wrong at position 3" is a bug report. "Turned B into 8" is the finding
    this scenario was written to produce.
    """
    from runner.checks import alnum_confusions

    r = alnum_confusions("b8qoz2g6", "88qo22g6")
    assert [(e["expected"], e["heard"], e["confusable_class"]) for e in r["errors"]] == [
        ("b", "8", "b8"), ("z", "2", "z2")]
    assert r["all_errors_are_known_confusables"] is True

    unrelated = alnum_confusions("b8qoz2g6", "p8qoz2g6")
    assert unrelated["errors"][0]["confusable_class"] == "unrelated"
    assert unrelated["all_errors_are_known_confusables"] is False


def test_a_wrong_character_fails_the_gate(tmp_path, predictor):
    p = write(tmp_path, "kyc.wav", speechlike(4.0))
    scenario = FakeScenario(text="B 8 Q O Z 2 G 6", checks={"must_say_alnum": "B8QOZ2G6"})
    ok = run_checks(scenario, "m1", p, "B 8 Q O Z 2 G 6", None, predictor)
    assert ok.passed, ok.failed_gates
    bad = run_checks(scenario, "m1", p, "8 8 Q O Z 2 G 6", None, predictor)
    assert "alnum_exact" in bad.failed_gates
    assert bad.measurements["alnum_confusions"]["errors"][0]["confusable_class"] == "b8"


@pytest.mark.parametrize(
    "transcript",
    [
        # Real p1 transcripts, 2026-09-03. Both were CORRECT readings that the
        # extractor scored 3/8 and 2/8.
        "Your verification reference is B8Q-OZ2G6. Please confirm the card ending 4419.",
        "Your verification reference is BRAVO 8 QUABEQ OSCAR ZULU 2 GOLF 6. Card ending 4419.",
        "Your verification reference is B.8.Q.O.Z.2.G.6",
    ],
)
def test_a_correct_readback_survives_the_transcriber_s_punctuation(transcript):
    """
    A single inserted hyphen left "oz" as an unmappable two-letter token and
    cost two characters; "QUABEQ" for Quebec shifted every character after
    it. Both were the recogniser's notation, not the model's reading.
    """
    from runner.checks import extract_alnum_sequence

    assert "b8qoz2g6" in extract_alnum_sequence(transcript)


def test_a_genuine_misreading_still_fails_after_those_fixes():
    """The repairs must not make the gate unfailable."""
    from runner.checks import extract_alnum_sequence

    assert "b8qoz2g6" not in extract_alnum_sequence("reference is B8P-OZ2G6")
    assert "b8qoz2g6" not in extract_alnum_sequence("BRAVO 8 PAPA OSCAR ZULU 2 GOLF 6")
