"""Rendering and naming: one context, two audiences, fixed file names.

Every report is rendered TWICE from ONE context — internal and client — so the
two files can never disagree on a number, and there is no flag to forget.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from .formatting import make_filters

KIT_TEMPLATES = Path(__file__).parent / "templates"

# File names. A per-run report lives inside its run folder; a study (several
# runs on one page) lives in runs/. The client copy always sits beside the
# internal one with a "-client" suffix.
RUN_INTERNAL = "report.html"
RUN_CLIENT = "report-client.html"
CLIENT_SUFFIX = "-client"


@dataclass(frozen=True)
class LaneProfile:
    """What the kit needs to know about a lane — nothing about its data.

    key        "image" | "video" | "voice" — shown in the top bar and titles
    unit       the noun for one output: "image", "clip" ("Cost per clip")
    media      "image" | "video" | "audio" — used only in wording
    templates  the lane's template folder; it must hold lane_hooks.j2
    """
    key: str
    unit: str
    media: str
    templates: Path
    extra_filters: dict = field(default_factory=dict)


def make_env(lane: LaneProfile, names: dict | None = None,
             client: bool = False) -> Environment:
    """Lane templates first (lane_hooks.j2 and any lane page), kit second."""
    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(str(lane.templates)),
                             FileSystemLoader(str(KIT_TEMPLATES))]),
        autoescape=select_autoescape(["html", "j2"]))
    env.filters.update(make_filters(names, client=client))
    env.filters.update(lane.extra_filters)
    env.globals["CLIENT"] = client
    env.globals["AUDIENCE"] = "client" if client else "internal"
    env.globals["LANE"] = lane
    return env


def run_report_paths(run_dir: Path) -> tuple[Path, Path]:
    run_dir = Path(run_dir)
    return run_dir / RUN_INTERNAL, run_dir / RUN_CLIENT


def study_report_paths(out_path: Path) -> tuple[Path, Path]:
    out_path = Path(out_path)
    return out_path, out_path.with_name(out_path.stem + CLIENT_SUFFIX + out_path.suffix)


def generated_stamp() -> str:
    """When THIS file was rendered — distinct from the run's own created time,
    since a re-render (a new rule, a layout change) is a new report."""
    return _dt.datetime.now().astimezone().strftime("%d %b %Y, %H:%M %Z")


def render_page(lane: LaneProfile, template: str, ctx: dict, client: bool,
                names: dict | None = None) -> str:
    """One audience of one report, returned (not written). Pages read the
    whole context as `ctx`, and its top-level keys directly."""
    ctx = dict(ctx)
    ctx.setdefault("generated", generated_stamp())
    env = make_env(lane, names if names is not None else ctx.get("names"), client=client)
    return env.get_template(template).render(ctx=ctx, **ctx).lstrip()   # doctype first


def write_page(html: str, path: Path, guard=None) -> Path:
    """Write a rendered page as UTF-8. `guard(n_bytes, path)` may refuse an
    undeliverable size before anything is written."""
    data = html.encode("utf-8")
    if guard is not None:
        guard(len(data), Path(path))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return Path(path)


def render_both(lane: LaneProfile, template: str, ctx: dict,
                internal_path: Path, client_path: Path,
                names: dict | None = None, guard=None) -> tuple[str, str]:
    """Render the internal and client copies of one report from ONE context and
    write both. Returns (internal_html, client_html)."""
    ctx = dict(ctx)
    ctx.setdefault("generated", generated_stamp())       # one stamp for both copies
    internal = render_page(lane, template, ctx, client=False, names=names)
    client = render_page(lane, template, ctx, client=True, names=names)
    write_page(internal, internal_path, guard)
    write_page(client, client_path, guard)
    return internal, client


def render_run(lane: LaneProfile, ctx: dict, run_dir: Path) -> Path:
    """Write <run>/report.html and <run>/report-client.html from one context.
    Returns the internal path."""
    internal, client = run_report_paths(run_dir)
    render_both(lane, "kit/run.html.j2", ctx, internal, client, names=ctx.get("names"))
    return internal
