"""
Speaker identity, exercised on the clips this project actually produced.

These are the only tests in the suite that touch real audio. They stay offline
- no key, no network - but they are skipped when no run directory exists, so a
fresh clone still runs green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runner.voiceprint import (
    compare, cosine, embed, holds_identity, voices_are_distinct,
)

ROOT = Path(__file__).resolve().parent.parent


def clips_for(model: str, n: int = 3) -> dict[str, Path]:
    found = sorted(ROOT.glob(f"runs/2026*/outputs/voice/*/{model}*.wav"))
    if len(found) < n:
        pytest.skip(f"needs {n} {model} clips on disk; found {len(found)}")
    return {f"clip {i+1}": p for i, p in enumerate(found[:n])}


def test_a_model_sounds_like_itself_across_scenarios():
    r = compare(clips_for("elevenlabs", 3))
    assert r.n == 3 and len(r.pairs) == 3
    # Measured 2026-09-03: ElevenLabs holds identity at ~0.976 across scenarios.
    assert r.lowest[2] > 0.90, r.as_record


def test_two_different_models_are_not_the_same_voice():
    el = sorted(ROOT.glob("runs/2026*/outputs/voice/*/elevenlabs*.wav"))
    ge = sorted(ROOT.glob("runs/2026*/outputs/voice/*/gemini*.wav"))
    if not el or not ge:
        pytest.skip("needs one clip from each model")
    v = cosine(embed(el[0]), embed(ge[0]))
    # The floor is NOT zero - different TTS voices sit around 0.64-0.78 - which
    # is exactly why a threshold has to be chosen against measured values.
    assert v < 0.90, f"two different models scored {v:.4f}"


def test_one_clip_is_unmeasured_not_a_perfect_score():
    """
    The rule the whole harness follows: absent evidence is a third state. A
    single clip cannot disagree with itself, and returning a passing 1.0 would
    claim consistency from evidence incapable of showing any.
    """
    one = dict(list(clips_for("elevenlabs", 1).items())[:1])
    v = holds_identity(one, min_cosine=0.90)
    assert v.measured is False
    assert "at least two" in v.detail
    assert v.result is None


def test_identity_reads_the_worst_pair_not_the_mean():
    """
    A narrator who holds the voice for nine episodes and loses it in the tenth
    has not held the voice - so a high mean must not rescue a bad pair.
    """
    mixed = dict(clips_for("elevenlabs", 2))
    ge = sorted(ROOT.glob("runs/2026*/outputs/voice/*/gemini*.wav"))
    if not ge:
        pytest.skip("needs a gemini clip")
    mixed["intruder"] = ge[0]
    v = holds_identity(mixed, min_cosine=0.90)
    assert v.measured and not v.passed
    assert "intruder" in v.detail


def test_distinctness_reads_the_closest_pair():
    """A cast of three containing two identical voices is a cast of two."""
    el = sorted(ROOT.glob("runs/2026*/outputs/voice/*/elevenlabs*.wav"))
    ge = sorted(ROOT.glob("runs/2026*/outputs/voice/*/gemini*.wav"))
    if len(el) < 2 or not ge:
        pytest.skip("needs two elevenlabs clips and one gemini clip")
    # Two clips from one model are the SAME voice; the gate must catch them.
    v = voices_are_distinct({"a": el[0], "b": el[1], "c": ge[0]}, max_cosine=0.80)
    assert v.measured and not v.passed
    assert {v.result.highest[0], v.result.highest[1]} == {"a", "b"}


def test_an_unembeddable_clip_is_recorded_not_dropped(tmp_path):
    """
    Silently skipping a bad clip is how six voices with two identical ones pass
    a distinctness gate as four.
    """
    bad = tmp_path / "not-audio.wav"
    bad.write_bytes(b"this is not a wav file")
    good = clips_for("elevenlabs", 2)
    r = compare({**good, "broken": bad})
    assert [c for c, _ in r.failed] == ["broken"]
    assert "broken" not in r.labels
    assert "embedding_failures" in r.as_record


def test_importing_the_module_does_not_load_the_encoder():
    """
    The offline suite must stay fast, so the torch weights load on FIRST USE,
    not on import. Checked in a fresh interpreter, because by the time the
    tests above have run the encoder is already cached in this one.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, runner.voiceprint as vp; "
         "print(vp._ENCODER is None, 'resemblyzer' in sys.modules)"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["True", "False"], out.stdout
