"""
The judge's four controls, proven without an API key.

Blinding is the single highest-value control in the design and it is also
the easiest to break by accident - one f-string that interpolates a model id
and every score is contaminated. These tests are the guard.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from runner.judge import blind_labels_for, build_prompt, strip_audio_metadata
from runner.rubrics import load_rubric

CONFIGS = Path(__file__).resolve().parent.parent / "configs"


@dataclass
class FakeScenario:
    id: str = "voi-003"
    task: str = "styled_tts"
    text: str = "Hi Priya, this is a callback about order 4-8-2-9-1-6."
    expected: str = "Every word accurate, apologetic but not theatrical."
    language: str | None = "en-IN"
    style: str | None = "warm, apologetic, unhurried"
    checks: dict[str, Any] = field(default_factory=dict)


FACTS = {
    "word error rate vs the script (normalized, measured by ASR)": "1.8%",
    "clip duration": "18.4s",
    "objective audio quality (objective signal metric, not a MOS, 1-5)": "3.90",
}


def test_labels_are_shuffled_but_reproducible():
    models = ["openai-tts-1", "openai-gpt-4o-mini-tts", "elevenlabs-flash-v2-5"]
    a = blind_labels_for("voi-001", models)
    b = blind_labels_for("voi-001", models)
    assert a == b, "the same scenario must map the same way on a re-judge"
    assert sorted(a.values()) == ["A", "B", "C"]


def test_order_differs_between_scenarios():
    """Judges favour position; a fixed order would bake that bias in."""
    models = ["m1", "m2", "m3", "m4"]
    orders = {tuple(sorted(blind_labels_for(f"voi-{i:03d}", models).items())) for i in range(12)}
    assert len(orders) > 1


def test_the_prompt_never_names_a_model_provider_or_file():
    rubric = load_rubric(CONFIGS, "voice", "styled_tts")
    prompt = build_prompt(FakeScenario(), rubric, FACTS, "B")
    lowered = prompt.lower()
    for forbidden in (
        "openai",
        "elevenlabs",
        "gemini",
        "google",
        "tts-1",
        "gpt-4o",
        "nova",
        ".wav",
        "model_id",
    ):
        assert forbidden not in lowered, f"blinding leak: {forbidden!r} appears in the judge prompt"
    assert "CLIP LABEL: B" in prompt


def test_the_judge_is_asked_only_for_judged_criteria():
    rubric = load_rubric(CONFIGS, "voice", "text_to_speech")
    prompt = build_prompt(FakeScenario(), rubric, FACTS, "A")
    asked = prompt.split("SCORE EXACTLY THESE CRITERIA")[1]
    for key in ("pronunciation", "naturalness", "clarity", "audio_quality"):
        assert key in asked
    # Word accuracy is measured, never judged.
    assert "text_accuracy" not in asked


def test_measured_facts_are_injected_and_flagged_as_not_re_scorable():
    rubric = load_rubric(CONFIGS, "voice", "text_to_speech")
    prompt = build_prompt(FakeScenario(), rubric, FACTS, "A")
    assert "MEASURED FACTS" in prompt
    assert "1.8%" in prompt
    assert "do NOT re-estimate" in prompt
    assert "do not judge whether the right words were said" in prompt


def test_the_style_task_asks_about_style_and_the_plain_task_does_not():
    plain = build_prompt(FakeScenario(), load_rubric(CONFIGS, "voice", "text_to_speech"), {}, "A")
    styled = build_prompt(FakeScenario(), load_rubric(CONFIGS, "voice", "styled_tts"), {}, "A")
    asked_plain = plain.split("SCORE EXACTLY THESE CRITERIA")[1]
    asked_styled = styled.split("SCORE EXACTLY THESE CRITERIA")[1]
    assert "style_adherence" in asked_styled
    assert "style_adherence" not in asked_plain


def test_reasoning_is_demanded_before_the_score():
    prompt = build_prompt(FakeScenario(), load_rubric(CONFIGS, "voice", "text_to_speech"), {}, "A")
    assert prompt.index("reasoning") < prompt.index('"score"')
    assert "reasoning` first" in prompt


def test_audio_is_re_encoded_so_provider_metadata_cannot_reach_the_judge(tmp_path):
    rate = 24000
    x = (0.2 * np.sin(2 * np.pi * 220 * np.linspace(0, 1, rate))).astype(np.float32)
    src = tmp_path / "tagged.wav"
    with sf.SoundFile(str(src), "w", rate, 1, subtype="PCM_16") as fh:
        fh.title = "generated-by-acme-voice-v9"
        fh.comment = "model=acme-9 voice=nova"
        fh.write(x)
    assert b"acme" in src.read_bytes(), "fixture did not actually embed metadata"

    cleaned, out_rate = strip_audio_metadata(src)
    assert out_rate == rate
    assert b"acme" not in cleaned
    # Still a real, decodable WAV.
    data, r = sf.read(io.BytesIO(cleaned))
    assert r == rate and len(data) == len(x)
