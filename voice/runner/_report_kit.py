"""Imports the repo's shared report kit (shared/report_kit).

Every lane presents its reports through that one kit, so a formatting change
is made once for image, video and voice. Lanes still never import each other.
If the kit is pip-installed it is used as-is; otherwise it is found beside the
lanes in this checkout. This file is identical in every lane — do not edit it
in one lane only.
"""
import sys
from pathlib import Path

try:
    import report_kit  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "report_kit"))
    import report_kit  # noqa: F401

from report_kit import *  # noqa: F401,F403,E402
