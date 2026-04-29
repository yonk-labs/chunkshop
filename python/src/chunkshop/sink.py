"""pgvector sink: creates target table, upserts chunk rows per-document."""
from __future__ import annotations
import json
import os
import re

import numpy as np
import psycopg
from psycopg import sql

from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig


def _advisory_lock_key(schema_name: str) -> int:
    """Deterministic 64-bit signed int key for pg_advisory_xact_lock.

    Python's built-in hash() is PYTHONHASHSEED-randomized per process, so two
    concurrent cells would compute DIFFERENT keys for the same schema name and
    fail to serialize. blake2b gives a stable digest across processes.
    """
    import hashlib
    digest = hashlib.blake2b(schema_name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _jsonb_path_get(meta: dict, path: str):
    """Traverse a dotted path through nested dicts; return None if any segment missing.

    Example: _jsonb_path_get({"entities": {"ORG": ["x"]}}, "entities.ORG") == ["x"].
    """
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


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
            # Postgres CREATE EXTENSION / CREATE SCHEMA IF NOT EXISTS are not race-safe
            # across concurrent sessions (two sessions can both see it missing, both
            # attempt the create, one fails on the pg_namespace unique index). Serialize
            # via a transaction-scoped advisory lock keyed on the schema name.
            lock_key = _advisory_lock_key(self.cfg.schema_name)
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))

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
        """Return the declared dim of the `embedding` vector column, or None if the column
        does not exist on this table.

        Uses ``format_type(atttypid, atttypmod)`` which yields strings like ``vector(384)``
        regardless of pgvector version — robust to the atttypmod-plus-VARHDRSZ encoding
        variance that used to bite direct ``atttypmod`` reads. Works on empty tables.
        """
        cur.execute(
            """
            SELECT format_type(atttypid, atttypmod)
            FROM pg_attribute
            WHERE attrelid = (
                SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s AND n.nspname = %s
            ) AND attname = 'embedding'
            """,
            (self.cfg.table, self.cfg.schema_name),
        )
        r = cur.fetchone()
        if r is None:
            return None
        m = re.match(r"^vector\((\d+)\)$", r[0])
        return int(m.group(1)) if m else None

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
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            # Check if the existing table holds rows from a different source_tag.
            cur.execute(
                sql.SQL("SELECT DISTINCT source FROM {tbl} WHERE source IS NOT NULL LIMIT 10").format(
                    tbl=self._fq()
                )
            )
            existing_tags = {r[0] for r in cur.fetchall()}
            my_tag = self.cfg.source_tag
            foreign = existing_tags - ({my_tag} if my_tag else set())
            if foreign:
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.schema_name}.{self.cfg.table}: "
                    f"table holds rows with source_tag values {sorted(foreign)!r} that differ "
                    f"from this cell's source_tag {my_tag!r}. Set target.force_overwrite: true "
                    f"in YAML to bypass."
                )
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
        if current_dim is None:
            raise RuntimeError(
                f"append mode: table {self.cfg.schema_name}.{self.cfg.table} exists but has no "
                f"'embedding' vector column. This does not appear to be a chunkshop target "
                f"table — point at a different table or use mode='overwrite'."
            )
        if current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target embedding dim is {current_dim}, cell's embedder dim is "
                f"{self.embed_dim}. Vectors are not comparable. Use a different target table "
                f"or re-ingest into an overwrite-mode target."
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
        promote = self.cfg.promote_metadata

        # Column idents and value placeholders, in order.
        base_col_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "embedding", "source",
        ]
        base_cols = [sql.Identifier(n) for n in base_col_names]
        base_placeholders = [
            sql.SQL("%s"), sql.SQL("%s"), sql.SQL("%s"), sql.SQL("%s"), sql.SQL("%s"),
            sql.SQL("%s"), sql.SQL("%s::jsonb"), sql.SQL("%s::vector"), sql.SQL("%s"),
        ]
        promote_cols = [sql.Identifier(pc.column_name) for pc in promote]
        promote_placeholders = [sql.SQL("%s")] * len(promote)

        all_cols = base_cols + promote_cols
        all_placeholders = base_placeholders + promote_placeholders

        # ON CONFLICT DO UPDATE: refresh content, tags, metadata, embedding, and
        # promoted columns. Explicitly SKIP `source` (base_cols[8]) — source is
        # write-once, owned by the cell that created the row. Without this, a
        # later cell with the same (doc_id, seq_num) would silently clobber the
        # original cell's provenance, breaking multi-source filtering (SC-006).
        update_cols = base_cols[3:8] + promote_cols  # skip id/doc_id/seq_num AND source
        update_assignments = [
            sql.SQL("{c} = EXCLUDED.{c}").format(c=c) for c in update_cols
        ]

        stmt = sql.SQL(
            "INSERT INTO {tbl} ({cols}) VALUES ({vals}) "
            "ON CONFLICT (id) DO UPDATE SET {updates}"
        ).format(
            tbl=fq,
            cols=sql.SQL(", ").join(all_cols),
            vals=sql.SQL(", ").join(all_placeholders),
            updates=sql.SQL(", ").join(update_assignments),
        )

        rows = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            vec_literal = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
            base_values = [
                f"{c.doc_id}::{c.seq_num}",
                c.doc_id,
                c.seq_num,
                c.original_content,
                c.embedded_content,
                tags,
                json.dumps(c.metadata),
                vec_literal,
                self.cfg.source_tag,
            ]
            promote_values = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            rows.append(tuple(base_values + promote_values))

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.executemany(stmt, rows)
            if self.cfg.delete_orphans:
                # When a doc shrinks (e.g. last run wrote 12 chunks, this run writes 8),
                # chunks at seq_num >= len(chunks) are now orphaned. Delete them within
                # the same transaction so an interrupted write either applies the new
                # chunkset OR keeps the old one — never a half-overwritten doc.
                cur.execute(
                    sql.SQL(
                        "DELETE FROM {tbl} WHERE doc_id = %s AND seq_num >= %s"
                    ).format(tbl=fq),
                    (doc_id, len(chunks)),
                )
            conn.commit()

    def count_docs(self) -> int:
        stmt = sql.SQL("SELECT COUNT(DISTINCT doc_id) FROM {}").format(self._fq())
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(stmt)
            return cur.fetchone()[0]
