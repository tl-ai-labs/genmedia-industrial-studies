"""
The adapter contract - the only thing a new provider implements
(plan v1.2 section 07).

Adapters do exactly three things: translate our request into the provider's
shape, call the API once, and return bytes plus raw usage.

They do NOT retry (the runner owns retries), do NOT do cost maths (cost.py
owns that), and do NOT write files (the runner owns that). That is what keeps
"2 models" and "6 models" the same amount of work, and it is what lets checks,
judge, scoring and report stay ignorant that providers exist at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..cost import Usage


# --------------------------------------------------------------------------
# Error taxonomy. The runner branches on these to decide retry vs stop
# (plan section 18), so an adapter must map its provider's failures onto them
# rather than letting a raw SDK exception escape.
# --------------------------------------------------------------------------

class AdapterError(RuntimeError):
    """Base. `status` is what lands in the telemetry row."""

    status = "provider_error"
    retryable = True


class RateLimited(AdapterError):
    status = "rate_limited"
    retryable = True

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Timeout(AdapterError):
    status = "timeout"
    retryable = True


class ProviderError(AdapterError):
    status = "provider_error"
    retryable = True


class AuthError(AdapterError):
    """A bad or missing credential. Retrying cannot fix it."""

    status = "auth_error"
    retryable = False


class SafetyRefusal(AdapterError):
    """
    The provider declined. NOT retried - retrying a refusal only costs money,
    and a refusal is a product fact reported in its own column, never a
    quality score (plan section 18).
    """

    status = "refused"
    retryable = False


@dataclass(frozen=True)
class Asset:
    """A typed input, already frozen into the run folder and hashed."""

    role: str  # "source" | "mask" | "reference"
    path: Path
    mime: str
    sha256: str


@dataclass(frozen=True)
class GenRequest:
    task: str
    # The prompt or script - identical for every model, by value.
    text: str
    inputs: tuple[Asset, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    # Provider-specific voice id already resolved from the model's voice_map,
    # plus the logical name it came from. Resolved by the runner, not the
    # adapter, so the mapping is recorded in one place.
    voice_id: str | None = None
    voice_logical: str | None = None
    language: str | None = None
    style: str | None = None
    timeout_s: float = 180.0
    # Ask the adapter to STREAM and time the first chunk carrying audio.
    # Opt-in, because streaming changes how a response is assembled and only
    # one scenario needs the number - a latency claim should not silently
    # alter how every other clip is produced.
    measure_ttfa: bool = False


@dataclass(frozen=True)
class GenResult:
    data: bytes
    mime: str
    provider_version: str | None = None
    usage: Usage = field(default_factory=Usage)
    # What the provider actually honoured. The runner diffs this against the
    # requested params to produce `params_unsupported` - the honest answer to
    # "was this really the same input?" (plan section 10).
    applied_params: dict[str, Any] = field(default_factory=dict)
    provider_request_id: str | None = None
    # Milliseconds to the first chunk carrying audio. None when the call was
    # not streamed. It is NOT latency_ms and must never overwrite it: whole-
    # call latency is a different quantity by an order of magnitude, and
    # every run before 2026-09-03 recorded only the latter.
    ttfa_ms: int | None = None


@runtime_checkable
class Adapter(Protocol):
    """
    Synchronous adapters implement run(). The optional submit()/poll() pair
    exists for asynchronous modalities (video): the runner treats a sync
    adapter's job as complete on return from run(). Fifteen lines today, and
    the difference between video slotting into this lifecycle later and video
    forcing a second runner (plan section 04).
    """

    id: str
    supports: tuple[str, ...]
    ext: str

    def run(self, req: GenRequest) -> GenResult: ...


class BaseAdapter:
    """Shared plumbing. Concrete adapters subclass and implement run()."""

    ext = "bin"

    def __init__(self, spec) -> None:  # spec: models.ModelSpec
        self.spec = spec
        self.id = spec.id
        self.supports = tuple(spec.supports)

    def supports_task(self, task: str) -> bool:
        return task in self.supports

    def run(self, req: GenRequest) -> GenResult:  # pragma: no cover - interface
        raise NotImplementedError

    # Optional async pair. Left unimplemented on purpose: a sync adapter that
    # inherited a fake submit()/poll() would look asynchronous to the runner.
    def submit(self, req: GenRequest) -> str:  # pragma: no cover - interface
        raise NotImplementedError(f"adapter '{self.id}' is synchronous")

    def poll(self, job_id: str) -> GenResult | None:  # pragma: no cover - interface
        raise NotImplementedError(f"adapter '{self.id}' is synchronous")


def params_unsupported(requested: dict[str, Any], applied: dict[str, Any]) -> list[str]:
    """
    Every knob the provider ignored. A key is "unsupported" when it was asked
    for and the adapter did not report honouring it - reported as a fact, not
    inferred from the output.
    """
    return sorted(k for k in requested if k not in applied)
