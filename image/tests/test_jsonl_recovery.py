"""An editor pretty-printing a run's JSONL files must not break reads, and
fixjsonl must restore the canonical layout losslessly (it has happened four
times in real use)."""
import json

from runner.telemetry import RunFiles, fix_jsonl

ROWS = [
    {"ts": "2026-09-01T00:00:00.000Z", "scenario_id": "s1", "model_id": "a",
     "status": "ok", "cost": {"micro_usd": 134000}},
    {"ts": "2026-09-01T00:00:05.000Z", "scenario_id": "s1", "model_id": "b",
     "status": "rate_limited", "error": "429"},
]


def _write_pretty(path):
    """What a JSON-formatting editor does on save: records intact, layout gone."""
    path.write_text("\n".join(json.dumps(r, indent=2) for r in ROWS) + "\n")


def test_read_recovers_pretty_printed(tmp_path, capsys):
    _write_pretty(tmp_path / "telemetry.jsonl")
    rows = RunFiles(tmp_path).read("telemetry")
    assert rows == ROWS
    assert "fixjsonl" in capsys.readouterr().err  # loud, with the fix command


def test_read_canonical_stays_silent(tmp_path, capsys):
    files = RunFiles(tmp_path)
    for r in ROWS:
        files.telemetry.append(r)
    assert files.read("telemetry") == ROWS
    assert capsys.readouterr().err == ""


def test_fixjsonl_restores_layout_and_keeps_backup(tmp_path):
    _write_pretty(tmp_path / "telemetry.jsonl")
    files = RunFiles(tmp_path)
    for r in ROWS:  # a healthy file next to the broken one
        files.checks.append(r)

    result = fix_jsonl(tmp_path)
    assert any(s.startswith("telemetry.jsonl") for s in result["repaired"])
    assert "checks.jsonl" in result["ok"]

    # canonical again: every line parses on its own, content identical
    lines = [l for l in (tmp_path / "telemetry.jsonl").read_text().splitlines() if l]
    assert [json.loads(l) for l in lines] == ROWS
    # the incident stays visible
    assert (tmp_path / "telemetry.jsonl.reformatted.bak").exists()
    # second pass: nothing left to repair
    assert fix_jsonl(tmp_path)["repaired"] == []
