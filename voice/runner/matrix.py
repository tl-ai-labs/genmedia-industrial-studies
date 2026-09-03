"""
The matrix - scenarios x models, filtered by task support (plan v1.2 section 05).

The runner builds this BEFORE any call, so a model that cannot do a task is
never asked to. That is not an optimisation; it is what makes the report's
n/a honest. An unsupported cell is excluded from that model's denominators
entirely - supporting fewer tasks is a capability fact for the report to
state, not a quality penalty to apply silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ModelSpec
from .scenarios import Scenario


@dataclass(frozen=True)
class Cell:
    scenario: Scenario
    model: ModelSpec

    @property
    def key(self) -> str:
        return f"{self.scenario.id}|{self.model.id}"


@dataclass(frozen=True)
class Skipped:
    scenario_id: str
    model_id: str
    reason: str


@dataclass(frozen=True)
class Matrix:
    cells: tuple[Cell, ...]
    skipped: tuple[Skipped, ...]

    @property
    def model_ids(self) -> list[str]:
        seen: list[str] = []
        for c in self.cells:
            if c.model.id not in seen:
                seen.append(c.model.id)
        return seen

    @property
    def tasks(self) -> list[str]:
        seen: list[str] = []
        for c in self.cells:
            if c.scenario.task not in seen:
                seen.append(c.scenario.task)
        return seen

    def coverage(self) -> dict[str, list[str]]:
        """model_id -> the tasks it was eligible for. Printed beside every model."""
        out: dict[str, list[str]] = {}
        for c in self.cells:
            out.setdefault(c.model.id, [])
            if c.scenario.task not in out[c.model.id]:
                out[c.model.id].append(c.scenario.task)
        return out


def build(scenarios: list[Scenario], models: list[ModelSpec]) -> Matrix:
    cells: list[Cell] = []
    skipped: list[Skipped] = []
    for s in scenarios:
        for m in models:
            if m.modality != s.modality:
                continue
            if not m.supports_task(s.task):
                skipped.append(
                    Skipped(
                        s.id,
                        m.id,
                        f"model does not support task '{s.task}' (supports {list(m.supports)})",
                    )
                )
                continue
            cells.append(Cell(scenario=s, model=m))
    return Matrix(cells=tuple(cells), skipped=tuple(skipped))
