"""chunkshop bakeoff: `chunkshop bakeoff` CLI subcommand + library surface.

v4 multi-backend: a bakeoff config is a corpus + a chunker × embedder matrix
× a list of database backends. Every (backend, chunker, embedder) cell ingests
the corpus and is queried via the sink's native vector syntax. The leaderboard
is rendered side-by-side per backend.
"""
from chunkshop.bakeoff.config import (
    BakeoffConfig,
    BakeoffResults,
    BakeoffTarget,
    ComboResult,
    GoldQuery,
    MariadbBakeoffTarget,
    MatrixConfig,
    PostgresBakeoffTarget,
    ScoringConfig,
    SqliteBakeoffTarget,
)

__all__ = [
    "BakeoffConfig",
    "BakeoffResults",
    "BakeoffTarget",
    "ComboResult",
    "GoldQuery",
    "MariadbBakeoffTarget",
    "MatrixConfig",
    "PostgresBakeoffTarget",
    "ScoringConfig",
    "SqliteBakeoffTarget",
]
