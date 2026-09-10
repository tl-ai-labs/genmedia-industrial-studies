"""
`python -m runner.cli` - export, serve, correlate.

    export    --run image=<run-dir> --run video=<run-dir> --run voice=<run-dir>
              [--voice-dashboard ../voice/dashboard [--voice-items scenario|all]]
    serve     [--port 8765] [--host 0.0.0.0]
    correlate [--out correlation.md] [--json correlation.json]
              [--run lane:run_id=<dir>] [--tie-band lane=x]

Free, offline, no API key. Nothing here can spend.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PANEL_ROOT = Path(__file__).resolve().parent.parent
DIST = PANEL_ROOT / "dist"
PRIVATE = PANEL_ROOT / "private"
KEY = PRIVATE / "key.json"
VOTES = PANEL_ROOT / "votes.jsonl"


def _lane_dir(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError("expected lane=<run-dir>")
    lane, d = spec.split("=", 1)
    p = Path(d).expanduser()
    if not p.is_dir():
        raise argparse.ArgumentTypeError(f"{p} is not a directory")
    return lane.strip(), p


def cmd_export(a: argparse.Namespace) -> int:
    from .export import export, format_summary
    extra = []
    if a.voice_dashboard:
        from .voice_dashboard import load_dashboard
        extra.append(load_dashboard(a.voice_dashboard, a.private / "voice-dashboard",
                                    items=a.voice_items))
    if not a.run and not extra:
        raise SystemExit("nothing to export: pass --run lane=<dir> and/or --voice-dashboard <dir>")
    s = export(a.run or [], dist=a.dist, private=a.private, salt=a.salt, extra=extra)
    print(format_summary(s))
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    from .serve import make_server
    srv = make_server(a.dist, a.key, a.votes, host=a.host, port=a.port)
    print(f"panel on http://{a.host}:{a.port}/  votes -> {a.votes}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_correlate(a: argparse.Namespace) -> int:
    from .correlate import correlate, render_markdown
    overrides = {}
    for spec in a.run or []:
        head, d = spec.split("=", 1)
        lane, run_id = head.split(":", 1)
        overrides[(lane, run_id)] = Path(d).expanduser()
    bands = {}
    for spec in a.tie_band or []:
        lane, v = spec.split("=", 1)
        bands[lane] = float(v)
    if not Path(a.votes).exists():
        print(f"no votes yet at {a.votes}", file=sys.stderr)
        return 2
    rep = correlate(a.votes, key_path=a.key, overrides=overrides, tie_bands=bands)
    md = render_markdown(rep)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(md)
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")
        print(f"wrote {a.json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="panel", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="copy run media to opaque ids under dist/")
    e.add_argument("--run", action="append", type=_lane_dir,
                   metavar="lane=<run-dir>", help="repeatable; one per run to include")
    e.add_argument("--voice-dashboard", type=Path, metavar="<dir>",
                   help="import voice from the committed voice/dashboard export "
                        "(when the voice runs are not on this machine)")
    e.add_argument("--voice-items", choices=["scenario", "all"], default="scenario",
                   help="one voice item per scenario (default) or every matched clip pair")
    e.add_argument("--dist", type=Path, default=DIST)
    e.add_argument("--private", type=Path, default=PRIVATE)
    e.add_argument("--salt", help="fix the salt so ids are reproducible (tests only)")
    e.set_defaults(fn=cmd_export)

    s = sub.add_parser("serve", help="serve dist/ and record votes")
    s.add_argument("--dist", type=Path, default=DIST)
    s.add_argument("--key", type=Path, default=KEY)
    s.add_argument("--votes", type=Path, default=VOTES)
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)

    c = sub.add_parser("correlate", help="human majority vs judge, per lane and overall")
    c.add_argument("--votes", type=Path, default=VOTES)
    c.add_argument("--key", type=Path, default=KEY,
                   help="used only to locate run folders; votes carry the models")
    c.add_argument("--run", action="append", metavar="lane:run_id=<dir>",
                   help="where a run's scores.jsonl lives, if not where the key says")
    c.add_argument("--tie-band", action="append", metavar="lane=x",
                   help="override a lane's judge tie band")
    c.add_argument("--out", help="write markdown here instead of stdout")
    c.add_argument("--json", help="also write the full report as JSON")
    c.set_defaults(fn=cmd_correlate)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
