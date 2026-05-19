"""chunkshop agent-memory staging API.

The only 'live' touchpoint: an external capture layer pushes session events
into a chunkshop-owned append-only staging table. Two scheduled cells
(memory/realtime.yaml, memory/consolidate.yaml) consume it. This module is
deliberately NOT chunkshop.Pipeline (which requires an inline source).
"""
from chunkshop.memory.staging import (
    stage_event, stage_events, ensure_staging_table, prune_staging,
)

__all__ = ["stage_event", "stage_events", "ensure_staging_table", "prune_staging"]
