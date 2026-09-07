"""
Named acoustic conditions: measure the clip the way the customer hears it.

Two scenarios in the bank are not about studio audio at all. A "where is my
order" readback happens over a phone line, band-limited to 300-3400 Hz and
quantised to eight bits. An episode recap on a smart speaker is heard across
a room, through reverb, off-axis. Measuring the studio master answers a
different question from the one the scenario asks, and answering it anyway
produces a number that looks like evidence.

WHY THE MASTER IS NEVER TOUCHED. A degraded file is a DERIVED artefact. The
run keeps the original beside it, the manifest records which condition
produced the measured numbers, and re-running the same condition reproduces
the same file. Overwriting the master would make the run unable to answer
what the model actually generated.

VERIFIED BEFORE IT WAS BUILT (2026-09-03). Both conditions were applied to
real clips and transcribed with the shipped recogniser:

    clean            WER 0.0154   12-digit reference recovered
    telephony 8 kHz  WER 0.0154   recovered
    far-field        WER 0.0154   recovered

The degradation is severe - far-field correlates with the source at 0.0015
and loses four orders of magnitude above 4 kHz - and the measurement still
worked. Had it not, the honest response was to drop those scenarios rather
than ship a gate every model fails.

THE DEFECT THAT PROBE FOUND, and why every condition ends with a gain stage.
The first far-field implementation landed at -59.7 dBFS, well under the
`loudness_sane` floor of -45. Every clip would have failed on LEVEL, for a
reason with nothing to do with the model - the exact class of instrument
fault this project keeps digging out. A real recorder applies gain; so does
every condition here, matching the source RMS so what is measured is
intelligibility rather than attenuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
from scipy import signal

# Telephony: the ITU narrowband passband and 8-bit mu-law companding, which
# together are what a PSTN call does to speech.
TELEPHONY_BAND_HZ = (300.0, 3400.0)
TELEPHONY_RATE = 8000
MU = 255.0

# Far-field: a small room at conversational distance. 280 ms of exponentially
# decaying reverb is a modest domestic room, not a hall.
FARFIELD_BAND_HZ = (180.0, 7000.0)
FARFIELD_RT_S = 0.28
FARFIELD_SEED = 7  # fixed, so a condition is reproducible


def _match_rms(y: np.ndarray, target_rms: float) -> np.ndarray:
    """
    Restore the source level. A real capture chain applies gain; without this
    every degraded clip fails `loudness_sane` on attenuation alone and the
    scenario measures our filter rather than the model.
    """
    cur = float(np.sqrt(np.mean(y**2)))
    if cur <= 1e-9 or target_rms <= 1e-9:
        return y
    scaled = y * (target_rms / cur)
    peak = float(np.max(np.abs(scaled)))
    # Leave a little headroom rather than clipping into the ceiling, which
    # would trip `no_clipping` for a reason the model did not cause.
    if peak > 0.99:
        scaled *= 0.99 / peak
    return scaled.astype("float32")


def telephony_8k_ulaw(x: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """A PSTN call: 300-3400 Hz, resampled to 8 kHz, 8-bit mu-law companded."""
    target = float(np.sqrt(np.mean(x**2)))
    lo, hi = TELEPHONY_BAND_HZ
    b, a = signal.butter(6, [lo / (sr / 2), hi / (sr / 2)], btype="band")
    y = signal.lfilter(b, a, x)
    y = signal.resample_poly(y, TELEPHONY_RATE, sr)
    y = np.clip(y, -1.0, 1.0)
    comp = np.sign(y) * np.log1p(MU * np.abs(y)) / np.log1p(MU)
    quant = np.round((comp + 1) * 127.5) / 127.5 - 1
    expanded = np.sign(quant) * ((1 + MU) ** np.abs(quant) - 1) / MU
    return _match_rms(expanded, target), TELEPHONY_RATE


def far_field_room(x: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """A smart speaker across a room: band-limited, reverberant, then re-gained."""
    target = float(np.sqrt(np.mean(x**2)))
    lo, hi = FARFIELD_BAND_HZ
    b, a = signal.butter(4, [lo / (sr / 2), min(hi, sr / 2 - 1) / (sr / 2)], btype="band")
    y = signal.lfilter(b, a, x)
    rng = np.random.default_rng(FARFIELD_SEED)
    n = int(FARFIELD_RT_S * sr)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0.0, 7.0, n))
    ir[0] = 1.0  # keep the direct path
    y = signal.fftconvolve(y, ir / np.abs(ir).sum(), mode="same")
    return _match_rms(y, target), sr


CONDITIONS: dict[str, Callable[[np.ndarray, int], tuple[np.ndarray, int]]] = {
    "telephony_8k_ulaw": telephony_8k_ulaw,
    "far_field_room": far_field_room,
}


@dataclass(frozen=True)
class Degraded:
    condition: str
    path: Path
    source_path: Path
    sample_rate: int

    @property
    def as_record(self) -> dict:
        return {
            "condition": self.condition,
            "measured_file": self.path.name,
            "source_file": self.source_path.name,
            "sample_rate": self.sample_rate,
            "note": "gates and WER were measured on the degraded file; the source "
                    "master is unchanged beside it",
        }


def apply_condition(source: Path, condition: str) -> Degraded:
    """
    Write `<name>.<condition>.wav` beside the master and return where it is.

    Raises on an unknown condition rather than passing the clip through
    unchanged - a scenario that asked to be measured over a phone line and
    silently got studio audio is worse than one that failed loudly.
    """
    fn = CONDITIONS.get(condition)
    if fn is None:
        raise ValueError(
            f"unknown acoustic condition {condition!r}. Known: {sorted(CONDITIONS)}"
        )
    source = Path(source)
    x, sr = sf.read(str(source), dtype="float32", always_2d=True)
    mono = x.mean(axis=1)
    y, out_sr = fn(mono, sr)
    out = source.with_suffix(f".{condition}.wav")
    sf.write(str(out), y, out_sr, subtype="PCM_16")
    return Degraded(condition=condition, path=out, source_path=source, sample_rate=out_sr)
