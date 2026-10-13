"""
ASR - the transcript the WER check is measured against (plan v1.2 section 13).

Word accuracy via ASR is the single most valuable check in the voice lane:
cheap, objective, and it catches the failure that matters most in production
- a model that sounds beautiful while saying the wrong reference number.

The raw transcript is kept beside the audio as evidence (<model>.txt). It is
NEVER edited or cleaned; normalization happens in memory, at comparison time,
by the one shared function in runner.normalize.

FAILURE IS A STATE. Two retries, then the transcript is absent and the
text_accuracy criterion becomes unmeasured - its weight is redistributed and
the report says so. It never becomes a zero, which would declare whichever
model the ASR choked on to be the worst.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .cost import Cost, Usage, compute_cost
from .models import ServiceSpec
from .normalize import _PUNCT  # keeps Devanagari and Telugu words whole

ASR_MAX_ATTEMPTS = 3  # the first call plus the plan's two retries

# An LLM asked to transcribe can fall into a loop and emit the passage twice.
# Observed 2026-09-02 on a 48.8s clip: 240 words returned for a 121-word
# script, reproducibly, from audio that a half-clip transcription proved
# contained ONE clean read. Left alone it fabricated a WER of 1.02 from a
# perfect clip and failed the gate - a wrong measurement presented as a model
# failure, which is the single worst thing this project can do.
#
# The guard is length-independent and needs no reference text: if the back
# half of a transcript restates the front half, the tail is the artefact.
REPEAT_MIN_WORDS = 24          # below this, a genuine refrain is more likely
REPEAT_MATCH_RATIO = 0.90      # how alike the halves must be to call it a repeat


# WHISPER'S OWN FABRICATIONS, and why a list is the honest fix.
#
# Whisper was trained on subtitle tracks, so at the end of a clip - especially
# over trailing silence - it emits the boilerplate those tracks end with. On
# 2026-09-03 three runs of ONE unchanged passage produced WERs of 0.099, 0.211
# and 0.479; the audio was identical to within a second, and the difference
# was a "For more information, go to www.fema.gov" in one and a repeated
# middle section in another. Read at face value that is a model degrading
# catastrophically on long-form narration. It is the recogniser talking.
#
# `condition_on_previous_text=False` and VAD filtering (set in the backend
# below) remove the repetition loops. They do NOT remove the boilerplate -
# they make it consistent, which is worse, because a uniform +0.13 WER looks
# like a real measurement.
#
# So: strip these phrases when they appear at the TAIL, and record that it
# happened. Never mid-transcript, where a scenario could legitimately contain
# the words, and never silently. tests/test_scenario_bank.py asserts no script
# in the bank ends with one, so this can never remove real content.
TAIL_HALLUCINATIONS = (
    "thanks for watching", "thank you for watching", "thanks for listening",
    "thank you for listening", "please subscribe", "subscribe to my channel",
    "like and subscribe", "for more information go to www fema gov",
    "for more information visit www fema gov", "www mooji org",
    "transcription by castingwords", "subtitles by the amara org community",
    "the end", "bye", "you",
)


def strip_tail_hallucination(text: str) -> tuple[str, list[str]]:
    """
    Return (transcript, phrases_removed).

    Only trailing sentences, only exact boilerplate, repeatedly - Whisper
    sometimes stacks two of them.
    """
    removed: list[str] = []
    body = text.rstrip()
    for _ in range(4):
        parts = [p for p in re.split(r"(?<=[.!?])\s+", body) if p.strip()]
        if len(parts) < 2:
            break
        last = re.sub(r"[^a-z0-9 ]+", " ", parts[-1].lower())
        last = re.sub(r"\s+", " ", last).strip()
        if last in TAIL_HALLUCINATIONS:
            removed.append(parts[-1].strip())
            body = " ".join(parts[:-1]).rstrip()
            continue
        break
    return body, removed


def _strip(word: str) -> str:
    return word.lower().strip(".,;:!?\"'()[]")


# A number as a transcriber writes it in digits. Space-separated groups are
# one number ("481 902 773", "4, 2, 1, 9"), as normalize() also treats them.
_DIGIT_GROUP = re.compile(r"\d[\d,.:]*(?:\s+\d[\d,.:]*)*")
_NUMBER_WORDS = frozenset(
    "zero oh one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand lakh lakhs crore crores million "
    "billion first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
    "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
    "nineteenth twentieth thirtieth".split()
    # Hindi, as the transcriber writes a Hinglish or Hindi read in Devanagari.
    # Without these a correct "तीन लाख चालीस हजार" counted as no number at
    # all and every Hindi spelled-out pass was set aside (2026-10-09).
    + """शून्य एक दो तीन चार पांच पाँच छह छः छे सात आठ नौ दस ग्यारह ग्यारा बारह बारा
    तेरह तेरा चौदह पंद्रह पन्द्रह सोलह सत्रह अठारह उन्नीस बीस इक्कीस बाईस तेईस
    चौबीस पच्चीस पचीस पच्चिस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस
    तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस
    तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन
    तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ
    पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर
    पचहत्तर पचचतर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी
    चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे
    चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे सौ सो हज़ार हजार अजार
    लाख लाक लाग करोड़ करोड""".split()
)
_NUMBER_JOINERS = frozenset({"and", "point"})


def _number_word_runs(text: str) -> int:
    """How many separate spoken numbers a words-only transcript contains."""
    runs, inside = 0, False
    for w in _PUNCT.sub(" ", text.lower()).split():
        if w in _NUMBER_WORDS:
            runs += 0 if inside else 1
            inside = True
        elif not (inside and w in _NUMBER_JOINERS):
            inside = False
    return runs


SPELLED_LOOP_RUN = 4          # the same word this many times in a row is a loop
SPELLED_MIN_LENGTH_RATIO = 0.7  # spelled-out words vs normal-transcript words


def spelled_pass_problem(normal: str, spelled: str) -> str | None:
    """
    Why the spelled-out transcript cannot be trusted, or None when it can.

    With digits banned, the recogniser sometimes has no way out: on a clip it
    heard as "1250" it wrote "AED Rs. Rs. Rs. Rs. ..." and dropped the rest of
    the sentence (2026-10-09, bank-en-04, both large-v3 and medium). Read as
    evidence, that loop would fail a must_say for a correct read. Spelled-out
    numbers only ever ADD words, so a pass much shorter than the normal one,
    or one stuck on a word the normal pass never repeats, is the recogniser
    failing - and the phrase checks go back to the normal transcript.
    """
    def words(t: str) -> list[str]:
        return [w for w in _PUNCT.sub(" ", t.lower()).split() if w]

    n, s = words(normal), words(spelled)
    if not s:
        return "empty"
    if len(s) < SPELLED_MIN_LENGTH_RATIO * len(n):
        return f"{len(s)} words against {len(n)} in the normal transcript"
    run = 1
    for i in range(1, len(s)):
        run = run + 1 if s[i] == s[i - 1] else 1
        if run >= SPELLED_LOOP_RUN and " ".join([s[i]] * SPELLED_LOOP_RUN) not in " ".join(n):
            return f"'{s[i]}' repeated {run}+ times in a row"
    # Every number the normal pass heard must come back as number words. The
    # pass can also DROP a number and keep the sentence: "A transaction of
    # ¥, was just made" for a clip the normal pass wrote "114,999 rupees"
    # (2026-10-09, bank-en-05) - not short, not looping, and it passed a
    # must_not_say on a number it never wrote down.
    heard, spelled_out = len(_DIGIT_GROUP.findall(normal)), _number_word_runs(spelled)
    if spelled_out < heard:
        return f"{heard - spelled_out} of {heard} numbers missing"
    return None


def collapse_repeated_transcript(text: str) -> tuple[str, bool]:
    """
    Return (transcript, was_collapsed).

    Finds a split point where everything after it restates everything before
    it, and drops the restatement. Deliberately conservative: it needs a long
    transcript and a near-exact match, because a script that genuinely repeats
    a line must not be truncated. When it fires, the caller records that it
    fired - a silently repaired measurement is its own kind of lie.
    """
    words = text.split()
    n = len(words)
    if n < REPEAT_MIN_WORDS:
        return text, False

    # A hallucinated repeat restarts near the middle; scan plausible splits.
    for cut in range(n // 3, (2 * n) // 3 + 1):
        head, tail = words[:cut], words[cut:]
        overlap = min(len(head), len(tail))
        if overlap < REPEAT_MIN_WORDS // 2:
            continue
        same = sum(
            1 for a, b in zip(head[:overlap], tail[:overlap]) if _strip(a) == _strip(b)
        )
        if same / overlap >= REPEAT_MATCH_RATIO:
            return " ".join(head), True
    return text, False


@dataclass(frozen=True)
class AsrResult:
    text: str
    provider_model: str
    latency_ms: int
    cost: Cost
    attempts: int
    # True when a hallucinated repeat was detected and removed. Carried into
    # telemetry and the check record so the repair is visible, never silent.
    repeat_collapsed: bool = False
    # Subtitle boilerplate removed from the tail, verbatim. Same rule: a
    # repaired measurement that does not say it was repaired is a lie.
    tail_stripped: tuple[str, ...] = ()


@dataclass(frozen=True)
class AsrFailure:
    error: str
    attempts: int
    provider_model: str


class AsrBackend(Protocol):
    def transcribe(self, audio_path: Path, language: str | None) -> str: ...


class OpenAITranscribeBackend:
    def __init__(self, spec: ServiceSpec) -> None:
        import os

        from openai import OpenAI

        key = os.environ.get(spec.auth_env)
        if not key:
            raise RuntimeError(f"${spec.auth_env} is not set - ASR cannot run")
        self.spec = spec
        self._client = OpenAI(api_key=key)

    def transcribe(self, audio_path: Path, language: str | None) -> str:
        kwargs = {"model": self.spec.provider_model, "response_format": "text"}
        if language:
            # The API wants a bare ISO-639-1 code; scenarios carry en-IN.
            kwargs["language"] = language.split("-")[0]
        with Path(audio_path).open("rb") as fh:
            resp = self._client.audio.transcriptions.create(file=fh, **kwargs)
        return resp if isinstance(resp, str) else getattr(resp, "text", "")


class GeminiTranscribeBackend:
    """
    Transcription via Gemini's native audio input (Vertex ADC or AI Studio).

    Gemini is not a dedicated ASR, so the prompt has to pin it down hard: a
    verbatim transcript and nothing else. Any preamble ("Here is the
    transcript:") would be counted as inserted words by the WER check and
    would punish the TTS model for the transcriber's manners.
    """

    PROMPT = (
        "Transcribe this audio verbatim. Output ONLY the words that are spoken, as one "
        "plain paragraph. Do not add a preamble, a heading, quotation marks, speaker "
        "labels, timestamps, or any commentary. Do not correct or paraphrase anything. "
        "If a number is spoken digit by digit, write those digits."
    )

    def __init__(self, spec: ServiceSpec) -> None:
        import os

        from google import genai
        from google.genai import types as _types

        if not os.environ.get(spec.auth_env):
            raise RuntimeError(f"${spec.auth_env} is not set - ASR cannot run")
        self.spec = spec
        self._types = _types
        project = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            from .gcp_auth import vertex_credentials

            self._client = genai.Client(
                vertexai=True,
                project=project,
                location=getattr(spec, "region", None) or "us-central1",
                http_options=_types.HttpOptions(timeout=120_000),
                credentials=vertex_credentials(),
            )
        else:
            self._client = genai.Client(api_key=os.environ[spec.auth_env])
        self.last_usage: Usage | None = None

    def transcribe(self, audio_path: Path, language: str | None) -> str:
        types = self._types
        prompt = self.PROMPT
        if language:
            prompt += f" The audio is in {language}."
        resp = self._client.models.generate_content(
            model=self.spec.provider_model,
            contents=[
                types.Part.from_bytes(data=Path(audio_path).read_bytes(), mime_type="audio/wav"),
                prompt,
            ],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            audio_in = 0
            for d in getattr(um, "prompt_tokens_details", None) or []:
                if str(getattr(d, "modality", "")).upper().endswith("AUDIO"):
                    audio_in = int(getattr(d, "token_count", 0) or 0)
            self.last_usage = Usage(
                reported=True,
                input_tokens=max(0, int(um.prompt_token_count or 0) - audio_in),
                audio_in_tokens=audio_in,
                output_tokens=int(um.candidates_token_count or 0),
                raw={"total_token_count": getattr(um, "total_token_count", None)},
            )
        return (resp.text or "").strip()


_BACKENDS = {
    "openai_transcribe": OpenAITranscribeBackend,
    "gemini_transcribe": GeminiTranscribeBackend,
}


def build_backend(spec: ServiceSpec) -> AsrBackend:
    backend = _BACKENDS.get(spec.adapter)
    if backend is None:
        raise RuntimeError(
            f"no ASR backend registered for '{spec.adapter}'. Known: {sorted(_BACKENDS)}"
        )
    return backend(spec)


class Asr:
    """Retrying wrapper that also prices the call."""

    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec
        self._backend = build_backend(spec)

    def _call_backend(self, audio_path: Path, language: str | None,
                      hotwords: list[str] | None, spelled: bool) -> str:
        if spelled:
            # Only a backend that can ban tokens offers this pass; any other
            # raises here, and the pass is recorded as failed.
            return self._backend.transcribe(audio_path, language, hotwords, spelled=True)
        try:
            return self._backend.transcribe(audio_path, language, hotwords)
        except TypeError:
            # Backends that predate biasing keep working unchanged.
            return self._backend.transcribe(audio_path, language)

    def transcribe(
        self, audio_path: Path, duration_s: float, language: str | None = None,
        hotwords: list[str] | None = None,
        spelled: bool = False,
    ) -> AsrResult | AsrFailure:
        last = "unknown failure"
        started = time.perf_counter()
        for attempt in range(1, ASR_MAX_ATTEMPTS + 1):
            try:
                text = self._call_backend(Path(audio_path), language, hotwords, spelled)
            except Exception as exc:  # noqa: BLE001 - any ASR failure is the same state
                last = f"{type(exc).__name__}: {exc}"
                if attempt < ASR_MAX_ATTEMPTS:
                    time.sleep(min(8.0, 2.0**attempt))
                continue
            if not (text or "").strip():
                last = "ASR returned an empty transcript"
                if attempt < ASR_MAX_ATTEMPTS:
                    time.sleep(min(8.0, 2.0**attempt))
                continue
            # Two billing shapes, handled honestly rather than assumed:
            # a per-minute ASR bills on the MEASURED duration of the file we
            # sent, while a token-billed model (Gemini) reports its own usage
            # and is priced from that.
            reported = getattr(self._backend, "last_usage", None)
            usage = (
                reported
                if reported is not None and self.spec.price.unit == "tokens"
                else Usage(reported=False, audio_seconds=duration_s)
            )
            cleaned, collapsed = collapse_repeated_transcript(text.strip())
            cleaned, stripped = strip_tail_hallucination(cleaned)
            return AsrResult(
                text=cleaned,
                provider_model=self.spec.provider_model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                cost=compute_cost(usage, self.spec.price, label=f"asr:{self.spec.provider_model}"),
                attempts=attempt,
                repeat_collapsed=collapsed,
                tail_stripped=tuple(stripped),
            )
        return AsrFailure(error=last, attempts=ASR_MAX_ATTEMPTS, provider_model=self.spec.provider_model)


class LocalWhisperBackend:
    """
    Whisper, run locally. NO network, NO key, NO vendor overlap with either
    model under test.

    WHY THIS EXISTS, and it is the most important thing in this file. The ASR
    produces the transcript that every WER number and every must_say gate is
    measured against - `text_accuracy` alone is 30% of the weighted score. Up
    to 2026-09-03 that ASR was gemini-2.5-flash while one of the two models
    under test was gemini-3.1-flash-tts, and the Google arm won on every
    ASR-mediated metric (median WER 0.0143 vs 0.0246; 6 phrase-gate failures
    vs 10). Nothing in that data can distinguish "Gemini speaks more clearly"
    from "Google's recogniser understands Google's synthesiser better", and a
    comparison that cannot separate those two is not a comparison.

    Whisper is OpenAI-trained, and neither model under test is OpenAI - so it
    is neutral with respect to THIS comparison, which is the property that
    matters. It is not necessarily the most accurate recogniser available;
    accuracy that favours one arm is worth less here than accuracy that
    favours neither.
    """

    def __init__(self, spec: ServiceSpec) -> None:
        from faster_whisper import WhisperModel

        self.spec = spec
        # provider_model names the Whisper size ("small.en", "medium", ...).
        # int8 on CPU keeps a 40-clip pass to a few minutes with no GPU.
        self._model = WhisperModel(
            spec.provider_model or "small.en", device="cpu", compute_type="int8"
        )
        self.last_usage: Usage | None = None
        self._digit_tokens: list[int] | None = None

    def _banned_digit_tokens(self) -> list[int]:
        """
        Every TEXT token that contains a digit - computed once.

        Only ids below <|endoftext|>: the special tokens after it include the
        timestamp markers ("<|0.00|>"), which contain digits too, and banning
        them broke decoding outright - large-v3 returned "*" for a whole clip
        (2026-10-09). The bug is in the list, not the model.
        """
        if self._digit_tokens is None:
            tok = self._model.hf_tokenizer
            first_special = tok.token_to_id("<|endoftext|>")
            self._digit_tokens = [
                i for i in range(first_special)
                if any(ch.isdigit() for ch in tok.decode([i]))
            ]
        return self._digit_tokens

    def transcribe(self, audio_path: Path, language: str | None,
                   hotwords: list[str] | None = None, spelled: bool = False) -> str:
        kwargs: dict = {"beam_size": 5}
        if spelled:
            # THE SPELLED-OUT PASS. Every token containing a digit is banned,
            # so a number must come back as the words that were spoken. The
            # normal pass writes "340,000" whether the speaker said "three
            # lakh forty thousand" or "three hundred forty thousand"; this
            # one cannot. No prompt and no hint toward either reading - the
            # ban is the only difference from the normal pass.
            kwargs["suppress_tokens"] = [-1] + self._banned_digit_tokens()
        if hotwords:
            # Bias the recogniser toward names it has no reason to know. A
            # coined brand or place name is not in its vocabulary, so a
            # CORRECT reading comes back mis-spelled and fails a phrase gate
            # that no model could have passed - which is how "Bhiwandi"
            # became "Bawande" and "Nykaa" became four different words.
            # Biasing does not make a wrong reading right: it removes the
            # transcriber's ignorance as a confound, leaving the model's
            # pronunciation as what is measured.
            kwargs["hotwords"] = " ".join(dict.fromkeys(hotwords))
        if language:
            code = language.split("-")[0]
            # An English-only model rejects a language argument entirely.
            if not (self.spec.provider_model or "").endswith(".en"):
                kwargs["language"] = code
        # Whisper loops and invents over silence. condition_on_previous_text
        # is the repetition driver; VAD removes the silence the boilerplate
        # spawns in. Measured 2026-09-03: WER variance across three runs of
        # one unchanged passage fell from 0.099-0.479 to 0.127-0.141.
        kwargs.update(
            condition_on_previous_text=False,
            vad_filter=True,
            hallucination_silence_threshold=2.0,
        )
        segments, _info = self._model.transcribe(str(Path(audio_path)), **kwargs)
        return " ".join(s.text for s in segments).strip()


_BACKENDS["local_whisper"] = LocalWhisperBackend
