"""MariaDB sink — chunks-table writer using the MariaDBBackend dialect."""
from __future__ import annotations
import os
from typing import Any

import numpy as np

from chunkshop.backends.base import ColSpec
from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig


def _jsonb_path_get(meta: dict, path: str):
    """Same dict navigation as sinks/pg._jsonb_path_get."""
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _canonical_cols(dim: int) -> list[ColSpec]:
    return [
        ColSpec("id", "VARCHAR(255)", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "VARCHAR(255)", nullable=False),
        ColSpec("seq_num", "INT", nullable=False),
        ColSpec("original_content", "LONGTEXT", nullable=False),
        ColSpec("embedded_content", "LONGTEXT", nullable=False),
        ColSpec("tags", "JSON", nullable=False, default="(JSON_ARRAY())"),
        ColSpec("metadata", "JSON", nullable=False, default="(JSON_OBJECT())"),
        ColSpec("embedding", f"VECTOR({dim})", nullable=False),
        ColSpec("source", "VARCHAR(255)"),
        ColSpec("created_at", "TIMESTAMP", nullable=False, default="CURRENT_TIMESTAMP"),
    ]


class MariaDbSink:
    """Per-document writer to a MariaDB chunks table.

    Mirrors PgSink's contract on MariaDB. delete_orphans uses DELETE-in-transaction
    same as PG. Vector literal is composed inline (VEC_FromText) rather than
    parameter-bound, since MariaDB doesn't have a vector parameter type adapter.
    """

    def __init__(self, cfg: TargetConfig, backend: MariaDBBackend, embed_dim: int):
        self.cfg = cfg
        self.backend = backend
        self.embed_dim = embed_dim

    def _fq(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    # -- create_table dispatch ----------------------------------------------
    def create_table(self) -> None:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            with self.backend.with_create_lock(cur, self.cfg.database_name):
                cur.execute(self.backend.create_database_sql(self.cfg.database_name))
                if self.cfg.mode == "overwrite":
                    self._overwrite_create(cur)
                elif self.cfg.mode == "append":
                    self._append_preflight(cur)
                elif self.cfg.mode == "create_if_missing":
                    self._create_if_missing(cur)
                else:
                    raise ValueError(f"unknown mode: {self.cfg.mode}")
            conn.commit()

    def _create_base_ddl(self, cur) -> None:
        for stmt in self.backend.emit_chunks_table_ddl(
            fq=self._fq(),
            cols=_canonical_cols(self.embed_dim),
            hnsw=self.cfg.hnsw,
            dim=self.embed_dim,
        ):
            cur.execute(stmt)
        self._ensure_promote_columns(cur)

    def _ensure_promote_columns(self, cur) -> None:
        for pc in self.cfg.promote_metadata:
            mariadb_type = _pg_type_to_mariadb(pc.type)
            cur.execute(self.backend.add_column_if_not_exists_sql(
                self._fq(), pc.column_name, mariadb_type
            ))

    def _overwrite_create(self, cur) -> None:
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            cur.execute(f"SELECT DISTINCT source FROM {self._fq()} WHERE source IS NOT NULL LIMIT 10")
            existing_tags = {r[0] for r in cur.fetchall()}
            my_tag = self.cfg.source_tag
            foreign = existing_tags - ({my_tag} if my_tag else set())
            if foreign:
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.database_name}.{self.cfg.table}: "
                    f"foreign source_tag values {sorted(foreign)!r}"
                )
        if self._table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._fq()))
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "VARCHAR(255)"))
            self._ensure_promote_columns(cur)

    def _append_preflight(self, cur) -> None:
        if not self._table_exists(cur):
            raise RuntimeError(f"append mode: table {self._fq()} does not exist")
        current_dim = self.backend.embedding_dim(cur, self.cfg.database_name, self.cfg.table)
        if current_dim is None:
            raise RuntimeError(f"append mode: {self._fq()} has no embedding column")
        if current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target dim {current_dim} != cell embed_dim {self.embed_dim}"
            )
        cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "VARCHAR(255)"))
        self._ensure_promote_columns(cur)

    def _table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.table)

    # -- write_document -----------------------------------------------------
    def write_document(
        self, doc_id: str, chunks: list[Chunk], embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks / embeddings length mismatch")
        if len(chunks) != len(tags_per_chunk):
            raise ValueError(f"chunks / tags length mismatch")

        promote = self.cfg.promote_metadata
        base_col_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "embedding", "source",
        ]
        all_col_names = base_col_names + [pc.column_name for pc in promote]
        # update_cols: skip id/doc_id/seq_num AND source (write-once on conflict)
        update_cols = base_col_names[3:8] + [pc.column_name for pc in promote]
        upsert_sql = self.backend.upsert_clause([], update_cols)

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in all_col_names)
        # Vector literal goes INLINE as VEC_FromText(...); other values via %s
        # PyMySQL placeholder is %s as well.
        non_vec_count = len(all_col_names) - 1  # everything except embedding
        # Build placeholders in the same column order; embedding slot = literal
        placeholders = []
        for c in all_col_names:
            if c == "embedding":
                placeholders.append("__VEC_PLACEHOLDER__")  # replaced per-row
            else:
                placeholders.append("%s")
        vals_sql_template = ", ".join(placeholders)

        rows = []
        sql_per_row = []  # one SQL string per row (because vector literal is inline)
        params_per_row = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            vec_expr = self.backend.vector_literal(emb)  # "VEC_FromText('[…]')"
            row_vals_sql = vals_sql_template.replace("__VEC_PLACEHOLDER__", vec_expr)
            row_sql = (
                f"INSERT INTO {self._fq()} ({cols_sql}) VALUES ({row_vals_sql}) {upsert_sql}"
            )
            base_params = [
                f"{c.doc_id}::{c.seq_num}",
                c.doc_id,
                c.seq_num,
                c.original_content,
                c.embedded_content,
                self.backend.tags_literal(tags),
                self.backend.json_literal(c.metadata),
                # embedding handled inline above
                self.cfg.source_tag,
            ]
            promote_params = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            sql_per_row.append(row_sql)
            params_per_row.append(tuple(base_params + promote_params))

        with self.backend.connect() as conn:
            cur = conn.cursor()
            for sql_, params in zip(sql_per_row, params_per_row):
                cur.execute(sql_, params)
            if self.cfg.delete_orphans:
                cur.execute(
                    f"DELETE FROM {self._fq()} WHERE doc_id = %s AND seq_num >= %s",
                    (doc_id, len(chunks)),
                )
            conn.commit()

    def delete_document(self, doc_id: str) -> int:
        """Delete all chunks for a doc, scoped to this sink's source_tag if set."""
        with self.backend.connect() as conn:
            cur = conn.cursor()
            if self.cfg.source_tag:
                cur.execute(
                    f"DELETE FROM {self._fq()} WHERE doc_id = %s AND source = %s",
                    (doc_id, self.cfg.source_tag),
                )
            else:
                cur.execute(f"DELETE FROM {self._fq()} WHERE doc_id = %s", (doc_id,))
            deleted = cur.rowcount
            conn.commit()
        return deleted

    def count_docs(self) -> int:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(DISTINCT doc_id) FROM {self._fq()}")
            return cur.fetchone()[0]

    def query_top_k(
        self, query_vec: np.ndarray, k: int
    ) -> list[tuple[str, int, float]]:
        """MariaDB top-K nearest by cosine distance.

        Implementation note: MariaDB's `VECTOR INDEX` (HNSW) only accelerates
        `VEC_DISTANCE_EUCLIDEAN`, not `VEC_DISTANCE_COSINE`. Since chunkshop's
        embeddings are L2-normalized (fastembed/BGE/Nomic all normalize), the
        ranking by euclidean is mathematically identical to ranking by cosine
        (eucl² = 2·cos_dist for unit vectors). HNSW also makes the result
        approximate — top-K may differ slightly from an exact cosine scan.

        We use the **hybrid shape**: euclidean in ORDER BY (index-accelerated),
        cosine in SELECT (the reported `distance` matches what users get on
        the other 3 backends, and matches what chunkshop wrote pre-v0.4.1).

        Drops query latency from ~150ms to ~1-2ms at 8k chunks (180×).
        Requires `target.hnsw: true` on the cell so the VECTOR INDEX exists;
        without the index, this query is the same brute-force scan as before.
        """
        vec_expr = self.backend.vector_literal(query_vec)  # "VEC_FromText('[…]')"
        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT doc_id, seq_num, "
                f"VEC_DISTANCE_COSINE(embedding, {vec_expr}) AS distance "
                f"FROM {self._fq()} "
                f"ORDER BY VEC_DISTANCE_EUCLIDEAN(embedding, {vec_expr}) "
                f"LIMIT %s",
                (k,),
            )
            return [(r[0], r[1], float(r[2])) for r in cur.fetchall()]


_PG_TO_MARIADB_TYPE = {
    "text": "TEXT",
    "text[]": "JSON",      # MariaDB has no native array
    "int": "INT",
    "bigint": "BIGINT",
    "boolean": "BOOLEAN",
    "jsonb": "JSON",
    "timestamptz": "TIMESTAMP",
    "date": "DATE",
}


def _pg_type_to_mariadb(pg_type: str) -> str:
    """Translate a PromoteColumn.type (PG-flavored) to MariaDB column type DDL."""
    return _PG_TO_MARIADB_TYPE.get(pg_type, pg_type)
