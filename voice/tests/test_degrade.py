"""
Named acoustic conditions, exercised on synthesised audio.

Offline: no key, no network, no run directory needed. What matters here is
that a condition genuinely degrades the signal, keeps the LEVEL sane, and
never touches the master.
"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from runner.checks import RMS_DBFS_MAX, RMS_DBFS_MIN, decode
from runner.degrade import CONDITIONS, apply_condition

RATE = 24000


def speechlike(seconds: float = 3.0, amp: float = 0.25) -> np.ndarray:
    t = np.linspace(0, seconds, int(seconds * RATE), endpoint=False)
    carrier = np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 2200 * t)
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 3.0 * t)
    return (amp * carrier * envelope).astype("float32")


@pytest.fixture
def master(tmp_path):
    p = tmp_path / "clip.wav"
    sf.write(str(p), speechlike(), RATE, subtype="PCM_16")
    return p


@pytest.mark.parametrize("condition", sorted(CONDITIONS))
def test_the_master_is_never_modified(master, condition):
    """
    A degraded file is DERIVED. Overwriting the master would leave the run
    unable to answer what the model actually generated.
    """
    before = master.read_bytes()
    out = apply_condition(master, condition)
    assert master.read_bytes() == before
    assert out.path != master and out.path.exists()


@pytest.mark.parametrize("condition", sorted(CONDITIONS))
def test_every_condition_keeps_the_level_sane(master, condition):
    """
    The defect the Phase 0 probe found: far-field landed at -59.7 dBFS, under
    the loudness floor, so every clip would have failed on LEVEL for a reason
    with nothing to do with the model. A real capture chain applies gain.
    """
    facts = decode(apply_condition(master, condition).path)
    assert RMS_DBFS_MIN <= facts.rms_dbfs <= RMS_DBFS_MAX, (
        f"{condition} produced {facts.rms_dbfs:.1f} dBFS, outside the sane window"
    )
    assert not facts.sustained_clipping, f"{condition} clipped into the ceiling"


def test_telephony_actually_band_limits(master):
    out = apply_condition(master, "telephony_8k_ulaw")
    facts = decode(out.path)
    # A phone line cannot carry a 2.2 kHz partial's harmonics; the rate alone
    # proves the band limit, since 8 kHz cannot represent above 4 kHz.
    assert out.sample_rate == 8000
    assert facts.sample_rate == 8000


def test_far_field_really_smears_the_waveform(master):
    """
    Reverb decorrelates. If the output still correlates strongly with the
    source, the condition is a filter with a fancy name and the scenario is
    measuring nothing new.
    """
    out = apply_condition(master, "far_field_room")
    src, _ = sf.read(str(master), dtype="float32")
    got, _ = sf.read(str(out.path), dtype="float32")
    n = min(len(src), len(got))
    assert abs(np.corrcoef(src[:n], got[:n])[0, 1]) < 0.5


def test_a_condition_is_reproducible(master):
    """The room impulse is seeded, so the same clip degrades identically."""
    a = apply_condition(master, "far_field_room").path.read_bytes()
    b = apply_condition(master, "far_field_room").path.read_bytes()
    assert a == b


def test_an_unknown_condition_raises_rather_than_passing_through():
    """
    A scenario that asked to be measured over a phone line and silently got
    studio audio is worse than one that failed loudly.
    """
    with pytest.raises(ValueError, match="unknown acoustic condition"):
        apply_condition("nope.wav", "underwater")


def test_the_record_names_both_files(master):
    rec = apply_condition(master, "telephony_8k_ulaw").as_record
    assert rec["condition"] == "telephony_8k_ulaw"
    assert rec["measured_file"] != rec["source_file"]
    assert "unchanged beside it" in rec["note"]
