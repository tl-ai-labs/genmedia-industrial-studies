"""
The per-run report, rendered through the shared report kit.

It had no rendering test before it moved onto the kit. These hold what a
per-run page must keep: two audiences from one context, the kit's sections,
the player beside the clip, and the internal-only accounting kept internal.
"""

from __future__ import annotations

import re
from pathlib import Path

from runner.models import load_registry
from runner.report import render
from runner.rubrics import load_rubric
from runner.scenarios import Scenario

from tests.test_summary import _run

ROOT = Path(__file__).resolve().parent.parent


def _render(tmp_path):
    p = _run(tmp_path, "2026-09-01_100000_voice-r1", [
        {"model": "gemini-x", "status": "scored", "score": 9.0, "lat": 8000, "cost": 90000},
        {"model": "elevenlabs-y", "status": "scored", "score": 8.0, "lat": 40000, "cost": 30000},
    ])
    wav = p.dir / "outputs" / "voice" / "s1" / "gemini-x.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF")
    scen = [Scenario(id="s1", modality="voice", task="text_to_speech", title="A readback",
                     text="Your code is B 8.", expected="clean read")]
    rubric = load_rubric(ROOT / "configs", "voice", "text_to_speech")
    out = render(p, scen, {"text_to_speech": rubric}, load_registry(ROOT / "configs"), ROOT)
    return out, out.read_text(encoding="utf-8"), (p.dir / "report-client.html").read_text(encoding="utf-8")


def test_both_audiences_are_written_from_one_render(tmp_path):
    out, internal, client = _render(tmp_path)
    assert out.name == "report.html"
    for html in (internal, client):
        assert html.startswith("<!doctype html>")
        assert '<table class="mx">' in html and 'id="evidence"' in html
        assert 'class="scn rev"' in html


def test_the_clip_plays_from_the_run_folder(tmp_path):
    _, internal, client = _render(tmp_path)
    for html in (internal, client):
        assert '<audio controls preload="none" src="outputs/voice/s1/gemini-x.wav">' in html


def test_run_accounting_stays_internal(tmp_path):
    _, internal, client = _render(tmp_path)
    assert "Run total" in internal and "Run total" not in client
    assert 'class="tiles"' in internal and 'class="tiles"' not in client
    # the rating row, in each audience's units: a percentage for the client,
    # the 10-point scale internally (scores are re-derived by build_scores)
    rating = lambda html: re.search(r'<tr data-row="mean".*?</tr>', html, re.S).group(0)
    assert re.search(r">\d+\.\d%</td>", rating(client))
    assert re.search(r">\d\.\d\d</td>", rating(internal)) and "%" not in rating(internal)
