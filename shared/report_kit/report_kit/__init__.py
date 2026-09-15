"""The one presentation layer for every lane's reports (image, video, voice).

A lane owns its DATA: loading run records, scoring, and the media it shows
(images, video clips, audio). This kit owns how that data is PRESENTED: the
page chrome, the section order, the tables, the styling tokens, the number
formats, the audience rules (internal vs client) and the file names.

A formatting change is made here, once, and every lane picks it up on its next
render. The rules for changing it are in ../RULES.md.
"""
from .compare import (INTERNAL_ROWS, build_duel, finish_rollup, gemini_first,
                      metric_rows, rollup, scenario_result, study_title,
                      tab_label, tally, vendor_of, wtl_rollup)
from .formatting import make_filters
from .rendering import (KIT_TEMPLATES, LaneProfile, generated_stamp, make_env,
                        render_both, render_page, render_run,
                        run_report_paths, study_report_paths, write_page)
from .study import build_study, render_study, vendor_lines

__all__ = [
    "INTERNAL_ROWS", "KIT_TEMPLATES", "LaneProfile", "build_duel", "build_study",
    "finish_rollup", "gemini_first", "generated_stamp", "make_env",
    "make_filters", "metric_rows", "render_both", "render_page", "render_run",
    "render_study", "write_page",
    "rollup", "run_report_paths", "vendor_lines",
    "scenario_result", "study_report_paths", "study_title", "tab_label",
    "tally", "vendor_of", "wtl_rollup",
]
