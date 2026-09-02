"""Image lane — everything image-specific lives in this package:

  * provider adapters (gemini_image, openai_image) — resolved by name via
    runner/adapters/__init__.py, so `adapter: gemini_image` in models.yaml
    keeps working unchanged
  * deterministic checks (checks.py) — registered in runner/checks.py's
    CHECK_SUITES by dotted path

The engine (generate/judge/score/report) stays modality-agnostic in runner/;
a new modality is a sibling package like this one (see runner/video/).
"""
