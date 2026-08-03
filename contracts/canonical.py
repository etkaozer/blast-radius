"""Canonical serialisation and content addressing.

Both owners need to agree, byte for byte, on how a payload is hashed: OWNER B
stamps `untrusted_text.id` at extraction time and OWNER A re-derives it when
building prompt delimiters. If the two disagreed, the untrusted envelope would
be forgeable. Keeping the single implementation here removes that risk.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

UNTRUSTED_ID_PREFIX = "ut-"
UNTRUSTED_ID_HEX_LEN = 12


def canonical_json(value: Any) -> str:
    """Serialise `value` deterministically: sorted keys, no insignificant whitespace.

    Used for digests that must be reproducible across machines and Python runs.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of_text(text: str) -> str:
    """Return the hex sha256 of `text` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_json(value: Any) -> str:
    """Return the hex sha256 of the canonical JSON form of `value`."""
    return sha256_of_text(canonical_json(value))


def untrusted_id(value: str) -> str:
    """Return the content-addressed id for a piece of untrusted free text.

    The id is derived from the content rather than assigned by the producer, and
    it is reused as the nonce in the prompt delimiter around that content (see
    `core/untrusted/envelope.py`). Because the delimiter contains a prefix of
    sha256(value), text that closes its own envelope would have to embed a
    prefix of its own hash, which is a preimage problem rather than a quoting
    problem.
    """
    return f"{UNTRUSTED_ID_PREFIX}{sha256_of_text(value)[:UNTRUSTED_ID_HEX_LEN]}"
