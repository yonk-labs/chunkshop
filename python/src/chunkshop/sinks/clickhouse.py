"""ClickHouse sink — append-only chunks-table writer using ClickHouseBackend dialect.

ClickHouse has no upsert: re-ingesting the same (doc_id, seq_num) produces a
DUPLICATE row. Users who want dedup opt into ReplacingMergeTree(created_at) via
the cfg.engine field; merge-time dedup happens lazily, not on read (use FINAL
modifier or OPTIMIZE TABLE … FINAL to force).

The sink shape mirrors PgSink/MariaDbSink for the chunkshop-canonical data
model (modes, foreign-tag safety, append preflight, promote_metadata, source
write-once which falls out for free here since there's no UPDATE clause).

`delete_orphans` is a NO-OP on ClickHouse and emits a one-time warning per
process — same pattern as SqliteSink's HNSW warning. Reason: CH mutations
(`ALTER TABLE … DELETE WHERE …`) are async background ops that don't fit
chunkshop's per-document atomic write contract. Users who need orphan cleanup
should run a manual `ALTER TABLE … DELETE WHERE doc_id = … AND seq_num >= …`
or use ReplacingMergeTree with a `_deleted` flag column.
"""
from __future__ import annotations
import logging
import os
from typing import Any

import numpy as np

from chunkshop.backends.base import ColSpec
from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig

_log = logging.getLogger(__name__)
_DELETE_ORPHANS_WARNED: set[int] = set()  # PID-keyed: warn once per process
_APPEND_WITHOUT_REPLACING_WARNED: set[int] = set()


def _jsonb_path_get(meta: dict, path: str):
    """Walk a dotted path through nested dicts; return None if any segment missing."""
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _canonical_cols(dim: int) -> list[ColSpec]:
    """Canonical chunkshop columns, CH-typed."""
    return [
        ColSpec("id", "String", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "String", nullable=False),
        ColSpec("seq_num", "Int32", nullable=False),
        ColSpec("original_content", "String", nullable=False),
        ColSpec("embedded_content", "String", nullable=False),
        ColSpec("tags", "Array(String)", nullable=False, default="[]"),
        ColSpec("metadata", "String", nullable=False, default="'{}'"),
        ColSpec("embedding", "Array(Float32)", nullable=False),
        ColSpec("source", "String"),
        ColSpec("created_at", "DateTime64(6)", nullable=False, default="now64()"),
    ]


_PG_TO_CH_TYPE = {
    "text": "String",
    "text[]": "Array(String)",
    "int": "Int32",
    "bigint": "Int64",
    "boolean": "UInt8",
    "jsonb": "String",
    "timestamptz": "DateTime64(6)",
    "date": "Date",
}


def _pg_type_to_ch(pg_type: str) -> str:
    return _PG_TO_CH_TYPE.get(pg_type, pg_type)


class ClickHouseSink:
    """Per-document writer to a ClickHouse chunks table.

    Append-only by design. Mode dispatch:
      - overwrite: DROP TABLE SYNC + CREATE (foreign-tag safety check still applies)
      - append: preflight (table exists, dim matches), then INSERT
      - create_if_missing: CREATE IF NOT EXISTS, then INSERT
    """

    def __init__(self, cfg: TargetConfig, backend: ClickHouseBackend, embed_dim: int):
        self.cfg = cfg
        self.backend = backend
        self.embed_dim = embed_dim
        if cfg.delete_orphans:
            pid = os.getpid()
            if pid not in _DELETE_ORPHANS_WARNED:
                _DELETE_ORPHANS_WARNED.add(pid)
                _log.warning(
                    "target.delete_orphans=true on ClickHouse is a no-op — CH mutations "
                    "are async background ops that don't fit chunkshop's per-document "
                    "atomic write contract. Use ReplacingMergeTree(created_at) for "
                    "lazy dedup or run manual ALTER TABLE … DELETE WHERE."
                )
        # Warn once per process if mode=append against the default MergeTree
        # engine (no per-row UPSERT → re-ingest duplicates accumulate). Silent
        # on overwrite mode and on explicit ReplacingMergeTree engines.
        engine = getattr(cfg, "engine", None) or ""
        if cfg.mode == "append" and "ReplacingMergeTree" not in engine:
            pid = os.getpid()
            if pid not in _APPEND_WITHOUT_REPLACING_WARNED:
                _APPEND_WITHOUT_REPLACING_WARNED.add(pid)
                _log.warning(
                    "ClickHouse mode='append' on the default MergeTree engine "
                    "accumulates duplicate rows when the same (doc_id, seq_num) is "
                    "re-ingested. ClickHouse has no per-row UPSERT. Set "
                    "target.engine: 'ReplacingMergeTree(created_at) ORDER BY (id)' "
                    "for lazy dedup at merge time (run OPTIMIZE TABLE … FINAL to "
                    "force a merge), or use mode='overwrite' for fresh ingests."
                )

    def _fq(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    # -- create_table dispatch ----------------------------------------------
    def create_table(self) -> None:
        with self.backend.connect() as client:
            with self.backend.with_create_lock(client, self.cfg.database_name):
                client.command(self.backend.create_database_sql(self.cfg.database_name))
                if self.cfg.mode == "overwrite":
                    self._overwrite_create(client)
                elif self.cfg.mode == "append":
                    self._append_preflight(client)
                elif self.cfg.mode == "create_if_missing":
                    self._create_if_missing(client)
                else:
                    raise ValueError(f"unknown mode: {self.cfg.mode}")

        if self.cfg.fts and self.cfg.fts.enabled:
            self._ensure_or_validate_fts()

    def _ensure_or_validate_fts(self) -> None:
        schema = self.cfg.database_name
        index_name = "original_content_tok"
        if self.cfg.mode == "append":
            with self.backend.connect() as client:
                r = client.query(
                    "SELECT 1 FROM system.data_skipping_indices "
                    "WHERE database={db:String} AND table={tbl:String} AND name={idx:String}",
                    parameters={"db": schema, "tbl": self.cfg.table, "idx": index_name},
                )
                if not r.result_rows:
                    raise RuntimeError(
                        f"target.fts.enabled=true but {schema}.{self.cfg.table} has no "
                        f"tokenbf_v1 index ({index_name}). Re-create the table with "
                        f"mode=overwrite or create_if_missing + fts.enabled, or remove "
                        f"target.fts."
                    )
            return
        from chunkshop.search_clickhouse import ensure_fts
        ensure_fts(
            self.cfg.resolve_dsn(),
            schema=schema,
            table=self.cfg.table,
            language=self.cfg.fts.language,
        )

    def _create_base_ddl(self, client) -> None:
        engine = getattr(self.cfg, "engine", None)  # optional CH-only field
        for stmt in self.backend.emit_chunks_table_ddl(
            fq=self._fq(),
            cols=_canonical_cols(self.embed_dim),
            hnsw=self.cfg.hnsw,
            dim=self.embed_dim,
            engine=engine,
        ):
            client.command(stmt)
        self._ensure_promote_columns(client)

    def _ensure_promote_columns(self, client) -> None:
        for pc in self.cfg.promote_metadata:
            ch_type = _pg_type_to_ch(pc.type)
            client.command(
                self.backend.add_column_if_not_exists_sql(self._fq(), pc.column_name, ch_type)
            )

    def _overwrite_create(self, client) -> None:
        # Foreign-tag safety: refuse to drop a table holding rows from a different source_tag.
        if self._table_exists(client) and not self.cfg.force_overwrite:
            r = client.query(
                f"SELECT DISTINCT source FROM {self._fq()} WHERE source != '' LIMIT 10"
            )
            existing = {row[0] for row in r.result_rows}
            my_tag = self.cfg.source_tag
            foreign = existing - ({my_tag} if my_tag else set())
            if foreign:
                my_tag = self.cfg.source_tag
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.database_name}.{self.cfg.table}: "
                    f"table holds rows with source_tag values {sorted(foreign)!r} that differ "
                    f"from this cell's source_tag {my_tag!r}. Set target.force_overwrite: true "
                    f"in YAML to bypass."
                )
        if self._table_exists(client):
            client.command(self.backend.drop_table_sql(self._fq()))
        self._create_base_ddl(client)

    def _create_if_missing(self, client) -> None:
        if not self._table_exists(client):
            self._create_base_ddl(client)
        else:
            client.command(
                self.backend.add_column_if_not_exists_sql(self._fq(), "source", "String")
            )
            self._ensure_promote_columns(client)

    def _append_preflight(self, client) -> None:
        if not self._table_exists(client):
            raise RuntimeError(
                f"append mode: table {self.cfg.database_name}.{self.cfg.table} does not exist. "
                f"Use mode='create_if_missing' on the first cell."
            )
        current_dim = self.backend.embedding_dim(client, self.cfg.database_name, self.cfg.table)
        if current_dim is None:
            # Empty table — can't introspect dim. CH has no nullable column-level
            # dim contract; we trust the user that they're appending consistent vectors.
            _log.warning(
                "append mode on empty CH table — cannot verify embedding dim matches. "
                "Continuing on faith; subsequent reads with mismatched dim will produce "
                "garbage cosine distances."
            )
        elif current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target embedding dim is {current_dim}, cell's embedder dim is "
                f"{self.embed_dim}. Vectors are not comparable."
            )
        client.command(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "String"))
        self._ensure_promote_columns(client)

    def _table_exists(self, client) -> bool:
        return self.backend.table_exists(client, self.cfg.database_name, self.cfg.table)

    # -- write_document -----------------------------------------------------
    def write_document(
        self, doc_id: str, chunks: list[Chunk], embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings) or len(chunks) != len(tags_per_chunk):
            raise ValueError("chunks/embeddings/tags length mismatch")

        promote = self.cfg.promote_metadata
        column_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "embedding", "source",
        ] + [pc.column_name for pc in promote]
        # CH 24.10's vector_similarity index has a bug where client.insert auto-
        # inference rejects standard Array(Float32) data with "Expected data type
        # Array(Float*)". Passing column_type_names explicitly works around it.
        # Promoted columns map their PG type to the CH equivalent.
        column_type_names = [
            "String", "String", "Int32", "String", "String",
            "Array(String)", "String", "Array(Float32)", "String",
        ] + [_pg_type_to_ch(pc.type) for pc in promote]

        rows = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            chunk_id = f"{c.doc_id}::{c.seq_num}"
            base = [
                chunk_id,
                c.doc_id,
                c.seq_num,
                c.original_content,
                c.embedded_content,
                self.backend.tags_literal(tags),     # list[str] passthrough
                self.backend.json_literal(c.metadata),  # JSON string
                self.backend.vector_literal(emb),    # list[float]
                self.cfg.source_tag or "",           # CH String can't be NULL by default
            ]
            promoted = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            rows.append(base + promoted)

        with self.backend.connect() as client:
            client.insert(
                self._fq(), rows,
                column_names=column_names,
                column_type_names=column_type_names,
            )
            # delete_orphans is a no-op on CH (warned at __init__).
            # Re-ingesting the same doc produces dup rows; ReplacingMergeTree
            # dedupes lazily at merge time, not on read.

    def delete_document(self, doc_id: str) -> int:
        """ALTER TABLE … DELETE WHERE — async on CH, but unavoidable.

        Returns the number of rows that the DELETE statement targeted (CH
        reports affected rows from system.mutations after submission).
        """
        with self.backend.connect() as client:
            # Count first so we can return a meaningful number (sync).
            scope = "AND source = {tag:String}" if self.cfg.source_tag else ""
            params = {"doc_id": doc_id}
            if self.cfg.source_tag:
                params["tag"] = self.cfg.source_tag
            r = client.query(
                f"SELECT count() FROM {self._fq()} WHERE doc_id = {{doc_id:String}} {scope}",
                parameters=params,
            )
            n = int(r.result_rows[0][0]) if r.result_rows else 0
            if n == 0:
                return 0
            # Async; the ALTER is queued. The caller should accept eventual consistency.
            client.command(
                f"ALTER TABLE {self._fq()} DELETE WHERE doc_id = {{doc_id:String}} {scope}",
                parameters=params,
            )
            return n

    def count_docs(self) -> int:
        with self.backend.connect() as client:
            r = client.query(f"SELECT uniqExact(doc_id) FROM {self._fq()}")
            return int(r.result_rows[0][0]) if r.result_rows else 0

    def query_top_k(self, query_vec: np.ndarray, k: int) -> list[tuple[str, int, float]]:
        """Top-k chunks by cosine distance to query_vec.

        Uses cosineDistance(embedding, [...]) which accelerates via the
        vector_similarity index when present. Returns [(doc_id, seq_num, distance), ...]
        ordered by ascending distance.
        """
        vec = self.backend.vector_literal(query_vec)  # list[float]
        with self.backend.connect() as client:
            r = client.query(
                f"SELECT doc_id, seq_num, cosineDistance(embedding, {{q:Array(Float32)}}) AS dist "
                f"FROM {self._fq()} ORDER BY dist LIMIT {{k:UInt32}}",
                parameters={"q": vec, "k": k},
            )
            return [(row[0], int(row[1]), float(row[2])) for row in r.result_rows]
