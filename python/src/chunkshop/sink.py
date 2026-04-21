"""pgvector sink: creates target table, upserts chunk rows per-document."""
from __future__ import annotations
import json
import os

import numpy as np
import psycopg
from psycopg import sql

from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig


class PgVectorSink:
    """Per-document writer to a pgvector table.

    Each call to `write_document` opens a short-lived connection and commits
    one transaction, so `COUNT(DISTINCT doc_id)` gives live ingest progress
    from another session. Suitable for batch ingest; not a connection pool.
    """

    def __init__(self, cfg: TargetConfig, embed_dim: int):
        self.cfg = cfg
        self.embed_dim = embed_dim
        self._dsn = os.environ[cfg.dsn_env]

    def _fq(self) -> sql.Composed:
        return sql.SQL(".").join(
            [sql.Identifier(self.cfg.schema_name), sql.Identifier(self.cfg.table)]
        )

    def create_table(self) -> None:
        """Ensure target schema + table per ``cfg.mode``.

        Modes:
          - overwrite: DROP TABLE IF EXISTS (safety-checked in Task 12), then CREATE.
          - append:    require table to exist; pre-flight (dim, source col, promoted cols).
          - create_if_missing: CREATE IF NOT EXISTS; ensures source + promoted cols present.
        """
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS vector"))
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(self.cfg.schema_name)
            ))

            if self.cfg.mode == "overwrite":
                self._overwrite_create(cur)
            elif self.cfg.mode == "append":
                self._append_preflight(cur)
            elif self.cfg.mode == "create_if_missing":
                self._create_if_missing(cur)
            else:
                raise ValueError(f"unknown mode: {self.cfg.mode}")

            conn.commit()

    def _table_exists(self, cur) -> bool:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=%s AND tablename=%s)",
            (self.cfg.schema_name, self.cfg.table),
        )
        return cur.fetchone()[0]

    def _current_embed_dim(self, cur) -> int | None:
        """Returns the existing embedding vector dim, or None if column/table missing."""
        # Check whether the `embedding` column exists first
        cur.execute(
            """
            SELECT 1 FROM pg_attribute
            WHERE attrelid = (
                SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s AND n.nspname = %s
            ) AND attname = 'embedding'
            """,
            (self.cfg.table, self.cfg.schema_name),
        )
        if cur.fetchone() is None:
            return None
        # Use vector_dims() to get the declared dim — most reliable across pgvector versions
        try:
            cur.execute(
                sql.SQL("SELECT vector_dims(embedding) FROM {} LIMIT 1").format(self._fq())
            )
            r = cur.fetchone()
            if r is not None:
                return r[0]
        except Exception:
            pass
        # Fall back: the table might be empty. Inspect atttypmod on the column.
        cur.execute(
            """
            SELECT atttypmod FROM pg_attribute
            WHERE attrelid = (
                SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s AND n.nspname = %s
            ) AND attname = 'embedding'
            """,
            (self.cfg.table, self.cfg.schema_name),
        )
        r = cur.fetchone()
        return r[0] if r else None

    def _create_base_ddl(self, cur) -> None:
        fq = self._fq()
        cur.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {tbl} (
                id text PRIMARY KEY,
                doc_id text NOT NULL,
                seq_num int NOT NULL,
                original_content text NOT NULL,
                embedded_content text NOT NULL,
                tags text[] NOT NULL DEFAULT '{{}}',
                metadata jsonb NOT NULL DEFAULT '{{}}',
                embedding vector({dim}) NOT NULL,
                source text,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """).format(tbl=fq, dim=sql.Literal(self.embed_dim)))
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON {tbl} (doc_id, seq_num)"
        ).format(name=sql.Identifier(f"{self.cfg.table}_doc_seq_idx"), tbl=fq))
        if self.cfg.hnsw:
            cur.execute(sql.SQL(
                "CREATE INDEX IF NOT EXISTS {name} ON {tbl} "
                "USING hnsw (embedding vector_cosine_ops)"
            ).format(name=sql.Identifier(f"{self.cfg.table}_emb_hnsw_idx"), tbl=fq))
        self._ensure_promote_columns(cur)

    def _ensure_promote_columns(self, cur) -> None:
        fq = self._fq()
        for pc in self.cfg.promote_metadata:
            col_ident = sql.Identifier(pc.column_name)
            # pc.type is allowlisted in PromoteColumn._safe_type — safe to concatenate as literal
            cur.execute(
                sql.SQL("ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} " + pc.type).format(
                    tbl=fq, col=col_ident
                )
            )

    def _overwrite_create(self, cur) -> None:
        # Safety check (refuse foreign source_tag) lands in Task 12.
        if self._table_exists(cur):
            cur.execute(sql.SQL("DROP TABLE {}").format(self._fq()))
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            # Table exists — ensure source column + promoted columns present.
            cur.execute(
                sql.SQL("ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS source text").format(tbl=self._fq())
            )
            self._ensure_promote_columns(cur)

    def _append_preflight(self, cur) -> None:
        if not self._table_exists(cur):
            raise RuntimeError(
                f"append mode: table {self.cfg.schema_name}.{self.cfg.table} does not exist. "
                f"Use mode='create_if_missing' on the first cell."
            )
        current_dim = self._current_embed_dim(cur)
        if current_dim is not None and current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target embedding dim is {current_dim}, cell's embedder dim is "
                f"{self.embed_dim}. Vectors are not comparable."
            )
        # Ensure source column + promoted columns present.
        cur.execute(
            sql.SQL("ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS source text").format(tbl=self._fq())
        )
        self._ensure_promote_columns(cur)

    def write_document(
        self,
        doc_id: str,
        chunks: list[Chunk],
        embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch"
            )
        if len(chunks) != len(tags_per_chunk):
            raise ValueError(
                f"chunks ({len(chunks)}) and tags ({len(tags_per_chunk)}) length mismatch"
            )
        fq = self._fq()
        rows = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            vec_literal = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
            rows.append((
                f"{c.doc_id}::{c.seq_num}",
                c.doc_id,
                c.seq_num,
                c.original_content,
                c.embedded_content,
                tags,
                json.dumps(c.metadata),
                vec_literal,
            ))
        stmt = sql.SQL("""
            INSERT INTO {tbl}
                (id, doc_id, seq_num, original_content, embedded_content, tags, metadata, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (id) DO UPDATE SET
                original_content = EXCLUDED.original_content,
                embedded_content = EXCLUDED.embedded_content,
                tags = EXCLUDED.tags,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding
        """).format(tbl=fq)
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.executemany(stmt, rows)
            conn.commit()

    def count_docs(self) -> int:
        stmt = sql.SQL("SELECT COUNT(DISTINCT doc_id) FROM {}").format(self._fq())
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(stmt)
            return cur.fetchone()[0]
