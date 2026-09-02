"""Load and validate scenarios (YAML dir/file or CSV), models.yaml and rubrics.

Scenarios are data, never hardcoded. Two input formats:
  * scenarios/*.yaml — the full format from the plan (§6)
  * a CSV/sheet export — one row per scenario: id, task, prompt, expected,
    required_text — so a whole batch can be set up from a spreadsheet.

Weights must sum to 1.0; the loader rejects anything else rather than
silently normalising. Rubric files are hashed; the hash is stamped into every
judge and score record.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .lifecycle import task_modality

WEIGHT_TOLERANCE = 1e-6


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: str
    modality: str
    task: str
    title: str = ""
    prompt: str = ""
    input: dict = Field(default_factory=dict)     # voice lane: script / language / style
    inputs: dict = Field(default_factory=dict)    # typed assets by role: source, mask, reference
    params: dict = Field(default_factory=dict)
    expected: str
    checks: dict = Field(default_factory=dict)
    criteria: Optional[list[str]] = None
    weights: Optional[dict[str, float]] = None
    tags: list[str] = Field(default_factory=list)

    source_path: Optional[str] = None             # where this scenario was loaded from

    @field_validator("modality")
    @classmethod
    def _modality_known(cls, v: str) -> str:
        if v not in ("image", "voice", "video"):
            raise ValueError(f"unknown modality {v!r}")
        return v

    @model_validator(mode="after")
    def _validate(self) -> "Scenario":
        expected_mod = task_modality(self.task)   # raises on unknown task
        if expected_mod != self.modality:
            raise ValueError(
                f"scenario {self.id}: task {self.task!r} belongs to modality "
                f"{expected_mod!r}, not {self.modality!r}")
        from .lifecycle import BUILD_TASKS
        for role in BUILD_TASKS.get(self.task, {}).get("inputs", []):
            if role not in self.inputs:
                raise ValueError(
                    f"scenario {self.id}: task {self.task!r} requires an "
                    f"input asset with role {role!r} (inputs: {{{role}: path}})")
        if not self.prompt and not self.input.get("script"):
            raise ValueError(f"scenario {self.id}: needs a prompt (or input.script for voice)")
        if (self.criteria is None) != (self.weights is None):
            raise ValueError(
                f"scenario {self.id}: criteria and weights must be given together")
        if self.weights is not None:
            _check_weights(self.weights, f"scenario {self.id}")
            if set(self.weights) != set(self.criteria or []):
                raise ValueError(
                    f"scenario {self.id}: weights keys must match criteria list")
        return self

    @property
    def text(self) -> str:
        """The exact string sent to every model — prompt or script."""
        return self.prompt if self.prompt else self.input.get("script", "")


def _check_weights(weights: dict[str, float], where: str) -> None:
    total = sum(weights.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(
            f"{where}: weights sum to {total:.6f}, not 1.0 — rejected, not "
            f"normalised (a normalised typo is a scoring bug that never "
            f"announces itself)")


def _scenario_from_yaml(path: Path) -> Scenario:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a mapping")
    data["source_path"] = str(path)
    return Scenario(**data)


SHEET_REQUIRED_COLS = {"id", "task", "prompt", "expected", "required_text"}


def _scenario_from_sheet_row(row: dict, source: str) -> Scenario:
    task = row["task"]
    modality = task_modality(task)
    checks: dict = {}
    if row.get("required_text"):
        # "A | B | C" in the sheet = independent strings, each OCR-matched on
        # its own (UI labels); a plain value = one contiguous phrase
        if "|" in row["required_text"]:
            checks["must_read_text"] = [p.strip() for p in
                                        row["required_text"].split("|") if p.strip()]
        else:
            checks["must_read_text"] = row["required_text"]
    data: dict = {
        "id": row["id"], "modality": modality, "task": task,
        "title": row.get("title") or row["id"],
        "expected": row["expected"], "checks": checks,
        "source_path": source,
    }
    if row.get("tags"):
        data["tags"] = [t.strip() for t in row["tags"].split(",") if t.strip()]
    if modality == "voice":
        data["input"] = {"script": row["prompt"]}
    else:
        data["prompt"] = row["prompt"]
    return Scenario(**data)


def _scenarios_from_csv(path: Path) -> list[Scenario]:
    """Simple sheet format: id, task, prompt, expected, required_text."""
    out: list[Scenario] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = {c.strip() for c in (reader.fieldnames or [])}
        missing = SHEET_REQUIRED_COLS - cols
        if missing:
            raise ValueError(f"{path}: CSV is missing columns {sorted(missing)}")
        for i, row in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            if not row.get("id"):
                continue  # blank line
            out.append(_scenario_from_sheet_row(row, f"{path}:{i}"))
    if not out:
        raise ValueError(f"{path}: no scenario rows found")
    return out


def _scenarios_from_xlsx(path: Path) -> list[Scenario]:
    """Same contract as the CSV loader, straight from a spreadsheet file.
    Headers on row 1 of the first worksheet (extra columns are ignored)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    try:
        header = [str(h).strip().lower() if h is not None else "" for h in next(rows)]
    except StopIteration:
        raise ValueError(f"{path}: empty worksheet")
    missing = SHEET_REQUIRED_COLS - set(header)
    if missing:
        raise ValueError(f"{path}: sheet is missing columns {sorted(missing)} "
                         f"(row 1 must be the header row)")
    out: list[Scenario] = []
    for i, values in enumerate(rows, start=2):
        row = {header[j]: (str(v).strip() if v is not None else "")
               for j, v in enumerate(values) if j < len(header) and header[j]}
        if not row.get("id"):
            continue
        out.append(_scenario_from_sheet_row(row, f"{path}:{i}"))
    if not out:
        raise ValueError(f"{path}: no scenario rows found")
    return out


def load_scenarios(path: str | Path, modality: str | None = None) -> list[Scenario]:
    """Load from a YAML file, a directory of YAML files, or a CSV file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"scenario path does not exist: {p}")
    if p.is_dir():
        scenarios = []
        for f in sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml")):
            scenarios.append(_scenario_from_yaml(f))
        for f in sorted(p.glob("*.csv")):
            scenarios.extend(_scenarios_from_csv(f))
        for f in sorted(p.glob("*.xlsx")):
            scenarios.extend(_scenarios_from_xlsx(f))
    elif p.suffix == ".csv":
        scenarios = _scenarios_from_csv(p)
    elif p.suffix == ".xlsx":
        scenarios = _scenarios_from_xlsx(p)
    else:
        scenarios = [_scenario_from_yaml(p)]

    if modality:
        scenarios = [s for s in scenarios if s.modality == modality]
    ids = [s.id for s in scenarios]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate scenario ids: {sorted(dupes)}")
    if not scenarios:
        raise ValueError(f"no scenarios loaded from {p}"
                         + (f" for modality {modality!r}" if modality else ""))
    return scenarios


# --------------------------------------------------------------------------
# Models config
# --------------------------------------------------------------------------

class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str                                 # per_image | per_token | per_1k_chars | per_minute
    usd: Optional[float] = None
    usd_per_1m: Optional[float] = None
    usd_in_per_1m: Optional[float] = None
    usd_out_per_1m: Optional[float] = None
    usd_audio_in_per_1m: Optional[float] = None
    est_usd_per_call: Optional[float] = None  # pre-flight budget only, never billing
    tier: Optional[str] = None
    source: str
    as_of: str

    @field_validator("as_of", mode="before")
    @classmethod
    def _date_to_str(cls, v):
        return str(v)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_concurrency: int = 2
    rpm: Optional[int] = None


class VertexCfg(BaseModel):
    """Vertex AI route: bills the named GCP project via ADC (gcloud auth
    application-default login) instead of an API key."""
    model_config = ConfigDict(extra="forbid")
    project: str
    location: str = "global"


class ModelCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    id: str
    display: Optional[str] = None   # human name for reports; id stays the key
    enabled: bool = False
    adapter: str
    provider: str
    provider_model: str
    auth_env: Optional[str] = None      # API-key route; omit when vertex: is set
    vertex: Optional[VertexCfg] = None  # ADC route; exactly one of the two
    supports: list[str]
    limits: Limits = Field(default_factory=Limits)
    params: dict = Field(default_factory=dict)
    voice_map: dict = Field(default_factory=dict)
    price: Price
    modality: str = ""    # filled by the loader from the lane the block sits in

    @field_validator("supports")
    @classmethod
    def _tasks_known(cls, v: list[str]) -> list[str]:
        for t in v:
            task_modality(t)  # raises on unknown task name
        return v

    @model_validator(mode="after")
    def _one_auth_route(self) -> "ModelCfg":
        if (self.auth_env is None) == (self.vertex is None):
            raise ValueError(
                f"model {self.id}: set exactly one of auth_env (API key) or "
                f"vertex: {{project, location}} (ADC)")
        return self


class JudgeCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    adapter: str
    provider: str
    provider_model: str
    auth_env: Optional[str] = None
    vertex: Optional[VertexCfg] = None
    temperature: float = 0.0
    price: Price

    @model_validator(mode="after")
    def _one_auth_route(self) -> "JudgeCfg":
        if (self.auth_env is None) == (self.vertex is None):
            raise ValueError(
                "judge: set exactly one of auth_env (API key) or "
                "vertex: {project, location} (ADC)")
        return self


class ModelsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    image: list[ModelCfg] = Field(default_factory=list)
    voice: list[ModelCfg] = Field(default_factory=list)
    judge: dict[str, JudgeCfg] = Field(default_factory=dict)
    asr: Optional[dict] = None


def load_models(path: str | Path) -> ModelsFile:
    p = Path(path)
    data = yaml.safe_load(p.read_text())
    mf = ModelsFile(**data)
    for lane in ("image", "voice"):
        for m in getattr(mf, lane):
            m.modality = lane
            for t in m.supports:
                if task_modality(t) != lane:
                    raise ValueError(
                        f"model {m.id}: supports task {t!r} which belongs to "
                        f"modality {task_modality(t)!r}, but the model sits in "
                        f"the {lane!r} lane")
    return mf


def enabled_models(mf: ModelsFile, modality: str) -> list[ModelCfg]:
    return [m for m in getattr(mf, modality) if m.enabled]


# --------------------------------------------------------------------------
# Rubrics — base per modality + small per-task override, hashed
# --------------------------------------------------------------------------

class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    weight: float
    judged_by: str                     # "judge" | "measured"
    description: str

    @field_validator("judged_by")
    @classmethod
    def _judged_by_known(cls, v: str) -> str:
        if v not in ("judge", "measured"):
            raise ValueError(f"judged_by must be 'judge' or 'measured', got {v!r}")
        return v


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    modality: str
    criteria: list[Criterion]
    rubric_hash: str = ""              # sha256 of the file bytes (base + override)
    source_files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _weights_sum(self) -> "Rubric":
        _check_weights({c.name: c.weight for c in self.criteria},
                       f"rubric ({self.modality})")
        return self

    def criterion(self, name: str) -> Criterion:
        for c in self.criteria:
            if c.name == name:
                return c
        raise KeyError(name)


def load_rubric(rubrics_dir: str | Path, modality: str, task: str) -> Rubric:
    """Base rubric per modality; a per-task override file replaces/extends it.

    The override (configs/rubrics/tasks/<task>.yaml) may redefine `criteria`
    wholesale — the plan's per-task rubrics are small, explicit files, not
    patches. The hash covers every file that contributed.
    """
    rubrics_dir = Path(rubrics_dir)
    base_path = rubrics_dir / f"{modality}.yaml"
    if not base_path.exists():
        raise FileNotFoundError(f"no base rubric for modality {modality!r}: {base_path}")
    raw = base_path.read_bytes()
    data = yaml.safe_load(raw)
    source_files = [str(base_path)]
    hash_input = raw

    override_path = rubrics_dir / "tasks" / f"{task}.yaml"
    if override_path.exists():
        oraw = override_path.read_bytes()
        odata = yaml.safe_load(oraw)
        if "criteria" in odata:
            data["criteria"] = odata["criteria"]
        source_files.append(str(override_path))
        hash_input += oraw

    rubric = Rubric(**data)
    rubric.rubric_hash = _sha256_bytes(hash_input)
    rubric.source_files = source_files
    return rubric


def effective_criteria(rubric: Rubric, scenario: Scenario) -> list[Criterion]:
    """Resolve the criteria + weights that actually apply to one scenario.

    Scenario-level criteria/weights override the rubric. A text_accuracy
    criterion with no must_read_text in the scenario is dropped and its
    weight redistributed proportionally (plan §12); the effective weights are
    recorded in the run manifest so nothing is silent.
    """
    if scenario.criteria is not None:
        crits = []
        for name in scenario.criteria:
            weight = scenario.weights[name]  # validated at load
            try:
                base = rubric.criterion(name)
                crits.append(base.model_copy(update={"weight": weight}))
            except KeyError:
                raise ValueError(
                    f"scenario {scenario.id}: criterion {name!r} is not defined "
                    f"in the {rubric.modality} rubric — criteria live in "
                    f"configs/rubrics/, scenarios may only re-weight them")
    else:
        crits = [c.model_copy() for c in rubric.criteria]

    def _drop(crits, name):
        kept = [c for c in crits if c.name != name]
        if len(kept) == len(crits):
            return crits
        dropped = next(c for c in crits if c.name == name)
        remaining = sum(c.weight for c in kept)
        if remaining <= 0:
            raise ValueError(f"scenario {scenario.id}: only {name} is weighted")
        for c in kept:
            c.weight = c.weight + dropped.weight * (c.weight / remaining)
        return kept

    if not scenario.checks.get("must_read_text"):
        crits = _drop(crits, "text_accuracy")
    if not scenario.checks.get("preservation"):
        # whole-image transforms declare no bound — the criterion is omitted
        # up front rather than surfacing as unmeasured noise
        crits = _drop(crits, "preservation")

    _check_weights({c.name: c.weight for c in crits},
                   f"scenario {scenario.id} effective weights")
    return crits


# --------------------------------------------------------------------------
# .env support — keys in .env (gitignored), loaded if present
# --------------------------------------------------------------------------

def load_dotenv(project_root: str | Path) -> None:
    import os
    env_path = Path(project_root) / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
