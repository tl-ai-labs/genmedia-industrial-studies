"""Adapter registry — lazy import by name, so adding a provider is one file.

`adapter: veo_video` in models.yaml resolves to the first module named
veo_video.py found in: runner/adapters/ (shared, e.g. the judge), then the
per-modality package runner/video/. Config files and old run manifests keep
their short adapter names across the folder split.
The module's `build(model_cfg, timeout_s)` returns an instance. Nothing else
in the system imports a provider SDK.
"""
from __future__ import annotations

import importlib

_SEARCH_PACKAGES = ("runner.adapters", "runner.video")


def get(adapter_name: str, model_cfg, timeout_s: float):
    module = None
    for pkg in _SEARCH_PACKAGES:
        try:
            module = importlib.import_module(f"{pkg}.{adapter_name}")
            break
        except ModuleNotFoundError as e:
            # only swallow "that module isn't there" — a missing SDK inside a
            # real adapter module must surface as itself
            if e.name and e.name.endswith(adapter_name):
                continue
            raise
    if module is None:
        raise ValueError(
            f"no adapter module {adapter_name}.py in any of {_SEARCH_PACKAGES} "
            f"(referenced by model {getattr(model_cfg, 'id', '?')})")
    if not hasattr(module, "build"):
        raise ValueError(f"adapter {adapter_name} has no build(model_cfg, timeout_s)")
    return module.build(model_cfg, timeout_s)
