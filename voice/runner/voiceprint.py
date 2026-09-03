"""
Speaker identity: is this the same voice, and are these voices different?

THE ONE PLACE that turns audio into a speaker embedding and two embeddings into
a similarity, for the same reason runner.normalize is the one place that
normalizes text: a second copy drifts, and the day the two disagree a gate
starts failing clips that are fine.

WHAT IT IS FOR. Six scenarios in the bank ask a question no per-clip check can
answer, because the evidence lives BETWEEN clips:

    "the same voice in episode fifty as in episode one"   (vr-drama-01)
    "the same character in English, Hindi and Spanish"    (vr-drama-03, vr-game-06)
    "fifty ad variants that all sound like one brand"     (vr-ads-01)
    "six NPCs that are mutually distinguishable"          (vr-game-04)
    "four characters, each line correctly cast"           (vr-drama-06)

The first four ask for SAMENESS and the last two for DIFFERENCE, which is why
this module exposes both directions rather than one number called "similarity".

WHAT THE NUMBERS ACTUALLY LOOK LIKE HERE, measured 2026-09-03 over the clips
this project has already produced - worth knowing before picking a threshold,
because a cosine of 0.86 sounds high until you see what the floor is:

    same model, same logical voice, different scenarios
        elevenlabs-multilingual-v2   mean 0.976   min 0.952
        gemini-3-1-flash-tts         mean 0.859   min 0.824
    different models, same logical voice
        female_mid_warm  0.777     male_mid_neutral  0.639

So the scale is compressed: "completely different voice" is around 0.64-0.78,
not 0.0. A threshold has to be chosen against THAT floor, not against intuition
about what a cosine means. The workbook asks for >0.90 on the cross-language
scenarios, which ElevenLabs clears comfortably and Gemini does not.

HONESTY RULES, the same ones the rest of the harness follows:
  - Fewer than two clips is UNMEASURED, not a perfect score. One clip cannot
    disagree with itself, and returning 1.0 would claim consistency from
    evidence incapable of showing any.
  - A clip that fails to embed is a recorded failure, never silently dropped
    from the mean - dropping the odd one out is how a set of six voices with
    two identical ones passes a distinctness gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# Loaded once, on first use. The offline test suite never touches audio, so
# importing this module must stay free - the encoder is ~17 MB of torch weights
# and would otherwise be paid for on every `pytest` run.
_ENCODER = None


def _encoder():
    global _ENCODER
    if _ENCODER is None:
        from resemblyzer import VoiceEncoder

        _ENCODER = VoiceEncoder(verbose=False)
    return _ENCODER


def embed(path: Path) -> np.ndarray:
    """A 256-dim speaker embedding for one clip. Raises if the audio is unusable."""
    from resemblyzer import preprocess_wav

    return _encoder().embed_utterance(preprocess_wav(Path(path)))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity, clamped to [-1, 1] against floating-point overshoot."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        raise ValueError("cannot compare a zero-length embedding")
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))


@dataclass(frozen=True)
class PairwiseResult:
    """
    Every pair, so a verdict can be traced to the two clips that produced it.

    `worst_pair` is what a gate reads. For SAMENESS that is the lowest cosine -
    the two takes that least sound like each other; a set is only as consistent
    as its most divergent pair. For DIFFERENCE it is the highest - a cast is
    only as distinguishable as its most confusable pair. Reporting the mean
    instead would let one indistinguishable pair hide behind five good ones.
    """

    labels: tuple[str, ...]
    pairs: tuple[tuple[str, str, float], ...]
    failed: tuple[tuple[str, str], ...] = ()

    @property
    def n(self) -> int:
        return len(self.labels)

    @property
    def values(self) -> list[float]:
        return [v for _, _, v in self.pairs]

    @property
    def mean(self) -> float | None:
        return float(np.mean(self.values)) if self.pairs else None

    @property
    def lowest(self) -> tuple[str, str, float] | None:
        return min(self.pairs, key=lambda p: p[2]) if self.pairs else None

    @property
    def highest(self) -> tuple[str, str, float] | None:
        return max(self.pairs, key=lambda p: p[2]) if self.pairs else None

    @property
    def as_record(self) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "clips": list(self.labels),
            "n_pairs": len(self.pairs),
            "mean_cosine": round(self.mean, 4) if self.mean is not None else None,
            "pairs": [[a, b, round(v, 4)] for a, b, v in self.pairs],
        }
        if self.lowest:
            a, b, v = self.lowest
            rec["lowest"] = {"a": a, "b": b, "cosine": round(v, 4)}
        if self.highest:
            a, b, v = self.highest
            rec["highest"] = {"a": a, "b": b, "cosine": round(v, 4)}
        if self.failed:
            rec["embedding_failures"] = [{"clip": c, "error": e} for c, e in self.failed]
        return rec


def compare(clips: dict[str, Path]) -> PairwiseResult:
    """
    Embed every clip and score every pair.

    `clips` maps a LABEL to a path - the label is what appears in the evidence,
    so it should say something a reader can act on ("pass 2", "hi-IN",
    "quartermaster"), not a file name.

    A clip that will not embed is recorded in `failed` and excluded from the
    pairs. It is never silently skipped: a distinctness gate over six voices
    that quietly became four has not measured what it claims.
    """
    embeddings: dict[str, np.ndarray] = {}
    failed: list[tuple[str, str]] = []
    for label, path in clips.items():
        try:
            embeddings[label] = embed(path)
        except Exception as exc:  # noqa: BLE001 - any unusable clip is one state
            failed.append((label, f"{type(exc).__name__}: {exc}"))

    labels = tuple(sorted(embeddings))
    pairs = tuple(
        (a, b, cosine(embeddings[a], embeddings[b]))
        for i, a in enumerate(labels)
        for b in labels[i + 1 :]
    )
    return PairwiseResult(labels=labels, pairs=pairs, failed=tuple(failed))


@dataclass
class IdentityVerdict:
    """One gate's outcome, with the state that is neither pass nor fail."""

    gate: str
    passed: bool
    measured: bool
    detail: str
    result: PairwiseResult | None = None

    @property
    def as_record(self) -> dict[str, Any]:
        rec = {"gate": self.gate, "passed": self.passed,
               "measured": self.measured, "detail": self.detail}
        if self.result is not None:
            rec["pairwise"] = self.result.as_record
        return rec


def holds_identity(clips: dict[str, Path], min_cosine: float) -> IdentityVerdict:
    """
    SAMENESS. Every pair must sit at or above `min_cosine`.

    Read on the LOWEST pair, not the mean: a narrator who holds the voice for
    nine episodes and loses it in the tenth has not held the voice.
    """
    if len(clips) < 2:
        return IdentityVerdict(
            "speaker_consistency", True, False,
            f"not evaluated - {len(clips)} clip(s); identity needs at least two to "
            f"compare, and one clip cannot disagree with itself",
        )
    r = compare(clips)
    if not r.pairs:
        return IdentityVerdict(
            "speaker_consistency", True, False,
            f"not evaluated - no clip embedded successfully ({len(r.failed)} failed)", r)
    a, b, v = r.lowest
    return IdentityVerdict(
        "speaker_consistency", v >= min_cosine, True,
        f"lowest pair {a} vs {b} at {v:.4f} against a floor of {min_cosine} "
        f"(mean {r.mean:.4f} over {len(r.pairs)} pairs)", r)


def voices_are_distinct(clips: dict[str, Path], max_cosine: float) -> IdentityVerdict:
    """
    DIFFERENCE. Every pair must sit at or below `max_cosine`.

    Read on the HIGHEST pair. A cast of six with one indistinguishable pair is
    a cast of five, and the mean would hide it.
    """
    if len(clips) < 2:
        return IdentityVerdict(
            "speaker_distinct", True, False,
            f"not evaluated - {len(clips)} clip(s); distinctness needs at least two",
        )
    r = compare(clips)
    if not r.pairs:
        return IdentityVerdict(
            "speaker_distinct", True, False,
            f"not evaluated - no clip embedded successfully ({len(r.failed)} failed)", r)
    a, b, v = r.highest
    return IdentityVerdict(
        "speaker_distinct", v <= max_cosine, True,
        f"closest pair {a} vs {b} at {v:.4f} against a ceiling of {max_cosine} "
        f"(mean {r.mean:.4f} over {len(r.pairs)} pairs)", r)
