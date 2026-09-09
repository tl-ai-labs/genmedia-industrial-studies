"""
Deterministic gates and measurements for the voice lane
(plan v1.2 sections 11 and 13).

TWO LAYERS, BOTH FREE, BOTH BEFORE THE JUDGE.

  Layer 1, gates: is this output valid at all? Decodes, duration in range,
  not silent, no sustained clipping, loudness sane, normalized WER at or
  below the scenario's max_wer. A gate failure stops the cell: score 0,
  status invalid_output, the judge is never called and no judge cost is
  incurred.

  Layer 2, measurements: facts with a right answer. Normalized WER, objective
  audio quality, duration, loudness. These become criterion scores directly
  (text_accuracy from WER) and are injected into the judge prompt as
  established facts so the judge does not re-estimate them.

Everything here is offline and deterministic except the ASR call, which is
made by the caller and handed in - so this module is fully testable with no
key and no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .mos import QualityPredictor, QualityResult
from .normalize import WerResult, normalize, normalized_wer

# Spoken digit -> character, for pulling a digit sequence back out of a
# transcript. "oh" is included because a reader saying a zero out loud very
# often says "oh", and an ASR writes exactly that.
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def says(haystack_norm: str, phrase: str) -> bool:
    """
    Is `phrase` present as WHOLE WORDS in an already-normalized transcript?

    Substring matching found "lakh" inside "lakhsmi" and would have passed a
    convention gate on a temple name. Phrases are matched as a contiguous run
    of tokens, which is what "the model said this" actually means.
    """
    need = normalize(str(phrase)).split()
    if not need:
        return False
    hay = haystack_norm.split()
    return any(hay[i:i + len(need)] == need for i in range(len(hay) - len(need) + 1))


def _digit_runs(text: str) -> list[str]:
    """
    Consecutive digit-ish tokens, grouped into runs.

    A LONE digit word is prose, not a digit. "read this one time code" and
    "state only the last four digits" contributed a 1 and a 4 to the extracted
    sequence in the 2026-09-01 run, turning "739152" into "1739152" - the
    substring check happened to survive it, which is exactly how a bug like
    this stays hidden. A digit word only counts when it sits beside another
    digit token; raw digits always count, since nobody writes "4471" as prose.
    """
    toks = normalize(text).split()
    kinds = [
        "raw" if t.isdigit() else ("word" if t in _DIGIT_WORDS else None)
        for t in toks
    ]
    runs: list[str] = []
    i = 0
    while i < len(toks):
        if kinds[i] is None:
            i += 1
            continue
        j = i
        while j < len(toks) and kinds[j] is not None:
            j += 1
        group = toks[i:j]
        # Keep the run if it is more than one token, or if its single token is
        # raw digits (a written number), but not a bare spoken word.
        if len(group) > 1 or kinds[i] == "raw":
            runs.append(
                "".join(t if t.isdigit() else _DIGIT_WORDS[t] for t in group)
            )
        i = j
    return runs


# NATO word -> the letter it clarifies. A verification agent says "Bravo"
# precisely so the listener does not hear "eight", which is the confusion this
# scenario exists to measure.
_NATO = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d",
    "echo": "e", "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i",
    "juliet": "j", "juliett": "j", "kilo": "k", "lima": "l", "mike": "m",
    "november": "n", "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r",
    "sierra": "s", "tango": "t", "uniform": "u", "victor": "v", "whiskey": "w",
    "whisky": "w", "xray": "x", "x-ray": "x", "yankee": "y", "zulu": "z",
    # Spellings a recogniser produces for the same spoken word. "QUABEQ" for
    # Quebec cost an eight-character reference six characters on 2026-09-03,
    # because one unmappable token shifts every character after it.
    "quabeq": "q", "quebeq": "q", "kebec": "q", "juliette": "j",
    "alfa": "a", "papa": "p", "poppa": "p", "oskar": "o", "victa": "v",
}

# Which characters are confusable with which, BY EAR. The pairs a KYC agent
# is trained to clarify, and the ones a confusion matrix has to report
# separately - "one character wrong" is not a useful finding if you cannot
# say whether the model turned B into 8 or into P.
CONFUSABLE_CLASSES = {
    "b8": set("b8"), "qo": set("qo0"), "z2": set("z2"), "g6": set("g6"),
    "mn": set("mn"), "sf": set("sf"), "d3": set("d3"), "5s": set("5s"),
}


def extract_alnum_sequence(text: str) -> str:
    """
    The alphanumeric identifier a transcript spelled out, as characters.

    The letter half of extract_digit_sequence, and it exists for the same
    reason: a verification reference with one wrong character is a failed
    call however good the clip sounds, and WER forgives one character in
    eight. Accepts every way a transcriber writes a spelled-out reference -
    "B 8 Q O", "b8qo", "Bravo eight Quebec Oscar" - because all three are the
    same utterance.

    Tokens that map to nothing (filler, "as in", punctuation) simply do not
    contribute; the run continues across them, because "B as in Bravo" is one
    character spoken with a clarifier, not two characters with a word between.
    """
    # A transcriber may write a spelled reference COMPACTLY - "B8QOZ2G6".
    # normalize() splits letters from digits, leaving "qoz" as one token that
    # maps to nothing and is silently dropped, so the eight-character
    # reference came out as five. Spread such a token before normalising:
    # alphanumeric, four or more characters, containing BOTH a letter and a
    # digit is an identifier, not a word.
    spread: list[str] = []
    for raw in re.split(r"\s+", text.strip()):
        core = raw.strip(".,;:!?\"'()[]")
        # A transcriber punctuates a spelled reference however it likes:
        # "B8QOZ2G6", "B8Q-OZ2G6", "B.8.Q.O". Separators INSIDE a candidate
        # identifier are notation, not content - strip them before deciding
        # whether this is an identifier. Observed 2026-09-03: a single
        # inserted hyphen left "oz" as an unmappable two-letter token and
        # silently cost two characters of an eight-character reference.
        compact = re.sub(r"[-.\u2010-\u2015_/]", "", core)
        if (len(compact) >= 4 and compact.isalnum()
                and any(c.isalpha() for c in compact) and any(c.isdigit() for c in compact)):
            spread.append(" ".join(compact))
        else:
            spread.append(raw)
    text = " ".join(spread)

    out: list[str] = []
    prev_nato_for: str | None = None
    for tok in normalize(text).split():
        ch = None
        if len(tok) == 1 and tok.isalnum():
            ch = tok
        elif tok in _DIGIT_WORDS:
            ch = _DIGIT_WORDS[tok]
        elif tok in _NATO:
            ch = _NATO[tok]
            # "B as in Bravo" is ONE character. Collapse the clarifier onto
            # the letter it clarifies rather than counting it twice.
            if out and out[-1] == ch and prev_nato_for != ch:
                prev_nato_for = ch
                continue
        elif tok.isalnum() and len(tok) > 1 and tok.isdigit():
            ch = tok  # a joined digit run, already handled by normalize
        if ch is not None:
            out.append(ch)
            prev_nato_for = None
    return "".join(out)


def alnum_confusions(expected: str, heard: str) -> dict[str, Any]:
    """
    Where the characters diverged, and whether the confusion was a KNOWN
    confusable pair. "Wrong at position 3" is a bug report; "turned B into 8"
    is the finding the scenario was written to produce.
    """
    exp, got = expected.lower(), heard.lower()
    pairs: list[dict[str, str]] = []
    for i, e in enumerate(exp):
        g = got[i] if i < len(got) else ""
        if g == e:
            continue
        cls = next((k for k, v in CONFUSABLE_CLASSES.items() if e in v and g in v), None)
        pairs.append({"position": i, "expected": e, "heard": g or "(missing)",
                      "confusable_class": cls or "unrelated"})
    return {
        "characters_expected": len(exp),
        "characters_correct": sum(1 for i, e in enumerate(exp) if i < len(got) and got[i] == e),
        "errors": pairs,
        "all_errors_are_known_confusables": bool(pairs) and all(
            p["confusable_class"] != "unrelated" for p in pairs),
    }


def extract_digit_sequence(text: str) -> str:
    """
    Every digit in `text` that is part of a digit RUN, in order.

    A transactional readback is checked digit-exactly rather than by WER,
    because WER forgives one wrong digit in a long string and a wrong digit
    in an order number is the whole failure. The transcript may carry
    "4471 8802 3915" or "four four seven one ...", so both spellings reduce
    to the same characters before comparing.
    """
    return "".join(_digit_runs(text))


def longest_digit_run(text: str) -> int:
    """Longest unbroken digit sequence, for the 'do not read the whole card' check."""
    return max((len(r) for r in _digit_runs(text)), default=0)

# Loudness sanity, in dBFS. Wider than a mastering spec on purpose: the gate
# is looking for a broken file, not judging the mix.
RMS_DBFS_MIN = -45.0
RMS_DBFS_MAX = -6.0
# A sample counts as clipped at or above this magnitude, and clipping is
# "sustained" once this many consecutive samples are clipped (~1 ms at 24 kHz).
CLIP_LEVEL = 0.999
CLIP_RUN_SAMPLES = 24
# Silence threshold relative to the clip's own peak.
SILENCE_REL = 0.02
SILENCE_FRAME_S = 0.02
# Room tone needs somewhere to be measured. Below this much combined lead and
# trail silence the noise floor is UNMEASURED rather than assumed good - a
# clip that starts on the first syllable has no room tone to assess, and
# scoring that as a pass would invent evidence.
MIN_NOISE_WINDOW_S = 0.20


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    detail: str

    @property
    def as_record(self) -> dict[str, Any]:
        return {"gate": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class CheckReport:
    scenario_id: str
    model_id: str
    gates: list[Gate] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)
    wer: WerResult | None = None
    quality: QualityResult | None = None
    transcript: str | None = None
    asr_error: str | None = None
    decode_error: str | None = None

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    @property
    def failed_gates(self) -> list[str]:
        return [g.name for g in self.gates if not g.passed]

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.gates.append(Gate(name, passed, detail))

    @property
    def as_record(self) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            "model_id": self.model_id,
            "passed": self.passed,
            "failed_gates": self.failed_gates,
            "gates": [g.as_record for g in self.gates],
            "measurements": self.measurements,
        }
        if self.wer is not None:
            # The normalized pair and the resulting WER, so a disputed gate
            # can be replayed by eye without re-running anything.
            rec["wer"] = self.wer.as_record
        if self.quality is not None:
            rec["audio_quality"] = self.quality.as_record
        if self.transcript is not None:
            rec["transcript_raw"] = self.transcript
        if self.asr_error:
            rec["asr_error"] = self.asr_error
        if self.decode_error:
            rec["decode_error"] = self.decode_error
        return rec


@dataclass(frozen=True)
class AudioFacts:
    samples: np.ndarray
    sample_rate: int
    duration_s: float
    peak: float
    rms_dbfs: float
    lead_silence_s: float
    trail_silence_s: float
    max_internal_silence_s: float
    clip_ratio: float
    sustained_clipping: bool
    channels: int
    # RMS of the lead and trail silence - the room tone a mastering spec
    # measures. None when there is not enough silence to measure it.
    noise_floor_dbfs: float | None

    @property
    def trimmed_duration_s(self) -> float:
        """
        Duration with leading and trailing silence removed - what an editor
        gets after topping and tailing. This, not the raw file length, is
        what has to fit a 15-second slot or a shot, because nobody ships the
        silence. Gating raw duration would let a model pass by padding.
        """
        return max(0.0, self.duration_s - self.lead_silence_s - self.trail_silence_s)


def _dbfs(v: float) -> float:
    return 20.0 * float(np.log10(max(v, 1e-12)))


def decode(path: Path) -> AudioFacts:
    """Decode and measure. Raises if the file is not readable audio."""
    data, rate = sf.read(str(path), always_2d=True, dtype="float64")
    channels = data.shape[1]
    x = data.mean(axis=1)
    if x.size == 0:
        raise ValueError("audio file decoded to zero samples")

    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x**2)))

    frame = max(1, int(SILENCE_FRAME_S * rate))
    n = x.size // frame
    if n >= 1:
        frames = x[: n * frame].reshape(n, frame)
        loud = np.max(np.abs(frames), axis=1) > max(peak * SILENCE_REL, 1e-5)
    else:
        loud = np.array([peak > 1e-5])

    idx = np.flatnonzero(loud)
    if idx.size == 0:
        lead = trail = float(x.size / rate)
        longest_gap = lead
    else:
        lead = float(idx[0] * frame / rate)
        trail = float((len(loud) - 1 - idx[-1]) * frame / rate)
        gaps = np.diff(idx) - 1
        longest_gap = float(int(gaps.max()) * frame / rate) if gaps.size else 0.0

    # Room tone: the RMS of what sits before the first word and after the
    # last. Only meaningful when there IS speech (an all-silent clip has no
    # signal to be a floor UNDER) and enough of it to average.
    noise_floor: float | None = None
    if idx.size:
        lead_n, trail_n = int(lead * rate), int(trail * rate)
        quiet = np.concatenate([x[:lead_n], x[x.size - trail_n :] if trail_n else x[:0]])
        if quiet.size >= int(MIN_NOISE_WINDOW_S * rate):
            noise_floor = _dbfs(float(np.sqrt(np.mean(quiet**2))))

    clipped = np.abs(x) >= CLIP_LEVEL
    clip_ratio = float(np.mean(clipped))
    sustained = False
    if clipped.any():
        run = 0
        for c in clipped:
            run = run + 1 if c else 0
            if run >= CLIP_RUN_SAMPLES:
                sustained = True
                break

    return AudioFacts(
        samples=x,
        sample_rate=int(rate),
        duration_s=float(x.size / rate),
        peak=peak,
        rms_dbfs=_dbfs(rms),
        lead_silence_s=lead,
        trail_silence_s=trail,
        max_internal_silence_s=longest_gap,
        clip_ratio=clip_ratio,
        sustained_clipping=sustained,
        channels=channels,
        noise_floor_dbfs=noise_floor,
    )


def run_checks(
    scenario,
    model_id: str,
    audio_path: Path,
    transcript: str | None,
    asr_error: str | None,
    predictor: QualityPredictor,
    extra: dict[str, Any] | None = None,
) -> CheckReport:
    """
    Every voice gate and measurement for one cell.

    `transcript` is the RAW ASR output (or None when ASR failed). This
    function never calls a network service, so it is fully exercised offline.
    """
    report = CheckReport(scenario_id=scenario.id, model_id=model_id, asr_error=asr_error)

    # Gate 1 - it must decode. Everything downstream needs the samples.
    try:
        facts = decode(Path(audio_path))
    except Exception as exc:  # noqa: BLE001
        report.decode_error = f"{type(exc).__name__}: {exc}"
        report.add("decodes", False, report.decode_error)
        return report
    report.add("decodes", True, f"{facts.duration_s:.2f}s @ {facts.sample_rate}Hz, {facts.channels}ch")

    report.measurements.update(
        {
            "duration_s": round(facts.duration_s, 3),
            "trimmed_duration_s": round(facts.trimmed_duration_s, 3),
            "sample_rate": facts.sample_rate,
            "channels": facts.channels,
            "peak": round(facts.peak, 5),
            "peak_dbfs": round(_dbfs(facts.peak), 2),
            "rms_dbfs": round(facts.rms_dbfs, 2),
            "lead_silence_s": round(facts.lead_silence_s, 3),
            "trail_silence_s": round(facts.trail_silence_s, 3),
            "max_internal_silence_s": round(facts.max_internal_silence_s, 3),
            "clip_ratio": round(facts.clip_ratio, 6),
        }
    )
    if facts.noise_floor_dbfs is not None:
        report.measurements["noise_floor_dbfs"] = round(facts.noise_floor_dbfs, 2)
    else:
        report.measurements["noise_floor_unmeasured"] = True

    # Delivery rate, from the SCRIPT's word count over the trimmed length.
    # The script rather than the transcript on purpose: the production
    # question is "do my 44 words fit the slot", and a model that drops words
    # would otherwise look faster rather than worse. WER catches the dropping.
    script_words = len(normalize(scenario.text).split())
    if facts.trimmed_duration_s > 0.05 and script_words:
        wpm = script_words / (facts.trimmed_duration_s / 60.0)
        report.measurements["speech_rate_wpm"] = round(wpm, 1)
        report.measurements["script_words"] = script_words

    checks = scenario.checks or {}

    # Gate 2 - duration inside the scenario's declared range.
    dur = checks.get("duration_s")
    if isinstance(dur, dict):
        lo, hi = float(dur.get("min", 0)), float(dur.get("max", 1e9))
        ok = lo <= facts.duration_s <= hi
        report.add("duration_in_range", ok, f"{facts.duration_s:.2f}s vs [{lo}, {hi}]")

    # Gate 2b - TRIMMED duration, for anything that has to fit a fixed slot:
    # a 15-second spot, a shot in an edit. Separate from gate 2 because they
    # ask different questions - gate 2 asks "is this file sane", this asks
    # "does the read fit". A model cannot pass this one by padding with
    # silence, which is exactly why the gate is on the trimmed length.
    tdur = checks.get("trimmed_duration_s")
    if isinstance(tdur, dict):
        lo, hi = float(tdur.get("min", 0)), float(tdur.get("max", 1e9))
        got = facts.trimmed_duration_s
        report.add(
            "trimmed_duration_in_range",
            lo <= got <= hi,
            f"{got:.3f}s trimmed vs [{lo}, {hi}] "
            f"(raw {facts.duration_s:.3f}s, lead {facts.lead_silence_s:.2f} "
            f"trail {facts.trail_silence_s:.2f})",
        )

    # Gate 2c - delivery rate. Fast legal copy has to BE fast; a disclaimer
    # read at a comfortable 150 wpm has not done the job even if every word
    # is intelligible.
    rate_spec = checks.get("speech_rate_wpm")
    if isinstance(rate_spec, dict):
        got = report.measurements.get("speech_rate_wpm")
        if got is None:
            report.add("speech_rate_in_range", False, "clip too short to measure a rate")
        else:
            lo, hi = float(rate_spec.get("min", 0)), float(rate_spec.get("max", 1e9))
            report.add(
                "speech_rate_in_range",
                lo <= got <= hi,
                f"{got:.1f} wpm vs [{lo}, {hi}] "
                f"({report.measurements['script_words']} script words in "
                f"{facts.trimmed_duration_s:.2f}s)",
            )

    # Gate 2d - time to first audio. Game audio teams cite 300 ms as the
    # immersion threshold, which is a hard line rather than a preference: past
    # it, a player hears the gap before they hear the character.
    #
    # ttfa_ms arrives from the adapter, not from this file, because only the
    # code holding the socket can time the first chunk. It is NOT latency_ms -
    # whole-call latency is a different quantity by an order of magnitude, and
    # reporting one as the other would be the single most flattering error
    # available here.
    max_ttfa = checks.get("max_ttfa_ms")
    if max_ttfa is not None:
        got = (extra or {}).get("ttfa_ms")
        if got is None:
            report.add("ttfa_within_max", True,
                       "not evaluated - the call was not streamed, so there is no "
                       "first-chunk time. Whole-call latency is NOT a substitute.")
            report.measurements["ttfa_unmeasured"] = True
        else:
            report.measurements["ttfa_ms"] = int(got)
            report.add("ttfa_within_max", int(got) <= int(max_ttfa),
                       f"first audio at {int(got)} ms vs a {int(max_ttfa)} ms ceiling")

    # Gate 3 - not silent overall.
    not_silent = facts.peak > 1e-4 and facts.rms_dbfs > -70.0
    report.add("not_silent", not_silent, f"peak={facts.peak:.4f} rms={facts.rms_dbfs:.1f} dBFS")

    # Gate 4 - no silence gap beyond the scenario's tolerance, at either end
    # or in the middle. A model that pauses for four seconds mid-sentence is
    # broken even though every word is present.
    max_sil = checks.get("max_silence_s")
    if max_sil is not None:
        limit = float(max_sil)
        worst = max(facts.lead_silence_s, facts.trail_silence_s, facts.max_internal_silence_s)
        report.add(
            "silence_within_bounds",
            worst <= limit,
            f"worst silence {worst:.2f}s vs limit {limit}s "
            f"(lead={facts.lead_silence_s:.2f} trail={facts.trail_silence_s:.2f} "
            f"internal={facts.max_internal_silence_s:.2f})",
        )

    # Gate 5 - no sustained clipping.
    if checks.get("no_clipping", True):
        report.add(
            "no_clipping",
            not facts.sustained_clipping,
            f"clip_ratio={facts.clip_ratio:.5f} sustained={facts.sustained_clipping}",
        )

    # Gate 6 - loudness in sane bounds. Catches a near-inaudible render and a
    # render slammed into the ceiling; both are file defects, not taste.
    report.add(
        "loudness_sane",
        RMS_DBFS_MIN <= facts.rms_dbfs <= RMS_DBFS_MAX,
        f"rms={facts.rms_dbfs:.1f} dBFS vs [{RMS_DBFS_MIN}, {RMS_DBFS_MAX}]",
    )

    # Gate 6b - the mastering spec, when a scenario declares one. ACX (the
    # Audible/Amazon submission standard) is RMS between -23 and -18 dBFS,
    # peak no higher than -3 dBFS, noise floor no higher than -60 dBFS. These
    # are PUBLISHED THRESHOLDS, not taste, which is what makes them worth
    # gating: the output either lands inside the spec or the production
    # pipeline has to master it first.
    rms_spec = checks.get("rms_dbfs")
    if isinstance(rms_spec, dict):
        lo, hi = float(rms_spec.get("min", -1e9)), float(rms_spec.get("max", 1e9))
        report.add(
            "rms_in_range",
            lo <= facts.rms_dbfs <= hi,
            f"rms {facts.rms_dbfs:.2f} dBFS vs [{lo}, {hi}]",
        )

    peak_max = checks.get("peak_dbfs_max")
    if peak_max is not None:
        got = _dbfs(facts.peak)
        report.add(
            "peak_below_max",
            got <= float(peak_max),
            f"peak {got:.2f} dBFS vs max {peak_max} dBFS",
        )

    floor_max = checks.get("noise_floor_dbfs_max")
    if floor_max is not None:
        if facts.noise_floor_dbfs is None:
            # No room tone to measure. UNMEASURED, not failed - the same rule
            # the ASR path follows. Absent evidence is a third state.
            report.add(
                "noise_floor_below_max",
                True,
                f"not evaluated - under {MIN_NOISE_WINDOW_S}s of lead/trail "
                f"silence to measure room tone in; noise floor is unmeasured, "
                f"not passing",
            )
        else:
            report.add(
                "noise_floor_below_max",
                facts.noise_floor_dbfs <= float(floor_max),
                f"noise floor {facts.noise_floor_dbfs:.2f} dBFS vs max {floor_max} dBFS",
            )

    # Measurement - objective audio quality. Free, local, deterministic.
    try:
        report.quality = predictor.predict(facts.samples, facts.sample_rate)
        report.measurements["audio_quality_1_5"] = round(report.quality.value, 3)
        report.measurements["audio_quality_predictor"] = report.quality.predictor
        report.measurements["audio_quality_is_mos"] = report.quality.is_mos
    except Exception as exc:  # noqa: BLE001 - a predictor failure is not a cell failure
        report.measurements["audio_quality_error"] = f"{type(exc).__name__}: {exc}"

    # Gate 7 and the headline measurement - normalized WER.
    #
    # `wer_reference` lets a negative-control scenario measure against text
    # that deliberately differs from what was spoken. Ordinary scenarios omit
    # it and the reference IS the script.
    reference = str(checks.get("wer_reference") or scenario.text)
    if transcript is None:
        # ASR failed after its retries. text_accuracy becomes UNMEASURED -
        # its weight is redistributed by the scorer and the report says so.
        # It is emphatically not a zero.
        report.measurements["text_accuracy_unmeasured"] = True
        report.add(
            "wer_within_max",
            True,
            f"not evaluated - ASR unavailable ({asr_error or 'no transcript'}); "
            f"text_accuracy is unmeasured, not zero",
        )
    else:
        report.transcript = transcript
        wer = normalized_wer(reference, transcript)
        report.wer = wer
        report.measurements["normalized_wer"] = round(wer.wer, 6)
        report.measurements["wer_reference_is_script"] = "wer_reference" not in checks
        max_wer = scenario.max_wer
        if max_wer is not None:
            report.add(
                "wer_within_max",
                wer.wer <= max_wer,
                f"normalized WER {wer.wer:.4f} vs max {max_wer}",
            )

        # Gate 8 - digit-exact readback. WER forgives one wrong digit in a
        # long string; an order number with one wrong digit is a total
        # failure. This is the check the transactional scenarios actually
        # specify: "any single digit error = fail".
        want = checks.get("must_say_digits")
        if want is not None:
            expected = "".join(ch for ch in str(want) if ch.isdigit())
            heard = extract_digit_sequence(transcript)
            report.measurements["digits_expected"] = expected
            report.measurements["digits_heard"] = heard
            report.add(
                "digits_exact",
                expected in heard,
                f"expected {expected!r}, transcript yielded {heard!r}",
            )

        # Gate 8b - character-exact ALPHANUMERIC readback, with the confusion
        # reported by class. A verification reference with one wrong character
        # is a failed call however good the clip sounds, and WER forgives one
        # character in eight. "Wrong at position 3" is a bug report; "turned
        # B into 8" is the finding this scenario was written to produce.
        want_alnum = checks.get("must_say_alnum")
        if want_alnum is not None:
            expected = "".join(ch for ch in str(want_alnum) if ch.isalnum()).lower()
            heard = extract_alnum_sequence(transcript)
            conf = alnum_confusions(expected, heard)
            report.measurements["alnum_expected"] = expected
            report.measurements["alnum_heard"] = heard
            report.measurements["alnum_confusions"] = conf
            report.add(
                "alnum_exact",
                expected in heard,
                f"expected {expected!r}, transcript yielded {heard!r} - "
                f"{conf['characters_correct']}/{conf['characters_expected']} characters"
                + (f", errors: " + ", ".join(
                    f"{e['expected']}->{e['heard']} ({e['confusable_class']})"
                    for e in conf["errors"][:4]) if conf["errors"] else ""),
            )

        # Gate 9 - the security assertion from VOI-TEL-09. A model asked to
        # confirm only the last four digits of a card must not read the whole
        # number, and that is a disclosure failure rather than a quality one,
        # so it is a gate and not a criterion.
        max_run = checks.get("max_digit_run")
        if max_run is not None:
            longest = longest_digit_run(transcript)
            report.measurements["longest_digit_run"] = longest
            report.add(
                "no_overlong_digit_run",
                longest <= int(max_run),
                f"longest spoken digit run {longest} vs permitted {max_run}",
            )

        # Gate 10 - required phrases, normalized on both sides. Covers the
        # amount-and-unit case ("4250.75 with the correct currency") without
        # needing a parser per value type.
        heard_norm = normalize(transcript)
        for phrase in checks.get("must_say") or []:
            need = normalize(str(phrase))
            report.add(
                f"must_say[{str(phrase)[:28]}]",
                says(heard_norm, phrase),
                f"looked for {need!r} as whole words",
            )

        # Gate 10b - alternation. An invented proper noun has no spelling a
        # transcriber can be expected to reproduce: "Ironhaus", read correctly,
        # came back as "Ironhouse" from one model and "Iron House" from the
        # other, and a must_say for the literal spelling failed BOTH for
        # pronouncing it right. Any one of the listed forms satisfies the gate,
        # so the check tests the READING rather than the transcriber's spelling.
        for group in checks.get("must_say_any") or []:
            options = [str(g) for g in (group if isinstance(group, (list, tuple)) else [group])]
            label = "|".join(options)[:26]
            report.add(
                f"must_say_any[{label}]",
                any(says(heard_norm, o) for o in options),
                f"any of {[normalize(o) for o in options]}",
            )

        # Gate 11 - phrases that must NOT appear. The positive check cannot
        # express "read this the local way": a model that says "two hundred
        # fifty thousand rupees" where the numeral was 2,50,000 has said
        # something true, in the wrong convention, and only a negative
        # assertion catches it. Also the right shape for a disclosure rule -
        # "never speak the full account number" - where the failure is a
        # phrase being PRESENT.
        for phrase in checks.get("must_not_say") or []:
            avoid = normalize(str(phrase))
            report.add(
                f"must_not_say[{str(phrase)[:24]}]",
                not says(heard_norm, phrase),
                f"must not contain {avoid!r}",
            )

    return report
