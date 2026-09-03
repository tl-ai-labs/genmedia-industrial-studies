"""
The WER normalization pipeline and the gate it feeds.

The two cases the plan names as Phase 2's done-criteria live here, offline:
normalized WER must CATCH a deliberately wrong script and must PASS a
digits-heavy correct one. Both run with no API key and no network.
"""

from __future__ import annotations

import pytest

from runner.normalize import normalize, normalized_wer

# --------------------------------------------------------------------------
# The pipeline itself
# --------------------------------------------------------------------------


def test_lowercases_and_strips_punctuation():
    assert normalize("Hello, Priya! It's here.") == "hello priya it s here"


def test_hyphenated_digit_string_and_bare_digit_string_converge():
    """
    The plan's motivating example: the script writes 4-8-2-9-1-6, the ASR
    returns the words, and a third source writes 482916. All three must
    normalize to the same thing or a perfect clip fails on formatting.
    """
    target = "four eight two nine one six"
    assert normalize("4-8-2-9-1-6") == target
    assert normalize("482916") == target
    assert normalize("Four Eight Two Nine One Six") == target


def test_short_numbers_expand_as_cardinals_not_digit_by_digit():
    assert normalize("press 3") == "press three"
    assert normalize("137") == "one hundred and thirty seven"


def test_leading_zero_run_is_an_identifier_however_short():
    # num2words(7) would silently drop the zeros; "007" is never a quantity.
    assert normalize("007") == "zero zero seven"


def test_collapses_whitespace():
    assert normalize("a   b\n\nc\t d") == "a b c d"


def test_is_pure_and_idempotent_on_its_own_output():
    once = normalize("Order 4-8-2-9-1-6, total $137.50!")
    assert normalize(once) == once


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

DIGITS_SCRIPT = (
    "Hello, this is Trailhead Outdoor confirming order 4-8-2-9-1-6. "
    "Your total is 137 dollars and 50 cents, charged to the card ending 7104."
)


def test_digits_heavy_correct_transcript_passes_a_tight_gate():
    """A correct clip, transcribed the way an ASR actually writes it."""
    transcript = (
        "Hello, this is Trailhead Outdoor confirming order 482916. "
        "Your total is $137.50, charged to the card ending 7104."
    )
    result = normalized_wer(DIGITS_SCRIPT, transcript)
    assert result.wer <= 0.10, f"WER {result.wer:.3f}\n{result.normalized_reference}\n{result.normalized_hypothesis}"


def test_raw_comparison_would_have_failed_the_same_clip():
    """
    Proof the normalization is load-bearing rather than decorative: without
    it, the identical correct clip fails a 10% gate on formatting alone.
    """
    import jiwer

    transcript = (
        "Hello, this is Trailhead Outdoor confirming order 482916. "
        "Your total is $137.50, charged to the card ending 7104."
    )
    raw = jiwer.wer(DIGITS_SCRIPT, transcript)
    normalized = normalized_wer(DIGITS_SCRIPT, transcript).wer
    assert raw > 0.10
    assert normalized <= 0.10
    assert normalized < raw


def test_deliberately_wrong_script_is_caught():
    """
    The negative control. Same shape of sentence, wrong order number, wrong
    courier, wrong day - a clip that would sound perfect. The gate must
    still say no.
    """
    spoken = (
        "Your parcel left our warehouse this morning and is with the courier. "
        "Tracking number 5-5-1-2-0-8. Expected delivery is Friday before six."
    )
    wrong_reference = (
        "Your parcel left our depot last night and is with the postal service. "
        "Tracking number 9-9-4-3-7-1. Expected delivery is Tuesday before noon."
    )
    result = normalized_wer(wrong_reference, spoken)
    assert result.wer > 0.10, "the gate failed to catch a deliberately wrong script"


def test_wrong_digits_alone_are_caught():
    """The failure that matters most: sounds beautiful, says the wrong number."""
    script = "Your reference is 4-8-2-9-1-6."
    spoken_wrong = "Your reference is 4-8-2-9-1-7."
    assert normalized_wer(script, spoken_wrong).wer > 0.0


def test_empty_transcript_is_total_error_not_a_pass():
    assert normalized_wer("hello there", "").wer == 1.0


def test_empty_script_raises_rather_than_scoring_perfect():
    with pytest.raises(ValueError, match="no speakable text"):
        normalized_wer("...", "anything")


def test_result_carries_both_normalized_sides_for_replay():
    r = normalized_wer("Order 4-8-2-9-1-6.", "order 482916")
    rec = r.as_record
    assert rec["normalized_script"] == "order four eight two nine one six"
    assert rec["normalized_transcript"] == "order four eight two nine one six"
    assert rec["wer"] == 0.0


# --------------------------------------------------------------------------
# The rules added 2026-09-01, each because a real text broke on it. Every
# case is a pair that a speaker would read IDENTICALLY but a transcriber
# might write two ways - which is exactly what the pipeline exists to absorb.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written,spoken",
    [
        ("$137.50", "137 dollars and 50 cents"),
        ("¥123,456", "123456 yen"),
        ("hypothesis number XLIV", "hypothesis number 44"),
        ("Dr. Quenell", "Doctor Quenell"),
        ("Prof. Vrzal", "Professor Vrzal"),
        ("3:47 a.m.", "3:47 AM"),
        ("11:11:11 p.m.", "11:11:11 PM"),
        ("catalogued", "cataloged"),
        ("the colour grey", "the color gray"),
        ("order 4-8-2-9-1-6", "order 482916"),
    ],
)
def test_two_written_forms_of_the_same_spoken_words_converge(written, spoken):
    assert normalize(written) == normalize(spoken)


def test_thousands_separators_survive_as_one_number():
    """
    "3,050,003.50" used to strip to "3 050 003 50" and expand as four
    separate numbers. It is one quantity and must read as one.
    """
    out = normalize("3,050,003.50")
    assert out.startswith("three million fifty thousand and three point")
    assert "zero five zero" not in out


def test_decimals_are_spoken_digit_by_digit_after_the_point():
    # num2words on a float would round-trip and can drop a digit; this is
    # also simply how a decimal is read aloud.
    assert normalize("0.00305") == "zero point zero zero three zero five"


def test_currency_uses_major_and_minor_units_not_point():
    out = normalize("£7,654.32")
    assert "pounds" in out and "pence" in out
    assert "point" not in out


def test_roman_numerals_that_are_also_english_words_are_left_alone():
    """
    Expanding "MIX" to 1009 would be a real error, and unlike the safe rules
    it could fire on one side only. The denylist is the guard.
    """
    assert "one thousand" not in normalize("MIX")
    assert normalize("MIX") == "mix"
    assert normalize("XLIV") == "forty four"


def test_lowercase_roman_lookalikes_are_never_expanded():
    # "did", "mix", "civil" in ordinary prose must survive untouched.
    assert normalize("he did mix the civil dim lid") == "he did mix the civil dim lid"


def test_ambiguous_abbreviations_are_deliberately_not_guessed():
    # "St." is Saint AND Street. Guessing would be worse than leaving it.
    assert "saint" not in normalize("St. Paul")
    assert "street" not in normalize("St. Paul")


def test_the_hard_passage_normalizes_without_crashing():
    """
    voi-nar-02 is engineered to break transcription conventions - IPA
    symbols, diacritics, a vowel-less Czech phrase, currency, Roman
    numerals. It must at minimum survive the pipeline intact.
    """
    from pathlib import Path

    import yaml

    p = Path(__file__).resolve().parent.parent / "demo" / "scenarios" / "voi-nar-02-five-minute-stability.yaml"
    if not p.exists():
        pytest.skip("voi-nar-02 not present")
    script = yaml.safe_load(p.read_text(encoding="utf-8"))["input"]["script"]
    out = normalize(script)
    assert len(out.split()) > 600
    # The traps expand rather than surviving as raw symbols.
    assert "forty four" in out          # XLIV
    assert "pounds" in out              # £7,654.32
    assert "doctor" in out              # Dr.
    assert "$" not in out and "£" not in out and "XLIV" not in out
    # And the pipeline is still idempotent on its own output.
    assert normalize(out) == out


# --------------------------------------------------------------------------
# Unit abbreviations and joined alphanumerics. Added 2026-09-02 after
# voi-ret-01: the speaker said "sixteen gigabytes", the ASR wrote "16 GB",
# and four must_say checks failed on BOTH models identically - the signature
# of an instrument fault rather than a model one.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "spoken,written",
    [
        ("sixteen gigabytes of RAM", "16 GB of RAM"),
        ("five-hundred-and-twelve-gigabyte SSD", "512 GB SSD"),
        ("one-terabyte SSD", "1 TB SSD"),
        ("a two-terabyte drive", "a 2 TB drive"),
        ("three point six gigahertz", "3.6 GHz"),
        ("ASUS Vivobook S 14", "ASUS VivoBook S14"),
        ("Lenovo IdeaPad Slim 5", "Lenovo IdeaPad Slim5"),
    ],
)
def test_unit_abbreviations_and_joined_alphanumerics_converge(spoken, written):
    a, b = normalize(spoken), normalize(written)
    assert a == b or a in b or b in a, f"{a!r} vs {b!r}"


def test_unit_plural_and_singular_land_on_one_form():
    assert normalize("16 gigabytes") == normalize("16 gigabyte")


def test_ordinals_are_not_split_into_a_number_and_a_suffix():
    """"23rd" must not become "23 rd" - that would invent a word."""
    assert "rd" not in normalize("the 23rd of March").split()
    assert "th" not in normalize("the 14th hypothesis").split()


def test_splitting_does_not_disturb_plain_numbers_or_digit_strings():
    assert normalize("482916") == "four eight two nine one six"
    assert normalize("007") == "zero zero seven"
    assert normalize("press 3") == "press three"


# --------------------------------------------------------------------------
# Indian numbering. Added 2026-09-03 while building the real-use-case bank.
# This was a WRONG ANSWER, not a gap: "2,50,000" is grouped 2-2-3, the
# international pattern matched only its TAIL, and the numbers came out
# mangled. A refund scenario built on it would have failed both models
# identically - an instrument fault wearing a model fault's clothes.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written,spoken",
    [
        ("Rs 2,50,000", "two lakh fifty thousand rupees"),
        ("Rs. 2,50,000", "two lakh fifty thousand rupees"),
        ("INR 2,50,000", "two lakh fifty thousand rupees"),
        ("₹2,50,000", "two lakh fifty thousand rupees"),
        ("2,50,000 rupees", "two lakh fifty thousand rupees"),
        ("Rs 4,99,999", "four lakh ninety nine thousand nine hundred and ninety nine rupees"),
        ("Rs 1,00,000", "one lakh rupees"),
        ("1,50,00,000", "one crore fifty lakh"),
    ],
)
def test_indian_grouped_amounts_read_as_lakh_and_crore(written, spoken):
    assert normalize(written) == normalize(spoken)


def test_the_international_reading_of_an_indian_numeral_does_not_converge():
    """
    The property the must_not_say gate depends on. A model that reads
    2,50,000 as "two hundred fifty thousand" has said something TRUE in the
    wrong convention - if normalization folded the two together, no check
    could ever see the difference and the scenario would measure nothing.
    """
    indian = normalize("Rs 2,50,000")
    for wrong in ("two hundred fifty thousand rupees",
                  "two hundred and fifty thousand rupees",
                  "250 thousand rupees"):
        assert normalize(wrong) != indian


def test_one_lakh_written_in_digits_no_longer_collapses():
    """Regression: "1,00,000" normalized to "one zero" before this rule."""
    out = normalize("1,00,000")
    assert out == "one lakh"


def test_international_grouping_is_untouched_by_the_indian_rule():
    # 250,000 cannot match the Indian pattern, so nothing has to guess.
    assert normalize("250,000") == "two hundred and fifty thousand"
    assert normalize("3,050,003.50").startswith("three million")
    assert "lakh" not in normalize("$250,000")


def test_lakh_and_crore_plurals_fold_to_one_form():
    assert normalize("5 lakhs") == normalize("5 lakh")
    assert normalize("2 crores") == normalize("2 crore")


def test_a_rupee_amount_keeps_its_paise():
    out = normalize("₹2,50,000.75")
    assert "paise" in out and "point" not in out


# --------------------------------------------------------------------------
# Percent. Added 2026-09-03: "%" was stripped as punctuation, so a script
# saying "thirty percent" and an ASR writing "30%" shared nothing and two
# correct ad reads would have failed their must_say gates.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written,spoken",
    [
        ("30%", "thirty percent"),
        ("24.9%", "twenty-four point nine percent"),
        ("up to 30% off", "up to thirty percent off"),
        ("24.9 per cent", "24.9 percent"),
        ("100%", "one hundred percent"),
    ],
)
def test_percent_is_a_spoken_word_not_punctuation(written, spoken):
    assert normalize(written) == normalize(spoken)


def test_percent_does_not_glue_itself_to_the_number():
    assert normalize("30%") == "thirty percent"
    assert "30" not in normalize("30%")


# --------------------------------------------------------------------------
# Gaps found by the first full bank run, 2026-09-03. Both failed CORRECT
# reads on notation alone, on both models identically.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written,spoken",
    [
        ("Thursday the 14th", "Thursday the fourteenth"),
        ("the 23rd of March", "the twenty-third of March"),
        ("the 1st and 2nd", "the first and second"),
        ("21st century", "twenty-first century"),
    ],
)
def test_digit_ordinals_read_as_the_word_a_speaker_says(written, spoken):
    assert normalize(written) == normalize(spoken)


def test_an_ordinal_is_not_split_into_a_number_and_a_suffix():
    """Regression on the older rule: "14th" must not become "14 th"."""
    out = normalize("the 14th")
    assert "th" not in out.split() and "14" not in out.split()


def test_harbour_folds_like_its_siblings():
    # colour/favour/honour were folded; harbour was simply missing, and cost
    # a correct narration one WER error.
    assert normalize("the harbour") == normalize("the harbor")
    assert normalize("armour and vapour") == normalize("armor and vapor")


def test_plain_digit_strings_are_untouched_by_the_ordinal_rule():
    assert normalize("482916") == "four eight two nine one six"
    assert normalize("press 3") == "press three"


def test_on_the_hour_clock_times_read_as_the_hour():
    """
    From vr-ecom-01's first run: the script said "between two and four", the
    ASR wrote "between 2:00 and 4:00", and the bare-digit rule read the zeros
    aloud - four tokens inserted into every model's WER equally.
    """
    assert normalize("between two and four") == normalize("between 2:00 and 4:00")
    assert normalize("at 9:00 sharp") == normalize("at nine sharp")
    # Minutes still read as minutes.
    assert normalize("3:30") == normalize("three thirty")
    # A plain number is not a time and must be untouched.
    assert normalize("200") == "two hundred"
