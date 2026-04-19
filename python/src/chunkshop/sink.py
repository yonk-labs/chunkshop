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
        fq = self._fq()
        create_ext = sql.SQL("CREATE EXTENSION IF NOT EXISTS vector")
        create_schema = sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
            sql.Identifier(self.cfg.schema_name)
        )
        drop_if = sql.SQL("DROP TABLE IF EXISTS {}").format(fq)
        create_tbl = sql.SQL("""
            CREATE TABLE IF NOT EXISTS {tbl} (
                id text PRIMARY KEY,
                doc_id text NOT NULL,
                seq_num int NOT NULL,
                original_content text NOT NULL,
                embedded_content text NOT NULL,
                tags text[] NOT NULL DEFAULT '{{}}',
                metadata jsonb NOT NULL DEFAULT '{{}}',
                embedding vector({dim}) NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """).format(tbl=fq, dim=sql.Literal(self.embed_dim))
        create_doc_idx = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON {tbl} (doc_id, seq_num)"
        ).format(name=sql.Identifier(f"{self.cfg.table}_doc_seq_idx"), tbl=fq)
        create_hnsw = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON {tbl} "
            "USING hnsw (embedding vector_cosine_ops)"
        ).format(name=sql.Identifier(f"{self.cfg.table}_emb_hnsw_idx"), tbl=fq)

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(create_ext)
            cur.execute(create_schema)
            if self.cfg.overwrite:
                cur.execute(drop_if)
            cur.execute(create_tbl)
            cur.execute(create_doc_idx)
            if self.cfg.hnsw:
                cur.execute(create_hnsw)
            conn.commit()

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
