"""
Model registry - configs/models.yaml into typed objects (plan v1.2 section 07).

This module is the ONLY thing that reads models.yaml. Adapters receive a
resolved ModelSpec and never re-read config; the runner asks this module
which models exist, what tasks they support, and whether their credentials
are present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PRICE_UNITS = {"tokens", "per_1k_chars", "per_1m_chars", "per_minute", "per_image"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    unit: str
    source: str
    as_of: str
    # tokens
    usd_in_per_1m: float | None = None
    usd_out_per_1m: float | None = None
    usd_audio_in_per_1m: float | None = None
    usd_audio_out_per_1m: float | None = None
    # per_1k_chars / per_minute / per_image
    usd: float | None = None
    # per_1m_chars
    usd_per_1m: float | None = None
    # Declared assumptions, used ONLY when a token-billed provider returns no
    # usage object. They live in config rather than in cost.py so the guess is
    # visible to a reader of models.yaml, and every cost derived from them is
    # labelled usage_exact=false and carries the assumption in its `basis`.
    est_chars_per_input_token: float | None = None
    est_audio_tokens_per_second: float | None = None

    @property
    def as_record(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class Limits:
    max_concurrency: int = 2
    rpm: int | None = None


@dataclass(frozen=True)
class ModelSpec:
    id: str
    modality: str
    enabled: bool
    adapter: str
    provider: str
    provider_model: str
    auth_env: str
    supports: tuple[str, ...]
    limits: Limits
    price: Price
    params: dict[str, Any] = field(default_factory=dict)
    voice_map: dict[str, str] = field(default_factory=dict)
    region: str | None = None
    disabled_reason: str = ""

    def supports_task(self, task: str) -> bool:
        return task in self.supports

    @property
    def has_credential(self) -> bool:
        # A LOCAL service declares no auth_env. "Needs no credential" is a
        # different state from "credential missing", and preflight must not
        # hard-stop a run for a key that was never required.
        if not self.auth_env:
            return True
        return bool(os.environ.get(self.auth_env))

    def resolve_voice(self, logical_voice: str | None) -> tuple[str | None, str | None]:
        """
        Map the scenario's LOGICAL voice ("female_mid_warm") to this provider's
        own voice id. Returns (provider_voice_id, logical_name).

        Voices are not comparable across providers the way sample rates are.
        voice_map pins one deliberate, human-chosen voice per provider; the
        pair returned here is recorded on every telemetry row, written into
        the manifest, and footnoted in the report as a DECLARED difference
        (plan section 07, voice caveat).
        """
        if not logical_voice:
            return None, None
        if logical_voice not in self.voice_map:
            raise ConfigError(
                f"model '{self.id}': scenario asks for voice '{logical_voice}' but the "
                f"model's voice_map declares only {sorted(self.voice_map)}. Add a "
                f"deliberate mapping - the runner will not pick a voice for you, "
                f"because an arbitrary voice is an undeclared difference between arms."
            )
        return self.voice_map[logical_voice], logical_voice


@dataclass(frozen=True)
class ServiceSpec:
    """A non-generating service: the judge, or the ASR used for the WER check."""

    role: str
    adapter: str
    provider: str
    provider_model: str
    auth_env: str
    price: Price
    temperature: float = 0.0
    region: str | None = None

    @property
    def has_credential(self) -> bool:
        # A LOCAL service declares no auth_env. "Needs no credential" is a
        # different state from "credential missing", and preflight must not
        # hard-stop a run for a key that was never required.
        if not self.auth_env:
            return True
        return bool(os.environ.get(self.auth_env))


@dataclass(frozen=True)
class MosSpec:
    predictor: str
    model_path: str


@dataclass(frozen=True)
class Registry:
    models: tuple[ModelSpec, ...]
    judges: dict[str, ServiceSpec]
    asr: ServiceSpec | None
    mos: MosSpec
    config_path: str

    def for_modality(self, modality: str, enabled_only: bool = True) -> list[ModelSpec]:
        return [
            m
            for m in self.models
            if m.modality == modality and (m.enabled or not enabled_only)
        ]


def _price(raw: dict | None, where: str) -> Price:
    if not raw:
        raise ConfigError(f"{where}: no `price` block. A model with no price cannot be costed.")
    unit = raw.get("unit")
    if unit not in PRICE_UNITS:
        raise ConfigError(
            f"{where}: price.unit={unit!r} is not one of {sorted(PRICE_UNITS)}"
        )
    for required in ("source", "as_of"):
        if not raw.get(required):
            raise ConfigError(
                f"{where}: price is missing `{required}`. Every rate carries where it "
                f"came from and when it was read - a rate with no provenance cannot be "
                f"re-checked, and provider rates move."
            )
    known = {
        "usd_in_per_1m",
        "usd_out_per_1m",
        "usd_audio_in_per_1m",
        "usd_audio_out_per_1m",
        "usd",
        "usd_per_1m",
        "est_chars_per_input_token",
        "est_audio_tokens_per_second",
    }
    price = Price(
        unit=unit,
        source=str(raw["source"]),
        as_of=str(raw["as_of"]),
        **{k: float(raw[k]) for k in known if raw.get(k) is not None},
    )
    # Fail at load rather than at cost time: a price block that names a unit
    # but carries no rate for it would silently cost every call at zero.
    if unit == "tokens" and price.usd_in_per_1m is None:
        raise ConfigError(f"{where}: price.unit=tokens needs at least `usd_in_per_1m`")
    if unit in ("per_1k_chars", "per_minute", "per_image") and price.usd is None:
        raise ConfigError(f"{where}: price.unit={unit} needs `usd`")
    if unit == "per_1m_chars" and price.usd_per_1m is None:
        raise ConfigError(f"{where}: price.unit=per_1m_chars needs `usd_per_1m`")
    return price


def _model(raw: dict, modality: str, path: Path) -> ModelSpec:
    mid = raw.get("id")
    if not mid:
        raise ConfigError(f"{path}: a {modality} model block has no `id`")
    where = f"{path} [{mid}]"
    for required in ("adapter", "provider_model", "auth_env", "supports"):
        if not raw.get(required):
            raise ConfigError(f"{where}: missing required field `{required}`")
    lim = raw.get("limits") or {}
    return ModelSpec(
        id=str(mid),
        modality=modality,
        enabled=bool(raw.get("enabled", False)),
        adapter=str(raw["adapter"]),
        # Default the lane key to the adapter name rather than the model id:
        # quota is per upstream vendor, so two models on one vendor must share
        # a semaphore or the per-provider limit means nothing.
        provider=str(raw.get("provider") or raw["adapter"]),
        provider_model=str(raw["provider_model"]),
        auth_env=str(raw["auth_env"]),
        supports=tuple(raw["supports"]),
        limits=Limits(
            max_concurrency=int(lim.get("max_concurrency", 2)),
            rpm=int(lim["rpm"]) if lim.get("rpm") else None,
        ),
        price=_price(raw.get("price"), where),
        params=dict(raw.get("params") or {}),
        voice_map=dict(raw.get("voice_map") or {}),
        region=raw.get("region"),
        disabled_reason=str(raw.get("disabled_reason") or ""),
    )


def _service(raw: dict, role: str, path: Path) -> ServiceSpec:
    where = f"{path} [{role}]"
    for required in ("adapter", "provider_model"):
        if not raw.get(required):
            raise ConfigError(f"{where}: missing required field `{required}`")
    # `auth_env` is OPTIONAL, and its absence is a claim: this service runs
    # locally and needs no credential. A local ASR has no key to name, and
    # demanding one would make the only vendor-neutral instrument we have
    # unconfigurable. Anything reached over a network still declares it -
    # preflight hard-stops on a NAMED variable that is unset, which is the
    # case this check exists for.
    return ServiceSpec(
        role=role,
        adapter=str(raw["adapter"]),
        provider=str(raw.get("provider") or raw["adapter"]),
        provider_model=str(raw["provider_model"]),
        auth_env=str(raw.get("auth_env") or ""),
        price=_price(raw.get("price"), where),
        temperature=float(raw.get("temperature", 0.0)),
        region=raw.get("region"),
    )


def load_registry(configs_dir: Path) -> Registry:
    path = Path(configs_dir) / "models.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    models: list[ModelSpec] = []
    for modality in ("voice", "image"):
        for raw in doc.get(modality) or []:
            models.append(_model(raw, modality, path))

    ids = [m.id for m in models]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ConfigError(f"{path}: duplicate model ids {sorted(dupes)}")

    judges = {
        role: _service(raw, f"judge:{role}", path)
        for role, raw in (doc.get("judge") or {}).items()
    }
    asr_raw = doc.get("asr")
    asr = _service(asr_raw, "asr", path) if asr_raw else None
    mos_raw = doc.get("mos") or {}

    return Registry(
        models=tuple(models),
        judges=judges,
        asr=asr,
        mos=MosSpec(
            predictor=str(mos_raw.get("predictor", "signal")),
            model_path=str(mos_raw.get("model_path", "")),
        ),
        config_path=str(path),
    )


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    ready: tuple[str, ...]
    missing_credentials: tuple[tuple[str, str], ...]
    disabled: tuple[tuple[str, str], ...]

    def render(self) -> str:
        lines = []
        for mid in self.ready:
            lines.append(f"  ok       {mid}")
        for mid, env in self.missing_credentials:
            lines.append(f"  MISSING  {mid} - ${env} is not set")
        for mid, reason in self.disabled:
            lines.append(f"  disabled {mid}" + (f" - {reason}" if reason else ""))
        return "\n".join(lines)


def preflight(registry: Registry, modality: str, needs: list[ServiceSpec]) -> PreflightResult:
    """
    Every enabled model's credential is checked at start-up, not at first call
    (plan section 08). A missing key is a hard stop BEFORE any spend, because
    discovering it on call 14 of 40 means paying for 13 calls of a comparison
    that can never complete.
    """
    ready: list[str] = []
    missing: list[tuple[str, str]] = []
    disabled: list[tuple[str, str]] = []
    for m in registry.for_modality(modality, enabled_only=False):
        if not m.enabled:
            disabled.append((m.id, m.disabled_reason))
        elif m.has_credential:
            ready.append(m.id)
        else:
            missing.append((m.id, m.auth_env))
    for svc in needs:
        if svc is None:
            continue
        if svc.has_credential:
            ready.append(f"{svc.role} ({svc.provider_model})")
        else:
            missing.append((f"{svc.role} ({svc.provider_model})", svc.auth_env))
    return PreflightResult(
        ok=not missing and bool(ready),
        ready=tuple(ready),
        missing_credentials=tuple(missing),
        disabled=tuple(disabled),
    )
