"""Exception types shared by both owners.

These live in `contracts/` rather than in `core/` for an isolation reason:
OWNER B's settings profile denies reading `core/**` outright, and a deny rule
has no exceptions. Anything both halves genuinely need has to sit in the shared
package, or the boundary would have to be softened to accommodate it.

`core/errors.py` re-exports these alongside its own engine-specific types.
"""

from __future__ import annotations

#: Owner labels, used in stub messages and in `blast-radius stubs` output.
#: They match the handles in .github/CODEOWNERS.
OWNER_A = "A (etka)"
OWNER_B = "B (teammate)"


class BlastRadiusError(Exception):
    """Base class for every error this tool raises deliberately."""


class StubNotImplementedError(NotImplementedError):
    """A scaffolded boundary that has not been implemented yet.

    Subclasses NotImplementedError so that `except NotImplementedError` keeps
    working for callers that do not know about this type.

    The message names the module, the owner and the contract, because during a
    seven-day build most runs of the pipeline end in one of these and it is
    worth making them the most useful line on the screen.
    """

    def __init__(self, target: str, owner: str, contract: str) -> None:
        self.target = target
        self.owner = owner
        self.contract = contract
        super().__init__(f"{target} is not implemented (owner {owner}).\n  Contract: {contract}")
