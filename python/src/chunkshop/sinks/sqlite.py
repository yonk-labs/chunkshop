"""SQLite sink — chunks-table writer with two-table layout (chunks + chunks_vec).

The embedding column lives in a `vec0` virtual table joined on `id`. The Sink
owns the two-table dance: every `write_document` writes both atomically; every
`delete_orphans` deletes from both.
"""
from __future__ import annotations
import logging
import sqlite3
from typing import Any

import numpy as np

from chunkshop.backends.base import ColSpec
from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig

_log = logging.getLogger(__name__)
_HNSW_WARNED: set[int] = set()  # process-id keyed so we warn once per process


def _jsonb_path_get(meta: dict, path: str):
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _canonical_cols(dim: int) -> list[ColSpec]:
    """Canonical columns INCLUDING embedding — the backend splits it out for the vec0 table."""
    return [
        ColSpec("id", "TEXT", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "TEXT", nullable=False),
        ColSpec("seq_num", "INTEGER", nullable=False),
        ColSpec("original_content", "TEXT", nullable=False),
        ColSpec("embedded_content", "TEXT", nullable=False),
        ColSpec("tags", "TEXT", nullable=False, default="'[]'"),
        ColSpec("metadata", "TEXT", nullable=False, default="'{}'"),
        ColSpec("embedding", f"FLOAT[{dim}]", nullable=False),
        ColSpec("source", "TEXT"),
        ColSpec("created_at", "TEXT", nullable=False, default="CURRENT_TIMESTAMP"),
    ]


_SQLITE_TYPE = {
    "text": "TEXT", "text[]": "TEXT", "int": "INTEGER", "bigint": "INTEGER",
    "boolean": "INTEGER", "jsonb": "TEXT", "timestamptz": "TEXT", "date": "TEXT",
}


def _pg_type_to_sqlite(pg_type: str) -> str:
    return _SQLITE_TYPE.get(pg_type, pg_type)


class SqliteSink:
    """Per-document writer to a SQLite chunks-table pair."""

    def __init__(self, cfg: TargetConfig, backend: SQLiteBackend, embed_dim: int):
        self.cfg = cfg
        self.backend = backend
        self.embed_dim = embed_dim
        if cfg.hnsw:
            import os
            pid = os.getpid()
            if pid not in _HNSW_WARNED:
                _HNSW_WARNED.add(pid)
                _log.warning(
                    "target.hnsw=true on SQLite is a no-op — sqlite-vec uses brute-force KNN. "
                    "Querying with `embedding MATCH '[...]' AND k = N` works without an index."
                )

    def _fq_main(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    def _fq_vec(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table + "_vec")

    def create_table(self) -> None:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            with self.backend.with_create_lock(cur, self.cfg.database_name):
                # SELECT 1 noop on SQLite — emit anyway for symmetry
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
            fq=self._fq_main(),
            cols=_canonical_cols(self.embed_dim),
            hnsw=self.cfg.hnsw,
            dim=self.embed_dim,
        ):
            cur.execute(stmt)
        self._ensure_promote_columns(cur)

    def _ensure_promote_columns(self, cur) -> None:
        for pc in self.cfg.promote_metadata:
            try:
                cur.execute(self.backend.add_column_if_not_exists_sql(
                    self._fq_main(), pc.column_name, _pg_type_to_sqlite(pc.type),
                ))
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    continue
                raise

    def _overwrite_create(self, cur) -> None:
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            cur.execute(f"SELECT DISTINCT source FROM {self._fq_main()} WHERE source IS NOT NULL LIMIT 10")
            existing = {r[0] for r in cur.fetchall()}
            my_tag = self.cfg.source_tag
            foreign = existing - ({my_tag} if my_tag else set())
            if foreign:
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.table}: foreign source_tag {sorted(foreign)!r}"
                )
        if self._table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._fq_main()))
            cur.execute(f"DROP TABLE IF EXISTS {self._fq_vec()}")
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            try:
                cur.execute(self.backend.add_column_if_not_exists_sql(self._fq_main(), "source", "TEXT"))
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            self._ensure_promote_columns(cur)

    def _append_preflight(self, cur) -> None:
        if not self._table_exists(cur):
            raise RuntimeError(f"append mode: table {self.cfg.table} does not exist")
        current_dim = self.backend.embedding_dim(cur, self.cfg.database_name, self.cfg.table)
        if current_dim is None:
            raise RuntimeError(f"append mode: {self.cfg.table} has no vec0 partner table")
        if current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target dim {current_dim} != cell embed_dim {self.embed_dim}"
            )
        try:
            cur.execute(self.backend.add_column_if_not_exists_sql(self._fq_main(), "source", "TEXT"))
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        self._ensure_promote_columns(cur)

    def _table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.table)

    def write_document(
        self, doc_id: str, chunks: list[Chunk], embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings) or len(chunks) != len(tags_per_chunk):
            raise ValueError("chunks/embeddings/tags length mismatch")

        promote = self.cfg.promote_metadata
        # Main table cols (no embedding)
        main_col_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "source",
        ] + [pc.column_name for pc in promote]
        update_cols = ["original_content", "embedded_content", "tags", "metadata"] + [pc.column_name for pc in promote]
        # `source` excluded from update — write-once
        upsert = self.backend.upsert_clause(["id"], update_cols)
        cols_sql = ", ".join(self.backend.quote_ident(c) for c in main_col_names)
        placeholders = ", ".join(["?"] * len(main_col_names))
        main_stmt = f"INSERT INTO {self._fq_main()} ({cols_sql}) VALUES ({placeholders}) {upsert}"

        # Vec table — id + embedding. sqlite-vec's vec0 virtual table does NOT
        # support UPSERT (`UPSERT not implemented for virtual table`) nor
        # INSERT OR REPLACE (UNIQUE constraint failure). The working
        # re-write pattern is DELETE-by-id then INSERT.
        vec_delete_stmt = f"DELETE FROM {self._fq_vec()} WHERE id = ?"
        vec_insert_stmt = f"INSERT INTO {self._fq_vec()} (id, embedding) VALUES (?, ?)"

        main_rows = []
        vec_ids = []
        vec_rows = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            chunk_id = f"{c.doc_id}::{c.seq_num}"
            base = [
                chunk_id, c.doc_id, c.seq_num, c.original_content, c.embedded_content,
                self.backend.tags_literal(tags),
                self.backend.json_literal(c.metadata),
                self.cfg.source_tag,
            ]
            promoted = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            main_rows.append(tuple(base + promoted))
            vec_ids.append((chunk_id,))
            vec_rows.append((chunk_id, self.backend.vector_literal(emb)))

        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.executemany(main_stmt, main_rows)
            cur.executemany(vec_delete_stmt, vec_ids)
            cur.executemany(vec_insert_stmt, vec_rows)
            if self.cfg.delete_orphans:
                cur.execute(
                    f"DELETE FROM {self._fq_main()} WHERE doc_id = ? AND seq_num >= ?",
                    (doc_id, len(chunks)),
                )
                cur.execute(
                    f"DELETE FROM {self._fq_vec()} WHERE id LIKE ? || '::%' "
                    f"AND CAST(substr(id, instr(id, '::') + 2) AS INTEGER) >= ?",
                    (doc_id, len(chunks)),
                )
            conn.commit()

    def delete_document(self, doc_id: str) -> int:
        """Delete all chunks for a doc from BOTH chunks and chunks_vec tables.

        Scoped to source_tag when set. Two-phase: SELECT matching ids first
        (so vec table can be deleted by id IN (...)), then DELETE both atomically.
        """
        with self.backend.connect() as conn:
            cur = conn.cursor()
            if self.cfg.source_tag:
                cur.execute(
                    f"SELECT id FROM {self._fq_main()} WHERE doc_id = ? AND source = ?",
                    (doc_id, self.cfg.source_tag),
                )
            else:
                cur.execute(
                    f"SELECT id FROM {self._fq_main()} WHERE doc_id = ?",
                    (doc_id,),
                )
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            cur.execute(f"DELETE FROM {self._fq_main()} WHERE id IN ({placeholders})", ids)
            deleted = cur.rowcount
            cur.execute(f"DELETE FROM {self._fq_vec()} WHERE id IN ({placeholders})", ids)
            conn.commit()
        return deleted

    def count_docs(self) -> int:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(DISTINCT doc_id) FROM {self._fq_main()}")
            return cur.fetchone()[0]

    def query_top_k(
        self, query_vec: np.ndarray, k: int
    ) -> list[tuple[str, int, float]]:
        """sqlite-vec MATCH on the vec0 virtual table, joined back to the main
        table for doc_id / seq_num. The id column on chunks_vec mirrors the
        main table's id (format `doc_id::seq_num`).
        """
        vec_lit = self.backend.vector_literal(query_vec)  # JSON array string
        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT c.doc_id, c.seq_num, v.distance "
                f"FROM {self._fq_vec()} v "
                f"JOIN {self._fq_main()} c ON c.id = v.id "
                f"WHERE v.embedding MATCH ? AND k = ? "
                f"ORDER BY v.distance",
                (vec_lit, k),
            )
            return [(r[0], r[1], float(r[2])) for r in cur.fetchall()]
