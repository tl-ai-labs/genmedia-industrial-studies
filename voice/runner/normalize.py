"""
The ONE text normalization function (plan v1.2 section 13).

WHY THIS FILE IS ONE FUNCTION AND NOT TWO.
Raw WER between a script and an ASR transcript mostly measures transcription
convention, not accuracy: the script says "4-8-2-9-1-6", the ASR returns
"four eight two nine one six", and a perfect clip fails the gate on
formatting. The fix only works if the SAME function runs over BOTH sides -
two nearly-identical normalizers drift, and the day they disagree the gate
starts failing clips that are fine. So: one function, imported by checks.py
for both the script and the transcript, and by nothing else.

THE PIPELINE, in the plan's order:
    lowercase -> strip punctuation -> expand digits/numbers -> collapse space

The steps below are refinements WITHIN that spine, not extra stages. Each one
exists because a real clip failed on it:

  Roman numerals   "XLIV" vs "44"           (voi-nar-02)
  abbreviations    "Doctor" vs "Dr."        (measured: 0.12 -> 0.04 WER)
  a.m. / p.m.      "a m" vs "am"            (voi-nar-02)
  currency         "£7,654.32" vs "7654.32 pounds"  (voi-nar-02)
  thousands sep.   "3,050,003.50" split into three numbers  (voi-nar-02)
  spelling variant "catalogued" vs "cataloged"  (measured, same probe)
  Indian grouping "Rs 2,50,000" read as lakh, not "two fifty thousand"

THE INDIAN NUMBERING RULE deserves its own note, because it was a WRONG
ANSWER rather than a missing one. `2,50,000` is grouped 2-2-3, so the
international thousands pattern did not match it whole - it matched the TAIL
`50,000` and produced "two fifty thousand", and `1,00,000` came out as
"one zero". A refund scenario built on that would have failed both models
identically, which is the signature of an instrument fault, not a model one.
The grouping itself is the signal: two digits, then twos, then a three, is
unambiguously Indian - `250,000` cannot match it - so nothing guesses a
locale. Rupee amounts read in the Indian system for the same reason - that
is how the amount is said aloud - and both sides are expanded by this one
function.

THE SYMMETRY ARGUMENT, which is what makes these safe. Every rule here is
applied identically to both sides. So even a rule that expands something
"wrongly" costs nothing, as long as it expands it wrongly on both sides -
the two strings still match. The only rules that can hurt are ones that fire
on one side and not the other, which is why every one of them keys off the
written form rather than off meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import jiwer
from num2words import num2words

# A run of digits at least this long reads as an IDENTIFIER (order number,
# reference, phone) and is spoken digit-by-digit; anything shorter is a
# quantity and is spoken as a cardinal. Both sides of the comparison use the
# same threshold, so the only thing this constant can change is whether a
# borderline number is compared as "one zero two four" or "one thousand
# twenty four" - never whether the two sides agree with each other.
DIGIT_STRING_MIN_LEN = 4

# Currency symbol -> the word a speaker says. Placed AFTER the amount, which
# is how the amount is actually read aloud ("seven thousand ... pounds").
CURRENCY = {"£": "pounds", "€": "euros", "¥": "yen", "$": "dollars", "₹": "rupees"}

# Written abbreviation -> spoken form. Only forms whose spoken expansion is
# unambiguous; "St." is deliberately absent because it is both Saint and
# Street and guessing would be worse than leaving it.
ABBREVIATIONS = {
    "dr": "doctor", "mr": "mister", "mrs": "missus", "ms": "miz",
    "prof": "professor", "st": None, "vs": "versus", "etc": "et cetera",
    "no": None, "fig": "figure", "approx": "approximately",
}

# Unit abbreviations -> the word a speaker says. A reader saying "sixteen
# gigabytes" and an ASR writing "16 GB" are the same utterance; without this
# they share nothing. Singular, because the plural is folded separately -
# "gigabytes" and "gigabyte" must land on one form or "512 GB" and
# "five-hundred-and-twelve-gigabyte" still disagree.
UNIT_ABBREVIATIONS = {
    "gb": "gigabyte", "tb": "terabyte", "mb": "megabyte", "kb": "kilobyte",
    "ghz": "gigahertz", "mhz": "megahertz", "khz": "kilohertz",
    "kg": "kilogram", "km": "kilometre", "cm": "centimetre",
    "mm": "millimetre", "ml": "millilitre", "kw": "kilowatt", "mp": "megapixel",
}
# Units whose plural must fold to the singular so both spellings converge.
UNIT_PLURALS = {f"{v}s": v for v in UNIT_ABBREVIATIONS.values()}

# Ordinal suffixes: "23rd" must NOT split into "23 rd".
_ORDINALS = ("st", "nd", "rd", "th")

# Spelling variants that mean the same word said the same way. A token-level
# map, NEVER a suffix rule: "-our -> -or" would turn "four" into "for".
SPELLING_VARIANTS = {
    "catalogued": "cataloged", "catalogue": "catalog", "colour": "color",
    "colours": "colors", "coloured": "colored", "favour": "favor",
    "favourite": "favorite", "honour": "honor", "labour": "labor",
    "neighbour": "neighbor", "behaviour": "behavior", "flavour": "flavor",
    "harbour": "harbor", "harbours": "harbors", "rumour": "rumor",
    "armour": "armor", "vapour": "vapor", "endeavour": "endeavor",
    "organise": "organize", "organised": "organized", "recognise": "recognize",
    "realise": "realize", "realised": "realized", "analyse": "analyze",
    "apologise": "apologize", "centre": "center", "theatre": "theater",
    "metre": "meter", "litre": "liter", "defence": "defense",
    "licence": "license", "practise": "practice", "traveller": "traveler",
    "cancelled": "canceled", "grey": "gray", "aeroplane": "airplane",
    "programme": "program", "dialogue": "dialog", "judgement": "judgment",
    # An ASR writes "2 lakhs" where the script says "2 lakh"; same amount.
    "lakhs": "lakh", "crores": "crore",
}

_ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
# Uppercase strings that are legal Roman numerals AND ordinary English words.
# Left alone: expanding "MIX" to 1009 on one side only would be a real error.
_ROMAN_WORDS = {"MIX", "DIM", "MID", "DID", "ILL", "LID", "MILD", "CIVIL", "I", "MI", "DI"}

# "S14" -> "S 14", "512GB" -> "512 GB". A joined alphanumeric is one token to
# a machine and two words to a speaker, so the halves must be separated before
# numbers are expanded. Ordinals are excluded by the check in _split_alnum.
_ALNUM_LD = re.compile(r"(?<=[A-Za-z])(?=\d)")
_ALNUM_DL = re.compile(r"(?<=\d)(?=[A-Za-z])")

# Indian digit grouping: last three digits, then twos. "2,50,000" and
# "1,50,00,000" match; "250,000" and "3,050,003.50" cannot, so this never
# has to guess which convention a number was written in.
_INDIAN_GROUPED = re.compile(r"\b\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?\b")
# "Rs 4,99,999" / "Rs. 4,99,999" / "INR 4,99,999" are the rupee symbol
# spelled out. Rewritten to the symbol so ONE currency path handles them all.
_RUPEE_WORD = re.compile(r"\b(?:rs\.?|inr)\s*(?=\d)", re.IGNORECASE)
# "%" is a WORD a speaker says. Stripped as punctuation it vanished, so a
# script saying "thirty percent" and an ASR writing "30%" shared nothing -
# the same shape of fault as "sixteen gigabytes" versus "16 GB". "per cent"
# folds to one spelling for the same reason.
_PERCENT = re.compile(r"\s*%")
_PER_CENT = re.compile(r"\bper\s+cent\b")
# "2:00" -> "two". A speaker saying "between two and four" is transcribed
# "between 2:00 and 4:00", and the bare digit rule then read the zeros aloud
# - "two zero zero and four zero zero" - inserting four tokens into every
# model's WER equally. An on-the-hour time carries no more information than
# its hour. Minutes are left to the H:MM rule below, which already reads
# "3:30" as "three thirty".
_CLOCK_HOUR = re.compile(r"\b([01]?\d|2[0-3]):00\b")
# "14th" -> "fourteenth". The script spells an ordinal out and the ASR writes
# the digit form, so without this a correct date read fails on notation. Note
# _split_alnum already protects "14th" from becoming "14 th"; this turns the
# protected token into the word a speaker actually says.
_DIGIT_ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.IGNORECASE)
_THOUSANDS = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
_DECIMAL = re.compile(r"\b\d+\.\d+\b")
_MERIDIEM = re.compile(r"\b([ap])\.\s?m\.", re.IGNORECASE)
_ABBREV = re.compile(r"\b([A-Za-z]{2,7})\.")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")
_UNDERSCORE = re.compile(r"_+")


def _roman_to_int(tok: str) -> int | None:
    if len(tok) < 2 or tok in _ROMAN_WORDS or not _ROMAN_RE.match(tok):
        return None
    total, prev = 0, 0
    for ch in reversed(tok):
        v = _ROMAN_VALUES[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total or None


# Matched on the RUN of Roman letters itself rather than on a whitespace
# token, so any surrounding punctuation is irrelevant. The first version
# stripped a fixed list of ASCII punctuation and missed `XLIV.”` - a curly
# quote was enough to defeat it, which is exactly the kind of near-miss a
# hand-written strip list produces.
_ROMAN_TOKEN = re.compile(r"\b[IVXLCDM]{2,}\b")


def _expand_roman(text: str) -> str:
    """Runs BEFORE lowercasing, because case is the only signal we have."""

    def sub(m: re.Match) -> str:
        n = _roman_to_int(m.group(0))
        return num2words(n, lang="en") if n else m.group(0)

    return _ROMAN_TOKEN.sub(sub, text)


def _split_alnum(text: str) -> str:
    """
    Separate letters from digits inside a token: "S14" -> "S 14".

    Skipped for ordinals ("23rd" must not become "23 rd") and for tokens that
    are a unit abbreviation glued to its number, which the unit pass handles
    with the number intact.
    """
    out = []
    for tok in text.split():
        core = tok.strip(".,;:!?\"'()[]")
        low = core.lower()
        if any(low.endswith(o) and low[:-2].isdigit() for o in _ORDINALS):
            out.append(tok)
            continue
        split = _ALNUM_DL.sub(" ", _ALNUM_LD.sub(" ", tok))
        out.append(split)
    return " ".join(out)


def _words_int(n: int, indian: bool = False) -> str:
    """
    `indian` selects the lakh/crore system. num2words ships it as the
    "en_IN" locale (note the underscore - "en-IN" silently falls back to
    international, which is the kind of typo that produces a plausible wrong
    answer rather than an error).
    """
    return num2words(n, lang="en_IN" if indian else "en")


def _decimal_words(whole: str, frac: str) -> str:
    """
    "0.00305" -> "zero point zero zero three zero five".

    Fractional digits are spoken ONE AT A TIME, which is both how people read
    them and how this avoids float precision entirely - num2words(0.00305)
    would round-trip through a float and can lose a digit.
    """
    tail = " ".join(_words_int(int(d)) for d in frac)
    return f" {_words_int(int(whole or 0))} point {tail} "


def _money_words(whole: str, frac: str, unit: str, force_indian: bool = False) -> str:
    """
    "$137.50" -> "one hundred and thirty seven dollars and fifty cents".

    A currency amount with two decimal places is spoken as major and minor
    units, never as "point five". Getting this wrong is what made a correct
    clip fail its gate: the script said "137 dollars and 50 cents" and the
    transcript said "$137.50", and un-reconciled they share almost nothing.
    """
    minor = {"dollars": "cents", "pounds": "pence", "euros": "cents",
             "rupees": "paise", "yen": "", "cents": ""}.get(unit, "")
    # A rupee amount is read in the Indian system - "two lakh fifty thousand
    # rupees", not "two hundred fifty thousand rupees". Both sides go through
    # here, so this decides how the amount is COMPARED, not who is right.
    indian = force_indian or unit in ("rupees", "paise")
    head = f" {_words_int(int(whole or 0), indian)} {unit} "
    if not frac or not minor:
        return head
    value = int(frac.ljust(2, "0")[:2])
    return head if value == 0 else f"{head}and {_words_int(value)} {minor} "


def _cardinal(value: str, indian: bool = False) -> str:
    """A number written with separators is a QUANTITY, not an identifier."""
    plain = value.replace(",", "")
    if "." in plain:
        whole, _, frac = plain.partition(".")
        return _decimal_words(whole, frac)
    try:
        return f" {_words_int(int(plain), indian)} "
    except ValueError:
        return value


def _expand_numeric_token(tok: str) -> str:
    """One whitespace-delimited bare-digit token -> its spoken form."""
    if not tok.isdigit():
        return tok
    # A leading zero is never a quantity - "007" is an identifier however
    # short it is, and num2words would silently drop the zeros.
    digit_by_digit = len(tok) >= DIGIT_STRING_MIN_LEN or tok.startswith("0")
    if digit_by_digit:
        return " ".join(num2words(int(d), lang="en") for d in tok)
    return num2words(int(tok), lang="en")


def normalize(text: str) -> str:
    """
    Normalize one side of the comparison. Pure: same input, same output,
    no I/O, no config. Applied identically to the script and the transcript.
    """
    if not text:
        return ""

    # 1. Roman numerals, while case still exists.
    text = _expand_roman(text)

    text = text.lower()

    # 2. "3:47 a.m." -> "3:47 am"; the bare dots would otherwise become "a m".
    text = _MERIDIEM.sub(lambda m: f"{m.group(1)}m", text)

    # 2b. "Rs"/"Rs."/"INR" before a number IS the rupee symbol. Rewriting it
    #     here means the currency pass below handles rupees exactly like every
    #     other currency, including the major/minor split.
    text = _RUPEE_WORD.sub("\u20b9", text)

    # 3. Currency. ONE pass over symbol-plus-amount, so the amount is spoken
    #    the way a reader says it - major and minor units, not "point five".
    def _money(m: re.Match) -> str:
        unit = CURRENCY[m.group(1)]
        amount = m.group(2)
        whole, _, frac = amount.replace(",", "").partition(".")
        # THE GROUPING BEATS THE SYMBOL. "2,50,000" is grouped 2-2-3, which
        # is Indian whatever precedes it. A transcriber that heard rupees and
        # wrote "$2,50,000" - observed 2026-09-03 - otherwise flipped the
        # amount to "two hundred and fifty thousand dollars" and fired the
        # very must_not_say gate that exists to catch that reading. The
        # written form is the evidence; the symbol is the guess.
        return _money_words(whole, frac, unit,
                            force_indian=bool(_INDIAN_GROUPED.fullmatch(amount)))

    text = re.sub(rf"([{''.join(CURRENCY)}])\s?(\d[\d,]*(?:\.\d+)?)", _money, text)

    # A bare number FOLLOWED by a currency word is unambiguously a quantity,
    # so it must read as a cardinal rather than falling through to the
    # digit-by-digit identifier rule. Without this, "£7,654.32" and
    # "7654 pounds 32 pence" - the same amount, written two ways - normalize
    # to "seven thousand six hundred..." and "seven six five four...".
    _UNITS = "pounds|pence|euros|yen|dollars|cents|rupees|paise"
    text = re.sub(
        rf"\b(\d[\d,]*(?:\.\d+)?)\s+({_UNITS})\b",
        lambda m: f"{_cardinal(m.group(1), m.group(2) in ('rupees', 'paise'))}{m.group(2)} ",
        text,
    )

    # 3c. Percent, before the strip turns "%" into nothing.
    text = _PERCENT.sub(" percent", text)
    text = _PER_CENT.sub("percent", text)

    # 3b. Joined alphanumerics, then unit abbreviations. Both run BEFORE the
    #     number pass so "512GB" becomes "512 gigabyte" and not "512gb".
    text = _split_alnum(text)
    text = " ".join(
        UNIT_ABBREVIATIONS.get(t.strip(".,;:"), t) if t.strip(".,;:") in UNIT_ABBREVIATIONS else t
        for t in text.split()
    )

    # 4. Abbreviations, before the dots are stripped.
    def _ab(m: re.Match) -> str:
        rep = ABBREVIATIONS.get(m.group(1).lower(), "")
        return f" {rep} " if rep else m.group(0)

    text = _ABBREV.sub(_ab, text)

    # 4a. On-the-hour clock times, before the bare-digit pass reads the zeros.
    text = _CLOCK_HOUR.sub(lambda m: f" {m.group(1)} ", text)

    # 4b. Digit ordinals, before the bare-digit pass would see "14" alone.
    text = _DIGIT_ORDINAL.sub(
        lambda m: f" {num2words(int(m.group(1)), lang='en', to='ordinal')} ", text
    )

    # 5. Numbers carrying separators or a decimal point are quantities and
    #    must survive punctuation stripping as ONE number, not three.
    #    Indian grouping FIRST: `_THOUSANDS` would otherwise match the tail of
    #    "2,50,000" and read it as "fifty thousand".
    text = _INDIAN_GROUPED.sub(lambda m: _cardinal(m.group(0), True), text)
    text = _THOUSANDS.sub(lambda m: _cardinal(m.group(0)), text)
    text = _DECIMAL.sub(lambda m: _cardinal(m.group(0)), text)

    # 6. The plan's strip: keep word characters, everything else to a space.
    #    A space, not nothing: "4-8" must become "4 8", never "48".
    text = _UNDERSCORE.sub(" ", text)
    text = _PUNCT.sub(" ", text)

    # 6b. Adjacent bare-digit tokens are ONE identifier. A transcriber writes
    #     a twelve-digit reference as "481 902 773 154" or "903-762" (the
    #     hyphen is a space by now), and expanding each group as its own
    #     cardinal destroyed it: "481 902 773 154" yielded the digits 192731,
    #     so a perfect readback failed its own gate. Joined only when the
    #     result is a real identifier - four digits or more - so "two and
    #     four" and other short adjacent numbers are untouched.
    _joined: list[str] = []
    for tok in text.split():
        if tok.isdigit() and _joined and _joined[-1].isdigit() and len(_joined[-1] + tok) >= DIGIT_STRING_MIN_LEN:
            _joined[-1] += tok
        else:
            _joined.append(tok)
    text = " ".join(_joined)

    # 7. Remaining bare digit runs.
    text = " ".join(_expand_numeric_token(t) for t in text.split())

    # num2words emits hyphens and commas ("eighty-two thousand, nine") - the
    # second strip is not belt-and-braces, it is required for correctness.
    text = _PUNCT.sub(" ", text)

    # 8. Spelling variants and unit plurals, token-level.
    text = " ".join(
        SPELLING_VARIANTS.get(t, UNIT_PLURALS.get(t, t)) for t in text.split()
    )

    return _WS.sub(" ", text).strip()


@dataclass(frozen=True)
class WerResult:
    """
    Everything a disputed gate needs to be replayed by eye. All four fields
    go into checks.jsonl; the report renders the two normalized strings side
    by side with the diff highlighted.
    """

    wer: float
    normalized_reference: str
    normalized_hypothesis: str
    reference_words: int
    hypothesis_words: int

    @property
    def as_record(self) -> dict:
        return {
            "wer": round(self.wer, 6),
            "normalized_script": self.normalized_reference,
            "normalized_transcript": self.normalized_hypothesis,
            "script_words": self.reference_words,
            "transcript_words": self.hypothesis_words,
        }


def normalized_wer(script: str, transcript: str) -> WerResult:
    """
    THE number every voice gate and the text_accuracy criterion use.

    An empty normalized script is a scenario-authoring bug rather than a
    perfect score, so it raises instead of returning 0.0. An empty transcript
    against a non-empty script is WER 1.0 - the model said nothing.
    """
    ref = normalize(script)
    hyp = normalize(transcript)
    if not ref:
        raise ValueError(
            "normalized_wer: the script normalizes to an empty string - the "
            "scenario has no speakable text. Fix the scenario; do not score it."
        )
    if not hyp:
        return WerResult(1.0, ref, "", len(ref.split()), 0)
    return WerResult(
        float(jiwer.wer(ref, hyp)),
        ref,
        hyp,
        len(ref.split()),
        len(hyp.split()),
    )
