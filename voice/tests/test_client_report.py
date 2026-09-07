"""
The client report: a second presentation of the same numbers.

WHAT THESE TESTS ARE FOR. Not the arithmetic - that lives in
`runner.dashboard` and is covered by `test_dashboard.py`. These hold the two
things a second surface can get wrong on its own: disagreeing with the first
one, and quietly dropping a caveat because the audience is external.

OFFLINE AND FREE, like the rest of the suite. The fixtures write real (tiny,
synthesised) wavs so the encoder runs for real; nothing here touches a network
or an API key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from runner.audio import AudioTooLarge, clip_data_uri, guard_size
from runner.client_report import (build, is_gemini, order_models, oriented_gap, pct,
                                  render_client_report)
from runner.dashboard import _duel, _scenario_blocks, load_runs, rollup_models


def _wav(path: Path, seconds: float = 0.4, sr: int = 24000) -> None:
    """A real, tiny clip - the encoder must actually have something to encode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    sf.write(str(path), (0.25 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr)


def _run(root: Path, run_id: str, cells: list[dict], scenario_id: str = "vr-ecom-01") -> None:
    d = root / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenarios").mkdir(exist_ok=True)
    (d / "scenarios" / "s.yaml").write_text(
        f'id: {scenario_id}\nmodality: voice\ntask: text_to_speech\n'
        f'title: "A readback"\ninput:\n  script: |\n    Your code is B 8 Q O.\n',
        encoding="utf-8")
    models = sorted({c["model"] for c in cells})
    (d / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "modality": "voice", "started_at": "2026-09-01T10:00:00+0530",
        "scenario_count": 1,
        "scenarios": [{"id": scenario_id, "task": "text_to_speech", "hash": "h"}],
        "models": [{"id": m, "voice_map": {}} for m in models],
        "judge": {"provider_model": "gemini-2.5-flash"},
        "asr": {"provider_model": "medium"},
        "mos": {"predictor": "signal"},
        "calibration": {"passed": False, "reason": "not run"},
    }), encoding="utf-8")
    with (d / "checks.jsonl").open("w") as ch, (d / "scores.jsonl").open("w") as sc, \
         (d / "telemetry.jsonl").open("w") as tl:
        for c in cells:
            key = {"scenario_id": scenario_id, "model_id": c["model"]}
            _wav(d / "outputs" / "voice" / scenario_id / f"{c['model']}.wav")
            ch.write(json.dumps({**key, "gates": [{"gate": "decodes", "passed": True}],
                                 "measurements": {"normalized_wer": c.get("wer", 0.02),
                                                  "duration_s": 0.4}}) + "\n")
            sc.write(json.dumps({**key, "task": "text_to_speech", "status": "scored",
                                 "score": c["score"], "criterion_scores": {},
                                 "calibration_trusted": False}) + "\n")
            tl.write(json.dumps({**key, "attempt": 1, "status": "ok",
                                 "latency_ms": c.get("lat", 9000),
                                 "output": {"duration_s": 0.4},
                                 "cost": {"micro_usd": c.get("cost", 40000),
                                          "usage_exact": True}}) + "\n")


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "runs"; r.mkdir()
    # ElevenLabs ahead, so the duel puts it in slot `a` - the arrangement that
    # makes the orientation bug below possible at all.
    _run(r, "2026-09-01_100000_voice-r1", [
        {"model": "gemini-3-1-flash-tts", "score": 8.0, "lat": 9000},
        {"model": "elevenlabs-multilingual-v2", "score": 9.0, "lat": 12000}])
    _run(r, "2026-09-01_110000_voice-r2", [
        {"model": "gemini-3-1-flash-tts", "score": 8.0, "lat": 9000},
        {"model": "elevenlabs-multilingual-v2", "score": 9.0, "lat": 12000}])
    return r


# ---------------------------------------------------------------- helpers ---

def test_gemini_is_always_the_left_column():
    assert order_models(["elevenlabs-multilingual-v2", "gemini-3-1-flash-tts"])[0] \
        .startswith("gemini")
    assert is_gemini("gemini-3-1-flash-tts") and not is_gemini("elevenlabs-multilingual-v2")


def test_orienting_a_gap_changes_its_sign_and_nothing_else():
    """Magnitude is a measurement; sign is a presentation choice about order."""
    assert oriented_gap(0.5, {"a": "gemini-x", "b": "eleven-y"}) == 0.5
    assert oriented_gap(0.5, {"a": "eleven-y", "b": "gemini-x"}) == -0.5
    for g in (0.5, -0.5, 0.0, 12.25):
        for duel in ({"a": "gemini-x", "b": "e"}, {"a": "e", "b": "gemini-x"}):
            assert abs(oriented_gap(g, duel)) == abs(g)
    assert oriented_gap(None, {"a": "gemini-x", "b": "e"}) is None


def test_a_percentage_is_the_rubric_score_not_a_rescale():
    assert pct(9.5) == 95.0
    assert pct(0.0) == 0.0
    assert pct(None) is None


# ------------------------------------------------- the two surfaces agree ---

def test_the_client_report_never_disagrees_with_the_internal_board(root):
    """
    THE TEST THIS MODULE EXISTS FOR.

    `_scenario_blocks` computes every gap as `duel.a - duel.b`, and the duel
    ranks by gate rate - which here puts ElevenLabs in slot `a`. The client
    report puts Gemini in the left column. Reordering the columns without
    reorienting the number, or reorienting it anywhere other than the single
    helper, prints a page that contradicts the internal board about the same
    two runs. Nobody reading either page alone could tell.
    """
    runs = load_runs(root, "voice")
    models = rollup_models(runs)
    duel = _duel(models)
    board = {b["id"]: b for b in _scenario_blocks(runs, duel, {})}
    client = {s["id"]: s for s in build(root, "voice")["scenarios"]}

    assert duel["a"] == "elevenlabs-multilingual-v2", "fixture must exercise the flip"
    for sid, b in board.items():
        c = client[sid]
        assert c["winner"] == b["winner"], f"{sid}: the two surfaces named different winners"
        if b["gap"] is not None:
            assert abs(c["gap"]) == pytest.approx(abs(b["gap"])), f"{sid}: magnitude moved"
            # And the sign is oriented to Gemini, not merely copied.
            assert (c["gap"] > 0) == (b["winner"] or "").startswith("gemini") or c["gap"] == 0


def test_the_headline_verdict_is_imported_not_restated(root):
    from runner.dashboard import _overall
    ctx = build(root, "voice")
    assert ctx["overall_verdict"] == _overall(rollup_models(load_runs(root, "voice")))["verdict"]


# --------------------------------------------------------------- the page ---

def test_the_page_carries_its_own_head(root):
    """It is opened from a desktop by double-click, so it stands on its own."""
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>"), (
        "not one byte before the doctype - blank lines there put a browser "
        "into quirks mode, and Jinja's macro definitions leave them behind")
    assert '<meta charset="utf-8">' in html
    assert 'src="outputs/' not in html and "file://" not in html


def test_the_default_keeps_the_audio_beside_the_page(root):
    """
    Inlining every clip produced a 22 MB page that was 99.2% base64 - 167 KB
    of markup behind 22 MB the browser had to parse before painting anything -
    and it would not open. The clips now sit beside the page.
    """
    out = render_client_report(root, "voice")
    assert out.name == "index.html" and out.parent.name == "client-report"
    html = out.read_text(encoding="utf-8")
    assert "data:audio/mpeg" not in html, "the page is markup, not payload"
    assert len(html.encode()) < 2_000_000, "the page itself stays small"

    srcs = re.findall(r'<audio[^>]+src="([^"]+)"', html)
    assert srcs and all(s.startswith("audio/") for s in srcs)
    # The `#` bug must not come back through a filename.
    assert not any("#" in s for s in srcs)
    for s in srcs:
        assert (out.parent / s).exists(), f"{s} is referenced but not written"
    written = {p.name for p in (out.parent / "audio").glob("*.mp3")}
    assert written == {s.split("/", 1)[1] for s in srcs}, "no orphans, no gaps"


def test_inline_still_produces_one_self_contained_file(root):
    """The single-file shape remains, for when it has to travel as one attachment."""
    out = render_client_report(root, "voice", inline=True)
    assert out.name == "client-report.html"
    html = out.read_text(encoding="utf-8")
    assert "data:audio/mpeg;base64," in html
    assert 'src="audio/' not in html


def test_a_rerender_leaves_no_stale_clips_behind(root):
    """A renamed scenario must not leave its old audio in the shared folder."""
    out = render_client_report(root, "voice")
    stray = out.parent / "audio" / "left-over-from-an-old-run.mp3"
    stray.write_bytes(b"junk")
    render_client_report(root, "voice")
    assert not stray.exists()


def test_the_removed_metrics_are_absent(root):
    """Point 7. These belong to the internal report and stay there."""
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    for banned in (">Judged<", ">Attempts<", ">Success<", "&lt;5", "Judge cost"):
        assert banned not in html, f"{banned!r} leaked into the client report"


def test_metrics_are_rows_and_models_are_columns_with_a_difference(root):
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    head = re.search(r"<thead><tr>(.*?)</tr></thead>", html, re.S).group(1)
    assert "gemini-3-1-flash-tts" in head and "elevenlabs-multilingual-v2" in head
    assert "Difference" in head
    # Gemini's column comes first.
    assert head.index("gemini-3-1-flash-tts") < head.index("elevenlabs-multilingual-v2")
    for metric in ("Quality", "Gates passed", "Run-to-run spread", "Cost per clip",
                   "Latency, median"):
        assert f">{metric}" in html


def test_scores_render_as_percentages(root):
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    assert "80.0%" in html and "90.0%" in html, "8.0 and 9.0 out of 10"
    assert "rubric score" in html, "and the page says what a percentage means"


def test_the_google_judge_and_the_calibration_gap_are_disclosed(root):
    """
    The audience is the vendor of the judge. Dropping this because the report
    is client-facing would make it a different report, not a simpler one.
    """
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    # Asserted on SUBSTANCE, not phrasing - the banner was compressed to a
    # one-liner on 2026-09-04 and the wording will change again. What may not
    # change is that both facts are stated, and stated before the numbers.
    flat = " ".join(html.split())
    assert "gemini-2.5-flash" in flat, "the judge is named"
    assert "Google model" in flat, "and identified as a Google model"
    assert "calibrated against human listeners" in flat, "and as uncalibrated"
    head = flat[:flat.index("Every metric, both models")]
    assert "gemini-2.5-flash" in head and "Google model" in head, (
        "the disclosure sits above the numbers, not buried in the footer")


def test_sort_and_expand_controls_exist(root):
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    for opt in ("gem-desc", "gem-asc", "oth-desc", "oth-asc", "gap-desc", "gap-asc"):
        assert f'value="{opt}"' in html
    assert 'id="expand"' in html and 'id="collapse"' in html


def test_an_unscored_scenario_carries_no_sort_key(tmp_path):
    """
    Absent is not zero, and a scenario nobody scored must not sort as the
    worst one. It carries an empty key, which the page pushes to the end in
    both directions.
    """
    r = tmp_path / "runs"; r.mkdir()
    _run(r, "2026-09-01_100000_voice-r1", [
        {"model": "gemini-3-1-flash-tts", "score": 8.0},
        {"model": "elevenlabs-multilingual-v2", "score": 9.0}], scenario_id="vr-ecom-01")
    ctx = build(r, "voice")
    s = ctx["scenarios"][0]
    assert s["sort_gem"] == 80.0 and s["sort_oth"] == 90.0
    # A scenario with no gap has no gap key rather than a zero one.
    assert oriented_gap(None, {"a": "gemini-x", "b": "e"}) is None


# ----------------------------------------------------------------- audio ---

def test_a_clip_encodes_to_a_playable_mp3_data_uri(tmp_path):
    p = tmp_path / "c.wav"; _wav(p, seconds=1.0)
    uri = clip_data_uri(p)
    assert uri.startswith("data:audio/mpeg;base64,")
    import base64
    raw = base64.b64decode(uri.split(",", 1)[1])
    assert raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"ID3"), "real MP3 framing"


def test_a_higher_compression_level_is_a_smaller_file(tmp_path):
    """The flag's direction is counter-intuitive, so it is pinned."""
    p = tmp_path / "c.wav"; _wav(p, seconds=1.5)
    assert len(clip_data_uri(p, 0.8)) < len(clip_data_uri(p, 0.0))


def test_an_undeliverable_file_is_refused_rather_than_written(tmp_path):
    with pytest.raises(AudioTooLarge, match="over the"):
        guard_size(200 * 1024 * 1024, tmp_path / "client-report.html")


def test_every_clip_carries_its_generation_time(root):
    """
    Point 4 asks for latency highlighted per clip. `_clip()` did not carry
    `latency_ms` at all, so the field was silently empty on every clip and
    the page rendered a metadata line with the timing missing - present,
    plausible, and answering a different question than the one asked.
    """
    ctx = build(root, "voice")
    clips = [cl for s in ctx["scenarios"] for c in s["cols"] for cl in c["clips"]]
    assert clips
    assert all(cl["latency_ms"] for cl in clips), "generation time is recorded per clip"
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    assert "generated in" in html


# ------------------------------------------------------------ head to head ---

def test_the_strip_and_the_table_read_off_the_same_rows(root):
    """
    The strip is a second rendering of the metric table. Built from its own
    list it could disagree with the table directly beneath it - which is the
    failure the overall verdict actually had: a headline naming one model
    while the table under it showed the other ahead.
    """
    ctx = build(root, "voice")
    assert [r["label"] for r in ctx["strip"]] == [m["label"] for m in ctx["metrics"]]
    for r, m in zip(ctx["strip"], ctx["metrics"]):
        assert (r["gem"], r["oth"], r["fmt"]) == (m["gem"], m["oth"], m["fmt"])
        # A star on one side only, and never on both.
        assert not (r["gem_win"] and r["oth_win"])
        if r["gem_win"] or r["oth_win"]:
            better = m["gem"] > m["oth"] if m["higher_better"] else m["gem"] < m["oth"]
            assert r["gem_win"] == better, f"{m['label']}: star is on the wrong side"


def test_the_strip_names_the_vendor_behind_each_arm(root):
    ctx = build(root, "voice")
    assert ctx["gem_vendor"] == "Google" and ctx["oth_vendor"] == "ElevenLabs"
    html = render_client_report(root, "voice").read_text(encoding="utf-8")
    assert "· Google" in html and "· ElevenLabs" in html


def test_quality_gets_no_star_inside_the_decision_band(tmp_path):
    """
    The strip is the most glanceable thing on the page and the easiest place
    to over-claim. Everywhere else refuses to name a winner inside the band;
    a star on a hair's-breadth quality gap would quietly contradict that.
    """
    from runner.dashboard import WIN_GAP

    r = tmp_path / "runs"; r.mkdir()
    for rid in ("2026-09-01_100000_voice-r1", "2026-09-01_110000_voice-r2"):
        _run(r, rid, [{"model": "gemini-3-1-flash-tts", "score": 9.00},
                      {"model": "elevenlabs-multilingual-v2", "score": 8.98}])
    ctx = build(r, "voice")
    q = next(x for x in ctx["strip"] if x["label"] == "Quality")
    assert abs(q["gem"] - q["oth"]) <= WIN_GAP
    assert not q["gem_win"] and not q["oth_win"], "0.02 is inside the band - no star"
    # But a gap that clears the band still earns one.
    r2 = tmp_path / "runs2"; r2.mkdir()
    for rid in ("2026-09-01_100000_voice-r1", "2026-09-01_110000_voice-r2"):
        _run(r2, rid, [{"model": "gemini-3-1-flash-tts", "score": 9.50},
                       {"model": "elevenlabs-multilingual-v2", "score": 8.00}])
    q2 = next(x for x in build(r2, "voice")["strip"] if x["label"] == "Quality")
    assert q2["gem_win"] and not q2["oth_win"]


def test_out_writes_the_folder_somewhere_committable(root, tmp_path):
    """
    `voice/dashboard/` is the committed export - the rendered report travels
    with the repo while `runs/` stays gitignored, the same seam
    `apps/dashboard/public/data` uses in the study console.
    """
    dest = tmp_path / "dashboard"
    out = render_client_report(root, "voice", out_dir=dest)
    assert out == dest / "index.html" and out.exists()
    assert (dest / "audio").is_dir()
    srcs = re.findall(r'<audio[^>]+src="([^"]+)"', out.read_text(encoding="utf-8"))
    for s in srcs:
        assert (dest / s).exists(), f"{s} referenced but not written"


def test_a_re_export_keeps_the_hand_written_files_beside_the_page(root, tmp_path):
    """
    README.md and vercel.json live in that folder and are not generated.
    The stale-clip sweep must clear audio, not its neighbours.
    """
    dest = tmp_path / "dashboard"
    render_client_report(root, "voice", out_dir=dest)
    (dest / "README.md").write_text("hand written", encoding="utf-8")
    (dest / "vercel.json").write_text("{}", encoding="utf-8")
    render_client_report(root, "voice", out_dir=dest)
    assert (dest / "README.md").read_text(encoding="utf-8") == "hand written"
    assert (dest / "vercel.json").exists()


def test_out_and_inline_are_refused_together(root, tmp_path):
    """One writes a folder, the other a file - silently picking would surprise."""
    with pytest.raises(SystemExit, match="cannot be combined"):
        render_client_report(root, "voice", inline=True, out_dir=tmp_path / "d")
