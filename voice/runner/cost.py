"""
Cost - provider usage x published price table, in integer micro-USD
(plan v1.2 sections 10 and 15).

TWO RULES THIS FILE EXISTS TO ENFORCE.

1. Cost is derived from returned usage fields, never guessed from prompt
   length. Where a provider genuinely does not report usage, the number is
   computed from the exact request-side billing quantity and LABELLED, so
   the label travels with it into the report.

2. Integer micro-USD, never float dollars. Floats accumulate rounding error
   across a hundred scenarios and a total that does not reconcile with the
   provider console is a total nobody trusts.

BILLING DIFFERS PER PROVIDER and this file is where that is handled honestly:
the same 612-character script bills as tokens on gpt-4o-mini-tts, as
characters on tts-1, as characters on ElevenLabs, and as tokens on Gemini
TTS. Four units, four arithmetic paths, one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Price

MICRO = 1_000_000


@dataclass(frozen=True)
class Usage:
    """
    What a call actually consumed. Adapters fill in what their provider
    exposes and set `reported` accordingly; everything else stays None.
    `raw` keeps the provider's own usage object verbatim so a disputed
    invoice can be reconciled against the response rather than our reading
    of it.
    """

    # True only when these numbers came back FROM the provider's response.
    reported: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    audio_out_tokens: int | None = None
    audio_in_tokens: int | None = None
    # Request-side billing quantities. `characters` is exact for a
    # character-billed API: it is the string we sent, which is definitionally
    # what such a provider bills.
    characters: int | None = None
    audio_seconds: float | None = None
    images: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Cost:
    micro_usd: int
    basis: str
    price_as_of: str
    price_source: str
    # The plan's two-value field (section 10). "api_reported" means the
    # provider's response carried the usage numbers.
    usage_source: str
    # Sibling provenance: is the billed quantity EXACT, whatever its source?
    # A character-billed call is exact without being api_reported - we know
    # precisely how many characters we sent. Without this, the report would
    # brand an exact cost "est" and a reader would discount a number that
    # needs no discounting. The report badges on this, not on usage_source.
    usage_exact: bool

    @property
    def usd(self) -> float:
        return self.micro_usd / MICRO

    @property
    def as_record(self) -> dict[str, Any]:
        return {
            "micro_usd": self.micro_usd,
            "basis": self.basis,
            "price_as_of": self.price_as_of,
            "price_source": self.price_source,
            "usage_source": self.usage_source,
            "usage_exact": self.usage_exact,
        }


class CostError(ValueError):
    pass


def compute_cost(usage: Usage, price: Price, label: str = "") -> Cost:
    """usage x price -> integer micro-USD, with the basis recorded."""
    unit = price.unit
    usage_source = "api_reported" if usage.reported else "estimated"

    if unit == "tokens":
        if not usage.reported:
            # The provider bills tokens but returned none (OpenAI's speech
            # endpoint does exactly this). Rather than invent a number here,
            # price from the assumptions DECLARED in models.yaml and label the
            # result: usage_source=estimated, usage_exact=false, and the basis
            # string names both assumptions so the report can show its working.
            # The plan anticipates this and says the runner replaces it with
            # the provider's own usage the moment the provider reports any.
            return _cost_from_declared_estimate(usage, price, label)
        usd = 0.0
        parts = []
        if usage.input_tokens and price.usd_in_per_1m is not None:
            usd += usage.input_tokens / 1e6 * price.usd_in_per_1m
            parts.append(f"in={usage.input_tokens}")
        if usage.audio_in_tokens and price.usd_audio_in_per_1m is not None:
            usd += usage.audio_in_tokens / 1e6 * price.usd_audio_in_per_1m
            parts.append(f"audio_in={usage.audio_in_tokens}")
        if usage.audio_out_tokens and price.usd_audio_out_per_1m is not None:
            usd += usage.audio_out_tokens / 1e6 * price.usd_audio_out_per_1m
            parts.append(f"audio_out={usage.audio_out_tokens}")
        if usage.output_tokens and price.usd_out_per_1m is not None:
            usd += usage.output_tokens / 1e6 * price.usd_out_per_1m
            parts.append(f"out={usage.output_tokens}")
        return Cost(
            micro_usd=round(usd * MICRO),
            basis="tokens@" + ",".join(parts) if parts else "tokens@none",
            price_as_of=price.as_of,
            price_source=price.source,
            usage_source=usage_source,
            usage_exact=True,
        )

    if unit == "per_1k_chars":
        if usage.characters is None:
            raise CostError(f"{label}: price.unit=per_1k_chars but no character count was recorded")
        usd = usage.characters / 1000.0 * float(price.usd)
        return Cost(
            micro_usd=round(usd * MICRO),
            basis=f"per_1k_chars@chars_sent={usage.characters}",
            price_as_of=price.as_of,
            price_source=price.source,
            usage_source=usage_source,
            usage_exact=True,
        )

    if unit == "per_1m_chars":
        if usage.characters is None:
            raise CostError(f"{label}: price.unit=per_1m_chars but no character count was recorded")
        usd = usage.characters / 1e6 * float(price.usd_per_1m)
        return Cost(
            micro_usd=round(usd * MICRO),
            basis=f"per_1m_chars@chars_sent={usage.characters}",
            price_as_of=price.as_of,
            price_source=price.source,
            usage_source=usage_source,
            usage_exact=True,
        )

    if unit == "per_minute":
        if usage.audio_seconds is None:
            raise CostError(f"{label}: price.unit=per_minute but no audio duration was recorded")
        usd = usage.audio_seconds / 60.0 * float(price.usd)
        return Cost(
            micro_usd=round(usd * MICRO),
            basis=f"per_minute@seconds={usage.audio_seconds:.3f}",
            price_as_of=price.as_of,
            price_source=price.source,
            usage_source=usage_source,
            # Duration is measured off the decoded file, not requested - exact.
            usage_exact=True,
        )

    if unit == "per_image":
        n = usage.images if usage.images is not None else 1
        usd = n * float(price.usd)
        return Cost(
            micro_usd=round(usd * MICRO),
            basis=f"per_image@n={n}",
            price_as_of=price.as_of,
            price_source=price.source,
            usage_source=usage_source,
            usage_exact=True,
        )

    raise CostError(f"{label}: unknown price unit {unit!r}")


def _cost_from_declared_estimate(usage: Usage, price: Price, label: str) -> Cost:
    """
    Token pricing for a provider that returned no usage. Both assumptions must
    be declared in the price block - a token-billed model with no declared
    estimation is a config error, not a licence to guess in code.
    """
    if price.est_chars_per_input_token is None or price.est_audio_tokens_per_second is None:
        raise CostError(
            f"{label}: price.unit=tokens, the provider returned no usage object, and the "
            f"price block declares no `est_chars_per_input_token` / "
            f"`est_audio_tokens_per_second`. Add both to models.yaml (they will be "
            f"labelled as estimates wherever the cost appears) or the call cannot be "
            f"priced without inventing a number."
        )
    if usage.characters is None or usage.audio_seconds is None:
        raise CostError(
            f"{label}: estimating token cost needs both the characters sent and the "
            f"measured audio duration; got characters={usage.characters}, "
            f"audio_seconds={usage.audio_seconds}"
        )
    in_tok = usage.characters / price.est_chars_per_input_token
    out_tok = usage.audio_seconds * price.est_audio_tokens_per_second
    usd = in_tok / 1e6 * (price.usd_in_per_1m or 0.0)
    usd += out_tok / 1e6 * (price.usd_audio_out_per_1m or 0.0)
    basis = (
        f"tokens@estimated:in={in_tok:.0f}(chars={usage.characters}"
        f"/{price.est_chars_per_input_token:g}),"
        f"audio_out={out_tok:.0f}({usage.audio_seconds:.2f}s"
        f"x{price.est_audio_tokens_per_second:g}/s)"
    )
    return Cost(
        micro_usd=round(usd * MICRO),
        basis=basis,
        price_as_of=price.as_of,
        price_source=price.source,
        usage_source="estimated",
        usage_exact=False,
    )


def estimate_scenario_cost(chars: int, price: Price, assumed_seconds: float) -> int:
    """
    Pre-flight only (plan section 08: print the estimated cost of the whole
    run and stop if it exceeds --budget). This IS a guess - it runs before
    any call, so no real usage exists yet - and its output is never written
    to telemetry or shown as a measured cost. It exists to stop a run, not to
    describe one.
    """
    if price.unit == "per_1k_chars":
        return round(chars / 1000.0 * float(price.usd) * MICRO)
    if price.unit == "per_1m_chars":
        return round(chars / 1e6 * float(price.usd_per_1m) * MICRO)
    if price.unit == "per_minute":
        return round(assumed_seconds / 60.0 * float(price.usd) * MICRO)
    if price.unit == "tokens":
        # ~4 chars per text token; TTS audio output is commonly ~25 audio
        # tokens per second. Both assumptions are ours and are why this
        # number never leaves the pre-flight banner.
        in_tok = chars / 4.0
        out_tok = assumed_seconds * 25.0
        usd = in_tok / 1e6 * (price.usd_in_per_1m or 0.0)
        usd += out_tok / 1e6 * (price.usd_audio_out_per_1m or 0.0)
        return round(usd * MICRO)
    if price.unit == "per_image":
        return round(float(price.usd) * MICRO)
    return 0


def fmt_usd(micro: int) -> str:
    return f"${micro / MICRO:,.4f}"
