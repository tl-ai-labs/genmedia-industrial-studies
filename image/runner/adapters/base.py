"""The adapter contract — the only thing a new provider implements (plan §7).

Adapters do exactly three things: translate our request into the provider's
shape, call the API once, and return bytes plus raw usage. No retries (the
runner owns those), no cost maths (cost.py owns that), no file writing (the
runner owns that).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Asset:
    """A typed input, already frozen into the run folder and hashed."""
    role: str                 # "source" | "mask" | "reference"
    path: Path
    mime: str
    sha256: str


@dataclass
class GenRequest:
    task: str                 # "text_to_image", "image_edit", "styled_tts", ...
    text: str                 # prompt or script — identical for every model
    inputs: list[Asset]       # empty for the pure text-to-X tasks
    params: dict


@dataclass
class GenResult:
    data: bytes               # the image or audio bytes
    mime: str                 # "image/png", "audio/wav"
    provider_version: Optional[str]           # model version string the API echoed back
    usage: dict = field(default_factory=dict)         # raw usage fields, verbatim
    applied_params: dict = field(default_factory=dict)  # what the provider actually honoured
    params_unsupported: list = field(default_factory=list)  # knobs the provider ignored
    request_id: Optional[str] = None


@dataclass
class JudgeResult:
    """Raw output of one judge call, before any schema validation."""
    text: str
    provider_version: Optional[str]
    usage: dict = field(default_factory=dict)
    request_id: Optional[str] = None


class Adapter:
    """Synchronous generation adapter."""
    supports: list[str] = []

    def run(self, req: GenRequest) -> GenResult:
        raise NotImplementedError

    # Optional, for asynchronous types (video). Sync adapters leave these
    # unimplemented and the runner treats the job as complete on return from
    # run(). ~Fifteen lines today; the difference between video slotting into
    # this lifecycle later and video forcing a second runner.
    def submit(self, req: GenRequest) -> str:
        raise NotImplementedError("this adapter is synchronous")

    def poll(self, job_id: str) -> Optional[GenResult]:
        raise NotImplementedError("this adapter is synchronous")


# ---- error taxonomy — adapters translate SDK exceptions into these --------

class AdapterError(Exception):
    status = "provider_error"
    retryable = True


class RateLimited(AdapterError):
    status = "rate_limited"
    retryable = True

    def __init__(self, msg: str = "", retry_after: Optional[float] = None):
        super().__init__(msg)
        self.retry_after = retry_after


class Timeout(AdapterError):
    status = "timeout"
    retryable = True


class SafetyRefusal(AdapterError):
    """No retry — retrying a refusal only costs money. A product fact."""
    status = "refused"
    retryable = False


class ProviderError(AdapterError):
    status = "provider_error"

    def __init__(self, msg: str = "", retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable
