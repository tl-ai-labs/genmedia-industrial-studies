"""
Adapter registry.

Adding a provider is one entry here plus one module. Imports are LAZY: a
provider SDK you have not installed must not break loading the ones you have,
and a voice-only run must never import an image provider's SDK.
"""

from __future__ import annotations

from typing import Callable

from .base import (  # re-exported so callers import from one place
    Adapter,
    AdapterError,
    Asset,
    AuthError,
    BaseAdapter,
    GenRequest,
    GenResult,
    ProviderError,
    RateLimited,
    SafetyRefusal,
    Timeout,
    params_unsupported,
)

__all__ = [
    "Adapter",
    "AdapterError",
    "Asset",
    "AuthError",
    "BaseAdapter",
    "GenRequest",
    "GenResult",
    "ProviderError",
    "RateLimited",
    "SafetyRefusal",
    "Timeout",
    "build_adapter",
    "known_adapters",
    "params_unsupported",
]


def _openai_tts():
    from .openai_tts import OpenAITtsAdapter

    return OpenAITtsAdapter


def _elevenlabs_tts():
    from .elevenlabs_tts import ElevenLabsTtsAdapter

    return ElevenLabsTtsAdapter


def _gemini_tts():
    from .gemini_tts import GeminiTtsAdapter

    return GeminiTtsAdapter


# name in models.yaml -> loader. The image lane adds its entries here and
# changes nothing else.
_REGISTRY: dict[str, Callable[[], type]] = {
    "openai_tts": _openai_tts,
    "elevenlabs_tts": _elevenlabs_tts,
    "gemini_tts": _gemini_tts,
}


def known_adapters() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build_adapter(spec):
    """spec: models.ModelSpec -> a constructed adapter."""
    loader = _REGISTRY.get(spec.adapter)
    if loader is None:
        raise ProviderError(
            f"model '{spec.id}': no adapter registered for '{spec.adapter}'. "
            f"Known: {list(known_adapters())}. Add a module in runner/adapters/ "
            f"and one line in _REGISTRY."
        )
    return loader()(spec)
