"""Shared google-genai client construction — used by the judge adapter and
every Google generation adapter (image, video), so the two auth routes are
defined exactly once."""
from __future__ import annotations

import os


def _make_client(genai, types, cfg, timeout_s: float):
    """Same models, two auth routes: API key (Gemini Developer API) or
    Vertex AI on a billed GCP project via ADC — chosen purely by config."""
    http = types.HttpOptions(timeout=int(timeout_s * 1000))
    if getattr(cfg, "vertex", None):
        return genai.Client(vertexai=True, project=cfg.vertex.project,
                            location=cfg.vertex.location, http_options=http)
    return genai.Client(api_key=os.environ[cfg.auth_env], http_options=http)
