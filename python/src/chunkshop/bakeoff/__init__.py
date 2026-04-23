"""chunkshop bakeoff: `chunkshop bakeoff` CLI subcommand + library surface.

Promotes the `scripts/bench_matrix.py` hacking tool into a user-facing,
config-driven chunker x embedder matrix evaluation that emits a leaderboard +
a runnable `recommended.yaml` cell.
"""
from chunkshop.bakeoff.config import (
    BakeoffConfig,
    BakeoffResults,
    BakeoffTargetConfig,
    ComboResult,
    GoldQuery,
    MatrixConfig,
    ScoringConfig,
)

__all__ = [
    "BakeoffConfig",
    "BakeoffResults",
    "BakeoffTargetConfig",
    "ComboResult",
    "GoldQuery",
    "MatrixConfig",
    "ScoringConfig",
]
