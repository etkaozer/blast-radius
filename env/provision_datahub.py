"""Provision the DataHub entities blast-radius writes into. OWNER B (@teammate).

DataHub rejects a structured-property assignment whose definition does not
exist yet, with a 422 naming the missing property. The definition is part of
the environment rather than part of a finding, so it is provisioned here once
instead of being created on the fly by core/writeback -- a review tool that
invents catalog schema while reviewing is a worse tool.

Idempotent: re-emitting the same aspect is an upsert.
"""

from __future__ import annotations

import os

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    StructuredPropertyDefinitionClass,
    TagPropertiesClass,
)

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")
PROPERTY_URN = "urn:li:structuredProperty:io.blastradius.impactRecord"
LEVELS = ("critical", "high", "medium", "low")


def main() -> None:
    emitter = DatahubRestEmitter(gms_server=GMS)

    definition = StructuredPropertyDefinitionClass(
        qualifiedName="io.blastradius.impactRecord",
        displayName="Blast Radius Impact Record",
        valueType="urn:li:dataType:datahub.string",
        cardinality="SINGLE",
        entityTypes=["urn:li:entityType:datahub.dataset"],
        description=(
            "Machine-readable blast-radius finding for the most recently "
            "reviewed change, stored as compact sorted JSON."
        ),
    )
    emitter.emit(MetadataChangeProposalWrapper(entityUrn=PROPERTY_URN, aspect=definition))
    print("provisioned", PROPERTY_URN)

    for level in LEVELS:
        urn = f"urn:li:tag:blast-radius-{level}"
        emitter.emit(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=TagPropertiesClass(
                    name=f"blast-radius-{level}",
                    description=f"blast-radius severity: {level}",
                ),
            )
        )
        print("provisioned", urn)


if __name__ == "__main__":
    main()
