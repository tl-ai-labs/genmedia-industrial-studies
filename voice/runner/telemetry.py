"""
The ONE writer (plan v1.2 sections 09 and 10).

Nothing else in this project appends to a JSONL file. One writer means one
place where the append is locked, one place where a row is stamped with the
run id and a timestamp, and one place to change when a field is added.

APPEND-ONLY AND IMMUTABLE. Nothing rewrites a row and nothing edits a file
under outputs/. A correction is a new run. That is what makes "the report
says 8.4, prove it" a five-second answer.

RunPaths owns the on-disk layout so that every module agrees on where an
artefact lives, and so the voice lane physically cannot write into the image
lane's directory: outputs are namespaced by modality, and a voice process
only ever asks for outputs_dir("voice", ...).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def artefact_url(rel_path: str) -> str:
    """
    A run-relative artefact path, in the form a BROWSER can actually fetch.

    THE BUG THIS EXISTS TO PREVENT. A variant scenario writes its clips under
    a directory named `parent#variant` - `vr-ecom-06#bare`. In a URL `#` opens
    the FRAGMENT, so `<audio src=".../vr-ecom-06#bare/eleven.wav">` asks the
    server for `.../vr-ecom-06`, gets a 404, and renders a player that looks
    perfectly normal and does nothing when pressed. 192 of the dashboard's 242
    players were dead this way, and nothing on the page or in the console said
    so - a broken <audio> element is silent in both senses.

    Encode the path, never the separators: `safe="/"` keeps the directory
    structure and escapes `#` to `%23`, which is what the file is really
    called on disk. Applies to every surface that points at a run artefact,
    so the per-run report and the cross-run dashboard cannot diverge.
    """
    from urllib.parse import quote

    return quote(rel_path, safe="/")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_run_id(label: str) -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_{label}"


@dataclass(frozen=True)
class RunPaths:
    root: Path
    run_id: str

    @property
    def dir(self) -> Path:
        return self.root / self.run_id

    @property
    def manifest(self) -> Path:
        return self.dir / "manifest.json"

    @property
    def scenarios_dir(self) -> Path:
        return self.dir / "scenarios"

    @property
    def inputs_dir(self) -> Path:
        return self.dir / "inputs"

    @property
    def report(self) -> Path:
        return self.dir / "report.html"

    def stream(self, name: str) -> Path:
        return self.dir / f"{name}.jsonl"

    def outputs_dir(self, modality: str, scenario_id: str) -> Path:
        """runs/<run>/outputs/<modality>/<scenario>/ - the per-scenario folder."""
        return self.dir / "outputs" / modality / scenario_id

    def output_path(self, modality: str, scenario_id: str, model_id: str, ext: str) -> Path:
        """
        The artefact itself: <model-id>.<ext>. Named by MODEL so a scenario's
        folder is a side-by-side of every model that answered it, which is the
        shape the report reads and the shape a human browsing the folder
        expects.
        """
        return self.outputs_dir(modality, scenario_id) / f"{model_id}.{ext}"

    def transcript_path(self, modality: str, scenario_id: str, model_id: str) -> Path:
        """The ASR transcript, kept beside the audio as evidence."""
        return self.outputs_dir(modality, scenario_id) / f"{model_id}.txt"

    def ensure(self) -> RunPaths:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.scenarios_dir.mkdir(exist_ok=True)
        self.inputs_dir.mkdir(exist_ok=True)
        return self


class Telemetry:
    """Append-only JSONL streams for one run. Thread-safe."""

    def __init__(self, paths: RunPaths, run_id: str) -> None:
        self.paths = paths
        self.run_id = run_id
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, stream: str) -> threading.Lock:
        with self._guard:
            if stream not in self._locks:
                self._locks[stream] = threading.Lock()
            return self._locks[stream]

    def write(self, stream: str, row: dict[str, Any]) -> dict[str, Any]:
        """
        Append one row. Returns the row as written (with ts and run_id
        stamped) so a caller can keep it in memory without re-reading.
        """
        full = {"ts": utc_now(), "run_id": self.run_id, **row}
        path = self.paths.stream(stream)
        line = json.dumps(full, ensure_ascii=False, separators=(",", ":"))
        with self._lock_for(stream):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return full


def read_stream(paths: RunPaths, stream: str) -> list[dict[str, Any]]:
    path = paths.stream(stream)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def iter_stream(paths: RunPaths, stream: str) -> Iterator[dict[str, Any]]:
    path = paths.stream(stream)
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def incomplete_scenarios(paths: RunPaths) -> set[str]:
    """
    Scenario ids that not every model answered.

    The runner writes one of these rows when a scenario finishes with a
    missing arm. Every consumer of a run's numbers must read this and exclude
    those scenarios: a paired comparison needs both models on the identical
    input, so a scenario answered by one of them is not a result for that one
    - it is a gap. Scoring it would credit whichever model happened to
    survive, which is the opposite of what the failure means.
    """
    return {
        r["scenario_id"]
        for r in read_stream(paths, "telemetry")
        if r.get("step") == "scenario" and r.get("status") == "incomplete" and r.get("scenario_id")
    }


def write_manifest(paths: RunPaths, manifest: dict[str, Any]) -> None:
    paths.manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def read_manifest(paths: RunPaths) -> dict[str, Any]:
    if not paths.manifest.exists():
        raise FileNotFoundError(
            f"no manifest at {paths.manifest} - is '{paths.run_id}' a real run directory?"
        )
    return json.loads(paths.manifest.read_text(encoding="utf-8"))


def latest_run(root: Path, modality: str | None = None) -> str | None:
    """Most recent run directory, so `--run` can be omitted in the common case."""
    root = Path(root)
    if not root.exists():
        return None
    candidates = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    for c in candidates:
        if not (c / "manifest.json").exists():
            continue
        if modality:
            try:
                man = json.loads((c / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if man.get("modality") != modality:
                continue
        return c.name
    return None
