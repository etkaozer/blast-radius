"""The frozen interface between the two halves of blast-radius.

`contracts/` holds three JSON Schemas, the typed models that mirror them, and the
golden fixtures both owners develop against. It is the only package both OWNER A
(`core/`, `skill/`) and OWNER B (`ci/`, `env/`) are allowed to import.

Changing anything here requires review from both owners. See CONTRACT.md.
"""

from contracts.canonical import canonical_json, sha256_of_json, sha256_of_text, untrusted_id
from contracts.loader import (
    ContractViolation,
    load_change_set,
    load_impact_report,
    load_schema,
    validate_instance,
)

__all__ = [
    "ContractViolation",
    "canonical_json",
    "load_change_set",
    "load_impact_report",
    "load_schema",
    "sha256_of_json",
    "sha256_of_text",
    "untrusted_id",
    "validate_instance",
]
