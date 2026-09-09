"""Video lane — everything video-specific lives in this package:

  * provider adapters (veo_video, openai_video) — resolved by name via
    runner/adapters/__init__.py, so `adapter: veo_video` in models.yaml
    keeps working unchanged
  * deterministic checks (checks.py) — registered in runner/checks.py's
    CHECK_SUITES by dotted path

The engine (generate/judge/score/report) stays modality-agnostic in runner/;
a new modality is a sibling package like this one (see ../image/ for the
image project this engine was lifted from).
"""
