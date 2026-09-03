"""
Objective audio quality - free, local, CPU, deterministic (plan v1.2 section 13).

TWO PREDICTORS, ONE INTERFACE, AND A LABEL THAT NEVER LIES.

  DnsmosOnnxPredictor  a real DNSMOS/NISQA-style ONNX model. Emits a genuine
                       MOS on the 1..5 scale. is_mos = True.
  SignalQualityPredictor  signal metrics only - noise floor, spectral
                       flatness, clipping, DC offset, bandwidth - combined
                       onto the same 1..5 axis so the rubric's scale applies
                       unchanged. is_mos = FALSE, and every surface that
                       shows the number says so.

Why ship the second one at all: a DNSMOS ONNX has to be downloaded, and until
someone does that the audio_quality criterion would have no code half at all.
A labelled signal metric is a real measurement of a real property of the file.
What it is not is a perceptual opinion, and calling it a MOS would be exactly
the kind of quiet fabrication this project refuses. `is_mos` is how the
distinction survives all the way to the report footnotes.

Adding a third predictor is one class and one line in build_predictor().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np


@dataclass(frozen=True)
class QualityResult:
    """`value` is always on the 1..5 axis so one rubric scale fits both."""

    value: float
    predictor: str
    is_mos: bool
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def as_record(self) -> dict[str, Any]:
        return {
            "quality_1_5": round(self.value, 4),
            "predictor": self.predictor,
            "is_mos": self.is_mos,
            "metrics": {k: round(v, 6) for k, v in self.metrics.items()},
        }


class QualityPredictor(Protocol):
    name: str
    is_mos: bool

    def predict(self, samples: np.ndarray, sample_rate: int) -> QualityResult: ...


def _mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim > 1:
        return samples.mean(axis=1)
    return samples


class SignalQualityPredictor:
    """
    Objective signal metrics, combined onto the 1..5 axis.

    NOT a MOS. It cannot hear. It measures five properties that a listener
    would notice if they were bad, and penalises each:

      noise_floor_db   level of the quietest 10% of frames. A hissy recording
                       has a high floor.
      clip_ratio       fraction of samples at or near full scale.
      dc_offset        a non-zero mean, which reads as a thump and wastes
                       headroom.
      spectral_flatness  1.0 is white noise, near 0 is tonal. Speech sits low;
                       a high value on a speech clip means noise or artefacts.
      bandwidth_hz     where 95% of spectral energy is contained. A muffled
                       or heavily-compressed clip loses the top end.
    """

    name = "signal-metrics-v1"
    is_mos = False

    def predict(self, samples: np.ndarray, sample_rate: int) -> QualityResult:
        x = _mono(np.asarray(samples, dtype=np.float64))
        if x.size == 0:
            return QualityResult(1.0, self.name, False, {"empty": 1.0})

        peak = float(np.max(np.abs(x))) or 1e-12
        rms = float(np.sqrt(np.mean(x**2)))

        # Frame the signal at 20 ms to separate speech from silence.
        frame = max(1, int(0.02 * sample_rate))
        n_frames = x.size // frame
        if n_frames >= 4:
            frames = x[: n_frames * frame].reshape(n_frames, frame)
            frame_rms = np.sqrt(np.mean(frames**2, axis=1)) + 1e-12
            floor = float(np.percentile(frame_rms, 10))
            speech = float(np.percentile(frame_rms, 90))
        else:
            floor, speech = rms + 1e-12, rms + 1e-12

        noise_floor_db = 20.0 * np.log10(floor / peak)
        snr_db = 20.0 * np.log10(speech / max(floor, 1e-12))
        clip_ratio = float(np.mean(np.abs(x) >= 0.999))
        dc_offset = abs(float(np.mean(x)))

        spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) + 1e-12
        geo = float(np.exp(np.mean(np.log(spec))))
        arith = float(np.mean(spec))
        flatness = geo / arith if arith > 0 else 1.0

        cumulative = np.cumsum(spec**2)
        total = cumulative[-1] if cumulative[-1] > 0 else 1.0
        idx = int(np.searchsorted(cumulative, 0.95 * total))
        freqs = np.fft.rfftfreq(x.size, 1.0 / sample_rate)
        bandwidth = float(freqs[min(idx, len(freqs) - 1)])

        # Start at the top of the axis and subtract for each defect. The
        # coefficients are a deliberate, documented weighting, not a fit to
        # any dataset - which is precisely why this is not called a MOS.
        score = 5.0
        score -= max(0.0, (25.0 - snr_db) / 25.0) * 1.6          # noisy
        score -= min(1.0, clip_ratio * 200.0) * 1.2               # clipped
        score -= min(1.0, dc_offset * 20.0) * 0.4                 # dc thump
        score -= max(0.0, (flatness - 0.10) / 0.40) * 0.8         # noise-like
        score -= max(0.0, (4000.0 - bandwidth) / 4000.0) * 1.0    # muffled
        score = float(np.clip(score, 1.0, 5.0))

        return QualityResult(
            value=score,
            predictor=self.name,
            is_mos=False,
            metrics={
                "snr_db": snr_db,
                "noise_floor_db": noise_floor_db,
                "clip_ratio": clip_ratio,
                "dc_offset": dc_offset,
                "spectral_flatness": flatness,
                "bandwidth_hz": bandwidth,
                "peak": peak,
                "rms": rms,
            },
        )


class DnsmosOnnxPredictor:
    """
    A real DNSMOS/NISQA-style ONNX predictor. Emits a genuine MOS.

    The model file is NOT vendored - drop it at the path configured under
    `mos.model_path` in models.yaml. Absent, build_predictor() falls back to
    the signal predictor and SAYS SO rather than silently scoring zero.
    """

    name = "dnsmos-onnx"
    is_mos = True
    # DNSMOS P.808 expects 16 kHz mono in ~9 s windows.
    TARGET_RATE = 16000
    WINDOW_S = 9.0

    def __init__(self, model_path: Path) -> None:
        import onnxruntime  # imported here so the dependency is optional

        self.model_path = Path(model_path)
        self._session = onnxruntime.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    def predict(self, samples: np.ndarray, sample_rate: int) -> QualityResult:
        x = _mono(np.asarray(samples, dtype=np.float32))
        if sample_rate != self.TARGET_RATE:
            n = int(round(x.size * self.TARGET_RATE / sample_rate))
            x = np.interp(
                np.linspace(0, x.size - 1, n, dtype=np.float64),
                np.arange(x.size),
                x,
            ).astype(np.float32)

        win = int(self.WINDOW_S * self.TARGET_RATE)
        if x.size < win:
            x = np.pad(x, (0, win - x.size))

        scores: list[float] = []
        for start in range(0, x.size - win + 1, win):
            chunk = x[start : start + win][np.newaxis, :]
            out = self._session.run(None, {self._input_name: chunk})
            scores.append(float(np.asarray(out[0]).ravel()[-1]))
        if not scores:
            raise RuntimeError("DNSMOS produced no windows")

        value = float(np.clip(np.mean(scores), 1.0, 5.0))
        return QualityResult(
            value=value,
            predictor=f"{self.name}:{self.model_path.name}",
            is_mos=True,
            metrics={"windows": float(len(scores)), "mos_raw": value},
        )


def build_predictor(spec, project_root: Path) -> tuple[QualityPredictor, str | None]:
    """
    Returns (predictor, fallback_reason). `fallback_reason` is non-None when
    the configured predictor could not be built - the run continues with the
    signal predictor and the reason is recorded in the manifest and printed
    in the report footnotes, so a reader is never left to assume DNSMOS ran.
    """
    if spec.predictor == "signal":
        return SignalQualityPredictor(), None
    if spec.predictor == "dnsmos":
        path = Path(spec.model_path)
        if not path.is_absolute():
            path = Path(project_root) / path
        if not path.exists():
            return (
                SignalQualityPredictor(),
                f"mos.predictor=dnsmos but no model at {path} - fell back to signal metrics",
            )
        try:
            return DnsmosOnnxPredictor(path), None
        except Exception as exc:  # noqa: BLE001
            return (
                SignalQualityPredictor(),
                f"mos.predictor=dnsmos failed to load ({type(exc).__name__}: {exc}) "
                f"- fell back to signal metrics",
            )
    return (
        SignalQualityPredictor(),
        f"unknown mos.predictor={spec.predictor!r} - fell back to signal metrics",
    )
