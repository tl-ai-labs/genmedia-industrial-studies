"""
The blind AI judge (plan v1.2 section 11).

WHAT THE JUDGE IS ALLOWED TO SCORE. Only criteria the rubric marks
`scored_by: judge` or `hybrid`. Word accuracy is measured by ASR and never
reaches the judge as a question - it reaches it as an established FACT, with
an explicit instruction not to restate it. That is what stops the most
reliable number in the lane being overwritten by an opinion.

THE FOUR CONTROLS, all of them cheap:
  Blind        the clip is relabelled A / B / C in a per-scenario shuffled
               order, the audio is re-encoded so no metadata survives, and
               no model, provider or file name appears in the prompt. The
               mapping lives only in judge.jsonl.
  Rotation     the shuffle seed is sha256(scenario_id), so the order differs
               per scenario and is reproducible run to run.
  Absolute     one clip per call, scored against the rubric and the
               scenario's `expected` text - never against the other clips.
               Comparative judging drifts with whatever else is in the batch.
  Fixed        one judge model, pinned, temperature 0, one prompt template,
               and the rubric hash recorded on every record.

JUDGE FAILURE IS A STATE, NOT A ZERO. API error -> two retries. Unparseable
JSON -> one repair retry with the schema restated. Out-of-range or missing
criterion -> rejected, one retry. After that the cell is `unjudged`: it shows
as a dash, is excluded from the mean, and is counted in a coverage column.
Averaging a missing score as 0 has one predictable outcome - it declares
whichever model the judge choked on to be the worst.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .cost import Cost, Usage, compute_cost
from .models import ServiceSpec
from .rubrics import Rubric

JUDGE_MAX_ATTEMPTS = 3
BLIND_LABELS = ("A", "B", "C", "D", "E", "F", "G", "H")


@dataclass(frozen=True)
class JudgedCriterion:
    name: str
    reasoning: str
    score: float


@dataclass
class JudgeRecord:
    scenario_id: str
    model_id: str
    status: str  # "judged" | "unjudged"
    blind_label: str
    rubric_hash: str
    judge_model: str
    prompt_sha256: str
    criteria: list[JudgedCriterion] = field(default_factory=list)
    overall_note: str = ""
    measured_facts: dict[str, Any] = field(default_factory=dict)
    cost: Cost | None = None
    latency_ms: int = 0
    attempts: int = 0
    error: str | None = None
    raw_response: str | None = None

    @property
    def as_record(self) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            # The blind map lives HERE and only here.
            "model_id": self.model_id,
            "blind_label": self.blind_label,
            "status": self.status,
            "rubric_hash": self.rubric_hash,
            "judge_model": self.judge_model,
            "prompt_sha256": self.prompt_sha256,
            "measured_facts": self.measured_facts,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
            "scores": {c.name: c.score for c in self.criteria},
            "reasoning": {c.name: c.reasoning for c in self.criteria},
            "overall_note": self.overall_note,
        }
        if self.cost is not None:
            rec["cost"] = self.cost.as_record
        if self.error:
            rec["error"] = self.error
        if self.raw_response is not None:
            rec["raw_response"] = self.raw_response
        return rec


def blind_labels_for(scenario_id: str, model_ids: list[str]) -> dict[str, str]:
    """
    model_id -> blind label, shuffled deterministically per scenario.

    Judges systematically favour the first (or last) item shown, so the order
    must differ per scenario; it must also be reproducible, or a re-judge
    would not be comparable with the first pass. sha256(scenario_id) gives
    both.
    """
    seed = int.from_bytes(hashlib.sha256(scenario_id.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    order = list(model_ids)
    perm = rng.permutation(len(order))
    return {order[int(i)]: BLIND_LABELS[pos] for pos, i in enumerate(perm)}


def strip_audio_metadata(path: Path) -> tuple[bytes, int]:
    """
    Re-encode to a bare WAV from decoded samples. Every provider chunk -
    LIST/INFO, iXML, vendor tags - is dropped simply by not being carried
    across, so the judge cannot read a model name out of the file it is
    scoring. Returns (wav_bytes, sample_rate).
    """
    data, rate = sf.read(str(path), always_2d=True, dtype="float32")
    mono = data.mean(axis=1)
    buf = io.BytesIO()
    sf.write(buf, mono, int(rate), format="WAV", subtype="PCM_16")
    return buf.getvalue(), int(rate)


def build_prompt(scenario, rubric: Rubric, measured_facts: dict[str, Any], label: str) -> str:
    """
    The judge prompt. Nothing in it names a model, a provider, a file or a
    run - the only identifier the judge ever sees is the blind label.
    """
    judged = rubric.judged_criteria
    lines: list[str] = []
    lines.append(
        "You are evaluating ONE generated audio clip against a brief. Score only what "
        "you can hear. You will never be told which system produced it."
    )
    lines.append(f"\nCLIP LABEL: {label}")
    lines.append("\nSCRIPT THE SPEAKER WAS GIVEN (verbatim):\n" + scenario.text)
    if scenario.style:
        lines.append("\nSTYLE DIRECTIVE GIVEN TO THE SPEAKER:\n" + scenario.style)
    if scenario.language:
        lines.append(f"\nTARGET LANGUAGE / ACCENT: {scenario.language}")
    lines.append("\nEXPECTED RESULT:\n" + scenario.expected)

    if measured_facts:
        lines.append(
            "\nMEASURED FACTS (established by code - treat as TRUE and do NOT re-estimate "
            "or re-score them):"
        )
        for k, v in measured_facts.items():
            lines.append(f"  - {k}: {v}")
        lines.append(
            "  These are measurements, not opinions. Word accuracy in particular has "
            "already been measured against the script by automatic speech recognition; "
            "do not judge whether the right words were said, and do not let it influence "
            "the criteria below."
        )

    lines.append("\nSCORE EXACTLY THESE CRITERIA, and no others:")
    for c in judged:
        extra = ""
        if c.key == "audio_quality":
            extra = (
                " Judge ONLY artefacts, glitches, abrupt cuts and audible processing "
                "damage. The objective signal measurement is given above; do not restate it."
            )
        lines.append(f"  - {c.key} ({c.label}): {c.description}{extra}")

    lines.append(
        "\nFor each criterion give `reasoning` first - one or two sentences citing "
        "something specific you heard - and only then the `score`, an integer or one "
        "decimal from 0 to 10."
    )
    lines.append(
        "\nReturn JSON only, no prose outside it, in exactly this shape:\n"
        '{"criteria":[{"name":"<criterion key>","reasoning":"<1-2 sentences>",'
        '"score":<0-10>}],"overall_note":"<one sentence>"}'
    )
    lines.append(
        "The `criteria` array must contain exactly these keys, once each: "
        + ", ".join(c.key for c in judged)
    )
    return "\n".join(lines)


class OpenAIAudioJudge:
    def __init__(self, spec: ServiceSpec) -> None:
        import os

        from openai import OpenAI

        key = os.environ.get(spec.auth_env)
        if not key:
            raise RuntimeError(f"${spec.auth_env} is not set - the judge cannot run")
        self.spec = spec
        self._client = OpenAI(api_key=key)

    def ask(self, prompt: str, wav_bytes: bytes) -> tuple[str, Usage]:
        resp = self._client.chat.completions.create(
            model=self.spec.provider_model,
            temperature=self.spec.temperature,
            modalities=["text"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64.b64encode(wav_bytes).decode("ascii"),
                                "format": "wav",
                            },
                        },
                    ],
                }
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        u = resp.usage
        audio_in = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            audio_in = int(getattr(details, "audio_tokens", 0) or 0)
        return text, Usage(
            reported=True,
            input_tokens=max(0, int(u.prompt_tokens) - audio_in),
            audio_in_tokens=audio_in,
            output_tokens=int(u.completion_tokens),
            raw={"prompt_tokens": int(u.prompt_tokens), "completion_tokens": int(u.completion_tokens)},
        )


class GeminiAudioJudge:
    """
    Gemini with native audio input (Vertex ADC or AI Studio).

    The plan's recommended judge shape: one model that takes audio natively,
    at temperature 0, reporting its own token usage so the judge's cost is
    api_reported rather than estimated.
    """

    def __init__(self, spec: ServiceSpec) -> None:
        import os

        from google import genai
        from google.genai import types as _types

        if not os.environ.get(spec.auth_env):
            raise RuntimeError(f"${spec.auth_env} is not set - the judge cannot run")
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

    def ask(self, prompt: str, wav_bytes: bytes) -> tuple[str, Usage]:
        types = self._types
        resp = self._client.models.generate_content(
            model=self.spec.provider_model,
            contents=[types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"), prompt],
            config=types.GenerateContentConfig(
                temperature=self.spec.temperature,
                response_mime_type="application/json",
            ),
        )
        um = getattr(resp, "usage_metadata", None)
        audio_in = 0
        if um is not None:
            for d in getattr(um, "prompt_tokens_details", None) or []:
                if str(getattr(d, "modality", "")).upper().endswith("AUDIO"):
                    audio_in = int(getattr(d, "token_count", 0) or 0)
        return (resp.text or "").strip(), Usage(
            reported=um is not None,
            input_tokens=max(0, int(getattr(um, "prompt_token_count", 0) or 0) - audio_in),
            audio_in_tokens=audio_in,
            output_tokens=int(getattr(um, "candidates_token_count", 0) or 0),
            raw={"total_token_count": getattr(um, "total_token_count", None)},
        )


_JUDGES = {"openai_audio": OpenAIAudioJudge, "gemini_audio": GeminiAudioJudge}


def build_judge(spec: ServiceSpec):
    backend = _JUDGES.get(spec.adapter)
    if backend is None:
        raise RuntimeError(f"no judge backend registered for '{spec.adapter}'. Known: {sorted(_JUDGES)}")
    return backend(spec)


def _parse(text: str, rubric: Rubric) -> tuple[list[JudgedCriterion] | None, str, str]:
    """Returns (criteria | None, overall_note, error)."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body.strip("`")
        body = body.removeprefix("json").strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        return None, "", "response contained no JSON object"
    try:
        doc = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, "", f"unparseable JSON: {exc}"

    wanted = {c.key for c in rubric.judged_criteria}
    got: dict[str, JudgedCriterion] = {}
    for entry in doc.get("criteria") or []:
        name = str(entry.get("name", "")).strip()
        if name not in wanted:
            continue
        raw = entry.get("score")
        try:
            score = float(raw)
        except (TypeError, ValueError):
            return None, "", f"criterion '{name}' has a non-numeric score {raw!r}"
        if not 0.0 <= score <= 10.0:
            return None, "", f"criterion '{name}' score {score} is out of range 0..10"
        got[name] = JudgedCriterion(name, str(entry.get("reasoning", "")).strip(), score)

    missing = sorted(wanted - set(got))
    if missing:
        return None, "", f"response is missing criteria {missing}"
    return [got[c.key] for c in rubric.judged_criteria], str(doc.get("overall_note", "")).strip(), ""


def judge_cell(
    backend,
    spec: ServiceSpec,
    scenario,
    rubric: Rubric,
    model_id: str,
    blind_label: str,
    audio_path: Path,
    measured_facts: dict[str, Any],
) -> JudgeRecord:
    prompt = build_prompt(scenario, rubric, measured_facts, blind_label)
    record = JudgeRecord(
        scenario_id=scenario.id,
        model_id=model_id,
        status="unjudged",
        blind_label=blind_label,
        rubric_hash=rubric.rubric_hash,
        judge_model=spec.provider_model,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        measured_facts=measured_facts,
    )
    try:
        wav, _ = strip_audio_metadata(Path(audio_path))
    except Exception as exc:  # noqa: BLE001
        record.error = f"could not re-encode audio for blinding: {type(exc).__name__}: {exc}"
        return record

    started = time.perf_counter()
    total_micro = 0
    last_error = "unknown failure"
    ask_prompt = prompt

    for attempt in range(1, JUDGE_MAX_ATTEMPTS + 1):
        record.attempts = attempt
        try:
            text, usage = backend.ask(ask_prompt, wav)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < JUDGE_MAX_ATTEMPTS:
                time.sleep(min(10.0, 2.0**attempt))
            continue

        # A call that returned is a call that billed, whether or not we could
        # use the answer. Accumulate before deciding.
        cost = compute_cost(usage, spec.price, label=f"judge:{spec.provider_model}")
        total_micro += cost.micro_usd
        record.raw_response = text

        criteria, note, err = _parse(text, rubric)
        if criteria is not None:
            record.status = "judged"
            record.criteria = criteria
            record.overall_note = note
            record.latency_ms = int((time.perf_counter() - started) * 1000)
            record.cost = Cost(
                micro_usd=total_micro,
                basis=cost.basis,
                price_as_of=cost.price_as_of,
                price_source=cost.price_source,
                usage_source=cost.usage_source,
                usage_exact=cost.usage_exact,
            )
            return record

        last_error = err
        # One repair retry with the schema restated - then stop. A judge that
        # cannot produce the shape twice is not going to on the third go.
        ask_prompt = (
            prompt
            + f"\n\nYour previous response was rejected: {err}. Return ONLY the JSON "
            f"object described above, with every listed criterion present exactly once "
            f"and every score between 0 and 10."
        )

    record.latency_ms = int((time.perf_counter() - started) * 1000)
    record.error = last_error
    if total_micro:
        record.cost = Cost(
            micro_usd=total_micro,
            basis="tokens@judge-failed-attempts",
            price_as_of=spec.price.as_of,
            price_source=spec.price.source,
            usage_source="api_reported",
            usage_exact=True,
        )
    return record
