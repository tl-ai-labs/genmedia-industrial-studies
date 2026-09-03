"""
The runner-owned wall-clock deadline.

Written after a real incident: on 2026-09-01 a Gemini call sat in CLOSE_WAIT
for 98 minutes at 0% CPU because the hang was inside the OAuth token refresh,
below the SDK's own read timeout. One cell held eleven finished clips
hostage. These tests pin the behaviour that makes that impossible.
"""

from __future__ import annotations

import threading
import time

import pytest

from runner.adapters import Timeout
from runner.generate import call_with_deadline


def test_returns_the_value_when_the_call_finishes_in_time():
    assert call_with_deadline(lambda: 42, 5.0, "fast") == 42


def test_propagates_the_original_exception_not_a_timeout():
    """A call that FAILS fast must surface its own error, not look like a hang."""

    def boom():
        raise ValueError("provider said no")

    with pytest.raises(ValueError, match="provider said no"):
        call_with_deadline(boom, 5.0, "failing")


def test_a_hung_call_raises_timeout_instead_of_blocking_forever():
    started = time.perf_counter()
    with pytest.raises(Timeout, match="wall-clock deadline"):
        call_with_deadline(lambda: time.sleep(30), 0.3, "hung cell")
    # The point of the whole exercise: we came back promptly.
    assert time.perf_counter() - started < 5.0


def test_the_abandoned_worker_is_a_daemon_so_it_cannot_block_exit():
    """
    Python cannot kill a thread, so a blown deadline abandons it. That is only
    acceptable if the leaked thread can never stop the interpreter exiting.
    """
    seen: dict[str, bool] = {}
    gate = threading.Event()

    def slow():
        seen["daemon"] = threading.current_thread().daemon
        gate.set()
        time.sleep(30)

    with pytest.raises(Timeout):
        call_with_deadline(slow, 0.3, "leaky")
    assert gate.wait(2.0), "the worker never started"
    assert seen["daemon"] is True


def test_the_timeout_message_points_at_the_likely_cause():
    """
    An operator reading this at 2am should not have to guess. The message has
    to say that the SDK's own timeout did not fire, because that is the clue
    that sends them below the SDK rather than round it again.
    """
    with pytest.raises(Timeout) as exc:
        call_with_deadline(lambda: time.sleep(30), 0.2, "voi-003 x flash attempt 1")
    msg = str(exc.value)
    assert "voi-003 x flash attempt 1" in msg
    assert "abandoned" in msg
    assert "auth token refresh" in msg


def test_one_hung_cell_does_not_stop_the_others():
    """The barrier this replaced is what turned one bad cell into a dead run."""
    results = []

    def work(n: int):
        if n == 2:
            try:
                call_with_deadline(lambda: time.sleep(30), 0.3, f"cell{n}")
            except Timeout:
                results.append((n, "timeout"))
        else:
            results.append((n, call_with_deadline(lambda: n * 10, 5.0, f"cell{n}")))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert len(results) == 4
    assert (2, "timeout") in results
    assert sorted(r for r in results if r[1] != "timeout") == [(0, 0), (1, 10), (3, 30)]


# --------------------------------------------------------------------------
# Scenario atomicity: a scenario is done when EVERY model has answered it.
# --------------------------------------------------------------------------

from types import SimpleNamespace as _NS

_LIMITS = _NS(max_concurrency=2, rpm=None)

def test_run_generation_groups_by_scenario_and_reports_incompleteness(tmp_path, monkeypatch):
    """
    One model succeeds, the other fails. The scenario must be announced as
    INCOMPLETE and a telemetry row written saying which arm is missing —
    a half-answered scenario is not comparable and must not read as a result
    for the model that did finish.
    """
    from types import SimpleNamespace

    from runner import generate as gen

    lines: list[str] = []
    written: list[dict] = []

    scenario = SimpleNamespace(id="s1", modality="voice", task="text_to_speech",
                               text="hello", params={}, language=None, style=None, checks={})
    cells = [
        SimpleNamespace(scenario=scenario, key=f"s1|{m}",
                        model=SimpleNamespace(id=m, provider="p", limits=_LIMITS))
        for m in ("alpha", "beta")
    ]
    matrix = SimpleNamespace(cells=cells)

    def fake_generate(cell, *a, **k):
        ok = cell.model.id == "alpha"
        return gen.CellOutcome(cell, "ok" if ok else "provider_error", 1,
                               error=None if ok else "boom")

    monkeypatch.setattr(gen, "_generate_one", fake_generate)
    monkeypatch.setattr(gen, "measure_cell", lambda outcome, *a, **k: outcome)

    tel = SimpleNamespace(write=lambda stream, row: written.append(row))
    gen.run_generation(matrix, paths=None, tel=tel, asr=None, predictor=None,
                       budget=gen.Budget(None), workers=2, log=lines.append)

    joined = "\n".join(lines)
    assert "INCOMPLETE" in joined
    assert "alpha" not in joined.split("INCOMPLETE")[1].split("\n")[0]  # alpha finished
    assert "beta" in joined.split("INCOMPLETE")[1].split("\n")[0]

    inc = [w for w in written if w.get("step") == "scenario"]
    assert len(inc) == 1
    assert inc[0]["status"] == "incomplete"
    assert inc[0]["models_done"] == 1 and inc[0]["models_expected"] == 2
    assert inc[0]["missing"] == ["beta"]


def test_a_fully_answered_scenario_is_announced_complete(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from runner import generate as gen

    lines: list[str] = []
    scenario = SimpleNamespace(id="s1", modality="voice", task="text_to_speech",
                               text="hello", params={}, language=None, style=None, checks={})
    cells = [
        SimpleNamespace(scenario=scenario, key=f"s1|{m}",
                        model=SimpleNamespace(id=m, provider="p", limits=_LIMITS))
        for m in ("alpha", "beta")
    ]
    monkeypatch.setattr(gen, "_generate_one",
                        lambda cell, *a, **k: gen.CellOutcome(cell, "ok", 1))
    monkeypatch.setattr(gen, "measure_cell", lambda outcome, *a, **k: outcome)

    gen.run_generation(SimpleNamespace(cells=cells), paths=None,
                       tel=SimpleNamespace(write=lambda *a: None), asr=None, predictor=None,
                       budget=gen.Budget(None), workers=2, log=lines.append)
    assert "COMPLETE s1" in "\n".join(lines).replace("COMPLETE  ", "COMPLETE ")
