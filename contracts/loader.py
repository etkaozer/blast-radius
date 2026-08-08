"""Load and validate contract payloads.

Every read and every write of a contract payload goes through this module, so
that "validated against the JSON Schema" is not a convention someone has to
remember but the only available code path. Validation is two-layered on
purpose:

1. the JSON Schema, which is the authority and is language independent, so a
   future non-Python consumer sees exactly the same contract;
2. the pydantic model, which adds the invariants a schema cannot express.

Both layers run in both directions. A payload that passes one but not the other
is a bug in `contracts/`, and the parity test in `contracts/tests/` will say so.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from contracts.canonical import sha256_of_json
from contracts.models import ChangeSet, ImpactReport, WritebackRecord

SchemaName = Literal["change_set", "impact_report", "writeback_record"]

CONTRACTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"

SCHEMA_FILES: dict[str, str] = {
    "change_set": "change_set.schema.json",
    "impact_report": "impact_report.schema.json",
    "writeback_record": "writeback_record.schema.json",
}


class ContractViolation(Exception):  # noqa: N818 - the name reads better at call sites
    """A payload does not satisfy its contract.

    Carries every error found rather than the first, because the usual reader is
    the other owner debugging why their output was rejected, and one round trip
    per error would be a bad way to spend a hackathon.
    """

    def __init__(self, schema_name: str, errors: list[str], source: Path | None = None) -> None:
        self.schema_name = schema_name
        self.errors = errors
        self.source = source
        location = f" in {source}" if source else ""
        detail = "\n".join(f"  - {e}" for e in errors)
        super().__init__(
            f"{len(errors)} contract violation(s) against {schema_name}.schema.json"
            f"{location}:\n{detail}"
        )


@cache
def load_schema(name: str) -> dict[str, Any]:
    """Return the parsed JSON Schema named `name` (without the .schema.json suffix)."""
    try:
        filename = SCHEMA_FILES[name]
    except KeyError as exc:
        known = ", ".join(sorted(SCHEMA_FILES))
        msg = f"unknown schema {name!r}; known schemas: {known}"
        raise KeyError(msg) from exc
    with (CONTRACTS_DIR / filename).open(encoding="utf-8") as fh:
        parsed: dict[str, Any] = json.load(fh)
    return parsed


@cache
def validator_for(name: str) -> Draft202012Validator:
    """Return a format-checking validator for the named schema."""
    return Draft202012Validator(
        load_schema(name), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def validate_instance(instance: Any, name: str, source: Path | None = None) -> None:
    """Validate `instance` against the named JSON Schema, or raise ContractViolation."""
    errors = sorted(validator_for(name).iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        messages = [
            f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors
        ]
        raise ContractViolation(name, messages, source)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _parse(model: type[Any], payload: Any, name: str, source: Path | None) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        messages = [
            f"{'/'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise ContractViolation(name, messages, source) from exc


def load_change_set(path: Path) -> ChangeSet:
    """Read, schema-validate and parse a ChangeSet produced by OWNER B."""
    payload = _read_json(path)
    validate_instance(payload, "change_set", path)
    result: ChangeSet = _parse(ChangeSet, payload, "change_set", path)
    return result


def load_impact_report(path: Path) -> ImpactReport:
    """Read, schema-validate and parse an ImpactReport produced by OWNER A."""
    payload = _read_json(path)
    validate_instance(payload, "impact_report", path)
    result: ImpactReport = _parse(ImpactReport, payload, "impact_report", path)
    return result


def load_writeback_record(path: Path) -> WritebackRecord:
    """Read, schema-validate and parse a WritebackRecord."""
    payload = _read_json(path)
    validate_instance(payload, "writeback_record", path)
    result: WritebackRecord = _parse(WritebackRecord, payload, "writeback_record", path)
    return result


def _prune_value(attribute: Any, value: Any) -> Any:
    """Recurse into `value`, pruning optional nulls, guided by the live model tree."""
    if isinstance(attribute, BaseModel) and isinstance(value, dict):
        return _prune_optional_nulls(attribute, value)
    if isinstance(attribute, list | tuple) and isinstance(value, list):
        return [_prune_value(item, dumped) for item, dumped in zip(attribute, value, strict=False)]
    if isinstance(attribute, dict) and isinstance(value, dict):
        return {key: _prune_value(attribute.get(key), dumped) for key, dumped in value.items()}
    return value


def _prune_optional_nulls(model: BaseModel, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop null-valued OPTIONAL fields, keeping null-valued REQUIRED ones.

    `model_dump(exclude_none=True)` cannot express this distinction: it drops
    every null, including those the schema requires the key for. A field that is
    required and nullable — `severityFactor.raw_value` is currently the only one
    across the three schemas — then vanishes from the payload and the report
    fails its own contract. That is not a rare shape: `raw_value` is null for
    `hop_proximity` when nothing is downstream and for `query_usage` when usage
    was never ingested, so an ordinary column on an ordinary catalog emitted a
    report `contracts/loader.py` itself would refuse to load.

    Pydantic's required/optional split is the right authority here rather than a
    hard-coded field list, because `contracts/models.py` mirrors the schemas
    field for field: a field with no default is exactly one the schema lists in
    `required`. A future required-and-nullable field is therefore handled the day
    it is added, with no change here.
    """
    pruned: dict[str, Any] = {}
    for name, field in type(model).model_fields.items():
        key = field.alias or name
        if key not in payload:
            continue
        value = payload[key]
        if value is None and not field.is_required():
            continue
        pruned[key] = _prune_value(getattr(model, name, None), value)
    return pruned


def to_payload(model: ChangeSet | ImpactReport | WritebackRecord) -> dict[str, Any]:
    """Serialise a contract model to plain JSON types, dropping unset optionals.

    Required fields survive even when null; see `_prune_optional_nulls`.
    """
    return _prune_optional_nulls(model, model.model_dump(mode="json"))


def dump(model: ChangeSet | ImpactReport | WritebackRecord, path: Path, name: str) -> Path:
    """Validate `model` against its schema and write it to `path`.

    Validation happens before the file exists, so an invalid payload never
    reaches the other owner's tooling.
    """
    payload = to_payload(model)
    validate_instance(payload, name, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    return path


def change_set_digest(change_set: ChangeSet) -> str:
    """Return the sha256 that pins an ImpactReport to the ChangeSet it came from."""
    return sha256_of_json(to_payload(change_set))


def fixture_dir(name: str) -> Path:
    """Return the directory of the golden fixture called `name`, e.g. '01_rename'."""
    path = FIXTURES_DIR / name
    if not path.is_dir():
        available = ", ".join(sorted(p.name for p in FIXTURES_DIR.iterdir() if p.is_dir()))
        msg = f"unknown fixture {name!r}; available: {available}"
        raise FileNotFoundError(msg)
    return path


def iter_fixture_dirs() -> list[Path]:
    """Return every golden fixture directory, in name order."""
    return sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
