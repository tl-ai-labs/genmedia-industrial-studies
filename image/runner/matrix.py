"""Scenarios x models filtered by task support — the list of cells the run
will attempt (plan §5). A model that cannot do a task is never called with
it: the cell is `skipped` before any spend and shows as n/a in the report,
excluded from that model's denominators.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Cell:
    scenario_id: str
    model_id: str
    modality: str
    task: str
    state: str = "planned"
    reason: str = ""
    history: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.scenario_id}::{self.model_id}"


def build_matrix(scenarios, models) -> list[Cell]:
    cells: list[Cell] = []
    for s in scenarios:
        for m in models:
            if m.modality != s.modality:
                continue
            if s.task in m.supports:
                cells.append(Cell(s.id, m.id, s.modality, s.task))
            else:
                cells.append(Cell(s.id, m.id, s.modality, s.task,
                                  state="skipped",
                                  reason=f"model does not support task {s.task!r}"))
    return cells
