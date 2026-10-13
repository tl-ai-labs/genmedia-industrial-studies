"""The judge command's resume rule: only a successful judgement is final."""

import json
from pathlib import Path
from types import SimpleNamespace

from runner import cli
from runner.telemetry import RunPaths


def _setup(tmp_path, monkeypatch, judge_rows):
    paths = RunPaths(tmp_path, "run1").ensure()
    (paths.dir / "outputs/voice/s1").mkdir(parents=True)
    for mid in ("m1", "m2"):
        (paths.dir / f"outputs/voice/s1/{mid}.wav").write_bytes(b"")
    paths.stream("checks").write_text("".join(
        json.dumps({"scenario_id": "s1", "model_id": m, "passed": True, "failed_gates": [],
                    "measurements": {}}) + "\n" for m in ("m1", "m2")))
    paths.stream("judge").write_text("".join(json.dumps(r) + "\n" for r in judge_rows))

    spec = SimpleNamespace(has_credential=True, provider_model="fake", temperature=0.0, auth_env="X")
    monkeypatch.setattr(cli, "load_registry", lambda c: SimpleNamespace(judges={"voice": spec}))
    monkeypatch.setattr(cli, "_resolve_run", lambda *a: paths)
    monkeypatch.setattr(cli, "read_manifest", lambda p: {"modality": "voice"})
    monkeypatch.setattr(cli, "load_scenarios", lambda *a: [SimpleNamespace(id="s1", task="t")])
    monkeypatch.setattr(cli, "_rubrics_for", lambda *a: {"t": None})
    monkeypatch.setattr(cli, "build_judge", lambda s: None)
    called = []

    def fake_judge_cell(backend, spec, scenario, rubric, mid, label, audio, facts):
        called.append(mid)
        return SimpleNamespace(status="judged", criteria=[], cost=None, blind_label=label,
                               as_record={"scenario_id": "s1", "model_id": mid, "status": "judged"})

    monkeypatch.setattr(cli, "judge_cell", fake_judge_cell)
    args = SimpleNamespace(configs="c", runs="r", run="run1", modality="voice", scenarios="s", budget=1.0)
    return paths, called, args


def test_an_unjudged_cell_is_judged_again_and_a_judged_one_is_not(tmp_path, monkeypatch):
    rows = [
        {"scenario_id": "s1", "model_id": "m1", "status": "judged"},
        {"scenario_id": "s1", "model_id": "m2", "status": "unjudged", "error": "429 RESOURCE_EXHAUSTED"},
    ]
    paths, called, args = _setup(tmp_path, monkeypatch, rows)
    cli.cmd_judge(args)
    assert called == ["m2"]
    stream = [json.loads(l) for l in paths.stream("judge").read_text().splitlines()]
    # The failed attempt stays as history; the newest row for the cell is the success.
    assert [r["status"] for r in stream if r["model_id"] == "m2"] == ["unjudged", "judged"]
