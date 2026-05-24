"""Postgres sink — pgvector chunks-table writer using the PostgresBackend dialect."""
from __future__ import annotations
import json
from typing import Any

import numpy as np
import psycopg
from psycopg import sql

from chunkshop.backends.base import ColSpec
from chunkshop.backends.postgres import PostgresBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig


def _jsonb_path_get(meta: dict, path: str):
    """Walk a dotted path through nested dicts; return None if any segment missing.

    Ported from sink.py — chunkshop-specific dict navigation, not SQL.
    """
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _canonical_cols(dim: int) -> list[ColSpec]:
    """The chunkshop-canonical chunks-table column list, PG-typed."""
    return [
        ColSpec("id", "text", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "text", nullable=False),
        ColSpec("seq_num", "int", nullable=False),
        ColSpec("original_content", "text", nullable=False),
        ColSpec("embedded_content", "text", nullable=False),
        ColSpec("tags", "text[]", nullable=False, default="'{}'"),
        ColSpec("metadata", "jsonb", nullable=False, default="'{}'"),
        ColSpec("embedding", f"vector({dim})", nullable=False),
        ColSpec("source", "text"),
        ColSpec("created_at", "timestamptz", nullable=False, default="now()"),
    ]


class PgSink:
    """Per-document writer to a Postgres chunks table.

    Wraps the canonical chunkshop data model (id/doc_id/seq_num/original_content/
    embedded_content/tags/metadata/embedding/source/created_at + promoted columns).
    Owns mode dispatch (overwrite/append/create_if_missing), foreign-tag safety,
    append preflight, source write-once, and delete_orphans. Delegates all
    dialect/connection/identifier work to PostgresBackend.
    """

    def __init__(self, cfg: TargetConfig, backend: PostgresBackend, embed_dim: int):
        self.cfg = cfg
        self.backend = backend
        self.embed_dim = embed_dim
        self._dsn = cfg.resolve_dsn()

    def _fq(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    def _row_id(self, doc_id: str, seq_num: int) -> str:
        return f"{doc_id}::{seq_num}"

    # -- create_table dispatch ----------------------------------------------
    def create_table(self) -> None:
        with self.backend.connect() as conn, conn.cursor() as cur:
            with self.backend.with_create_lock(cur, self.cfg.database_name):
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
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

        if self.cfg.fts and self.cfg.fts.enabled:
            self._ensure_or_validate_fts()

    def _ensure_or_validate_fts(self) -> None:
        schema = self.cfg.database_name
        idx = f"{self.cfg.table}_fts_idx"
        if self.cfg.mode == "append":
            with self.backend.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND indexname=%s",
                    (schema, idx),
                )
                if cur.fetchone() is None:
                    raise RuntimeError(
                        f"target.fts.enabled=true but {schema}.{self.cfg.table} has no "
                        f"FTS index ({idx}). Re-create the table with mode=overwrite or "
                        f"create_if_missing + fts.enabled, or remove target.fts."
                    )
            return
        from chunkshop.search import ensure_fts
        ensure_fts(self.cfg.resolve_dsn(), schema=schema,
                   table=self.cfg.table, language=self.cfg.fts.language,
                   include_metadata_paths=self.cfg.fts.include_metadata_paths)

    def _create_base_ddl(self, cur) -> None:
        for stmt in self.backend.emit_chunks_table_ddl(
            fq=self._fq(),
            cols=_canonical_cols(self.embed_dim),
            hnsw=self.cfg.hnsw,
            dim=self.embed_dim,
            vector_metric=self.cfg.vector_metric,
        ):
            cur.execute(stmt)
        self._ensure_promote_columns(cur)

    def _ensure_promote_columns(self, cur) -> None:
        for pc in self.cfg.promote_metadata:
            cur.execute(self.backend.add_column_if_not_exists_sql(
                self._fq(), pc.column_name, pc.type
            ))

    def _overwrite_create(self, cur) -> None:
        # Foreign-tag safety: refuse to drop a table holding rows from a different source_tag.
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            cur.execute(
                f"SELECT DISTINCT source FROM {self._fq()} WHERE source IS NOT NULL LIMIT 10"
            )
            existing_tags = {r[0] for r in cur.fetchall()}
            my_tag = self.cfg.source_tag
            foreign = existing_tags - ({my_tag} if my_tag else set())
            if foreign:
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.database_name}.{self.cfg.table}: "
                    f"table holds rows with source_tag values {sorted(foreign)!r} that differ "
                    f"from this cell's source_tag {my_tag!r}. Set target.force_overwrite: true "
                    f"in YAML to bypass."
                )
        if self._table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._fq()))
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "text"))
            self._ensure_promote_columns(cur)

    def _append_preflight(self, cur) -> None:
        if not self._table_exists(cur):
            raise RuntimeError(
                f"append mode: table {self.cfg.database_name}.{self.cfg.table} does not exist. "
                f"Use mode='create_if_missing' on the first cell."
            )
        current_dim = self.backend.embedding_dim(cur, self.cfg.database_name, self.cfg.table)
        if current_dim is None:
            raise RuntimeError(
                f"append mode: table {self.cfg.database_name}.{self.cfg.table} exists but has no "
                f"'embedding' vector column. This does not appear to be a chunkshop target table."
            )
        if current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target embedding dim is {current_dim}, cell's embedder dim is "
                f"{self.embed_dim}. Vectors are not comparable."
            )
        cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "text"))
        self._ensure_promote_columns(cur)

    def _table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.table)

    # -- write_document -----------------------------------------------------
    def write_document(
        self, doc_id: str, chunks: list[Chunk], embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch")
        if len(chunks) != len(tags_per_chunk):
            raise ValueError(f"chunks ({len(chunks)}) and tags ({len(tags_per_chunk)}) length mismatch")

        promote = self.cfg.promote_metadata
        base_col_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "embedding", "source",
        ]
        all_col_names = base_col_names + [pc.column_name for pc in promote]

        # update_cols: skip id/doc_id/seq_num AND source (source is write-once).
        update_cols = base_col_names[3:8] + [pc.column_name for pc in promote]
        upsert_sql = self.backend.upsert_clause(["id"], update_cols)

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in all_col_names)
        # PG-specific value placeholders: jsonb cast, vector cast
        placeholders = ["%s"] * 5 + ["%s", "%s::jsonb", "%s::vector", "%s"] + ["%s"] * len(promote)
        vals_sql = ", ".join(placeholders)

        stmt = f"INSERT INTO {self._fq()} ({cols_sql}) VALUES ({vals_sql}) {upsert_sql}"

        rows = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            base_values = [
                self._row_id(c.doc_id, c.seq_num),
                c.doc_id,
                c.seq_num,
                c.original_content,
                c.embedded_content,
                self.backend.tags_literal(tags),
                self.backend.json_literal(c.metadata),
                self.backend.vector_literal(emb),
                self.cfg.source_tag,
            ]
            promote_values = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            rows.append(tuple(base_values + promote_values))

        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.executemany(stmt, rows)
            if self.cfg.delete_orphans:
                cur.execute(
                    f"DELETE FROM {self._fq()} WHERE doc_id = %s AND seq_num >= %s",
                    (doc_id, len(chunks)),
                )
            conn.commit()

    def delete_document(self, doc_id: str) -> int:
        """Delete all chunks for a doc, scoped to this sink's source_tag if set."""
        with self.backend.connect() as conn, conn.cursor() as cur:
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
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(DISTINCT doc_id) FROM {self._fq()}")
            return cur.fetchone()[0]

    def query_top_k(
        self, query_vec: np.ndarray, k: int
    ) -> list[tuple[str, int, float]]:
        """pgvector top-K. Returns (doc_id, seq_num, operator-distance) tuples."""
        vec_lit = self.backend.vector_literal(query_vec)
        op, _opclass = self.backend.vector_metric_sql(self.cfg.vector_metric)
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT doc_id, seq_num, embedding {op} %s::vector AS distance "
                f"FROM {self._fq()} ORDER BY embedding {op} %s::vector LIMIT %s",
                (vec_lit, vec_lit, k),
            )
            return [(r[0], r[1], float(r[2])) for r in cur.fetchall()]
