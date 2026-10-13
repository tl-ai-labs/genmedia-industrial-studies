"""
ElevenLabs request building - offline, no key, no network.

Covers what changed for Eleven v4 (2026-10-09): a style directive sent as an
audio tag, two-voice scenes sent to Text to Dialogue, and billing on the
characters actually sent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runner.adapters import GenRequest, ProviderError
from runner.adapters.elevenlabs_tts import ElevenLabsTtsAdapter, split_dialogue
from runner.models import load_registry

CONFIGS = Path(__file__).resolve().parent.parent / "configs"


def _adapter(model_id):
    spec = next(m for m in load_registry(CONFIGS).models if m.id == model_id)
    a = ElevenLabsTtsAdapter.__new__(ElevenLabsTtsAdapter)
    a.spec = spec
    a.id = spec.id
    return a


def _req(**kw):
    base = dict(task="styled_tts", text="I will not lose.", params={"voice": "male_older"},
                voice_id="H53NeKIHK4upDFLnIv9Z", voice_logical="male_older")
    return GenRequest(**{**base, **kw})


def test_v4_receives_the_style_as_one_leading_audio_tag():
    path, body, applied, billed = _adapter("elevenlabs-v4").build_request(
        _req(style="calm, menacing.", language="en-IN"))
    assert path == "/text-to-speech/H53NeKIHK4upDFLnIv9Z"
    assert body["text"] == "[calm, menacing] I will not lose."
    assert body["model_id"] == "eleven_v4"
    assert body["language_code"] == "en"
    assert applied["style"] == "calm, menacing."
    assert billed == len(body["text"])


def test_v3_without_the_opt_in_never_speaks_brackets_and_claims_no_style():
    path, body, applied, _ = _adapter("elevenlabs-v3").build_request(_req(style="calm"))
    assert body["text"] == "I will not lose."
    assert "style" not in applied


def test_streaming_uses_the_stream_route():
    path, *_ = _adapter("elevenlabs-v4").build_request(_req(measure_ttfa=True))
    assert path.endswith("/stream")


def test_a_two_voice_scene_goes_to_text_to_dialogue_one_input_per_line():
    req = _req(text="A: Where were you\nat eleven?\nB: At home!", style=None,
               speakers=(("A", "vid-a", "male_older", "cold"), ("B", "vid-b", "male_young", "defensive")))
    path, body, applied, billed = _adapter("elevenlabs-v4").build_request(req)
    assert path == "/text-to-dialogue"
    assert body["inputs"] == [
        {"text": "[cold] Where were you at eleven?", "voice_id": "vid-a"},
        {"text": "[defensive] At home!", "voice_id": "vid-b"},
    ]
    assert applied["speakers"] == ["A", "B"]
    assert billed == sum(len(i["text"]) for i in body["inputs"])


def test_dialogue_text_before_any_speaker_is_refused_not_guessed():
    with pytest.raises(ProviderError):
        split_dialogue("Hello there.\nA: Hi.", ["A", "B"])
