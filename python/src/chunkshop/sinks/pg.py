"""Postgres sink — pgvector chunks-table writer using the PostgresBackend dialect."""
from __future__ import annotations
import json
import math
from typing import Any

import numpy as np
import psycopg

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


def _document_cols() -> list[ColSpec]:
    return [
        ColSpec("doc_id", "text", nullable=False, is_primary_key=True),
        ColSpec("source", "text"),
        ColSpec("title", "text"),
        ColSpec("uri", "text"),
        ColSpec("source_path", "text"),
        ColSpec("content_hash", "text"),
        ColSpec("full_content", "text"),
        ColSpec("metadata", "jsonb", nullable=False, default="'{}'"),
        ColSpec("lede_summary", "text"),
        ColSpec("lede_toc", "jsonb", nullable=False, default="'[]'"),
        ColSpec("lede_facts", "jsonb", nullable=False, default="'[]'"),
        ColSpec("lede_report", "jsonb", nullable=False, default="'{}'"),
        ColSpec("lede_search_text", "text"),
        ColSpec("char_count", "int"),
        ColSpec("token_count_est", "int"),
        ColSpec("chunk_count", "int", nullable=False, default="0"),
        ColSpec("created_at", "timestamptz", nullable=False, default="now()"),
        ColSpec("updated_at", "timestamptz", nullable=False, default="now()"),
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
        self._pending_document_records: dict[str, dict[str, Any]] = {}

    def _fq(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    def _doc_fq(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.documents.table)

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
        if self.cfg.documents.enabled:
            self._ensure_document_table(cur)

    def _ensure_promote_columns(self, cur) -> None:
        for pc in self.cfg.promote_metadata:
            cur.execute(self.backend.add_column_if_not_exists_sql(
                self._fq(), pc.column_name, pc.type
            ))

    def _ensure_document_table(self, cur) -> None:
        for stmt in self.backend.emit_plain_table_ddl(
            fq=self._doc_fq(),
            cols=_document_cols(),
        ):
            cur.execute(stmt)
        for pc in self.cfg.documents.promote_metadata:
            cur.execute(self.backend.add_column_if_not_exists_sql(
                self._doc_fq(), pc.column_name, pc.type
            ))
        if self.cfg.documents.fts and self.cfg.documents.fts.enabled:
            cur.execute(
                f"ALTER TABLE {self._doc_fq()} ADD COLUMN IF NOT EXISTS search_vector tsvector "
                f"GENERATED ALWAYS AS ("
                f"setweight(to_tsvector('{self.cfg.documents.fts.language}', coalesce(title,'')), 'A') || "
                f"setweight(to_tsvector('{self.cfg.documents.fts.language}', coalesce(lede_summary,'')), 'B') || "
                f"setweight(to_tsvector('{self.cfg.documents.fts.language}', coalesce(lede_search_text,'')), 'C')"
                f") STORED"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS "
                f"{self.backend.quote_ident(self.cfg.documents.table + '_fts_idx')} "
                f"ON {self._doc_fq()} USING gin (search_vector)"
            )

    def _overwrite_create(self, cur) -> None:
        # Foreign-tag safety: refuse to drop a table holding rows from a different source_tag.
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            self._refuse_foreign_sources(cur, self._fq(), f"{self.cfg.database_name}.{self.cfg.table}")
        if (
            self.cfg.documents.enabled
            and self._document_table_exists(cur)
            and not self.cfg.force_overwrite
        ):
            self._refuse_foreign_sources(
                cur,
                self._doc_fq(),
                f"{self.cfg.database_name}.{self.cfg.documents.table}",
            )
        if self._table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._fq()))
        if self.cfg.documents.enabled and self._document_table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._doc_fq()))
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "text"))
            self._ensure_promote_columns(cur)
            if self.cfg.documents.enabled:
                self._ensure_document_table(cur)

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
        if self.cfg.documents.enabled:
            self._ensure_document_table(cur)

    def _table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.table)

    def _document_table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.documents.table)

    def _refuse_foreign_sources(self, cur, fq: str, label: str) -> None:
        try:
            cur.execute(f"SELECT DISTINCT source FROM {fq} WHERE source IS NOT NULL LIMIT 10")
        except Exception as exc:
            raise RuntimeError(
                f"overwrite refuses to drop {label}: could not verify source_tag safety. "
                "Set target.force_overwrite: true to bypass only if this table is disposable."
            ) from exc
        existing_tags = {r[0] for r in cur.fetchall()}
        my_tag = self.cfg.source_tag
        foreign = existing_tags - ({my_tag} if my_tag else set())
        if foreign:
            raise RuntimeError(
                f"overwrite refuses to drop {label}: table holds rows with source_tag values "
                f"{sorted(foreign)!r} that differ from this cell's source_tag {my_tag!r}. "
                "Set target.force_overwrite: true in YAML to bypass."
            )

    # -- write_document_record ---------------------------------------------
    def write_document_record(
        self,
        *,
        doc_id: str,
        title: str | None,
        content: str,
        metadata: dict[str, Any],
        chunk_count: int,
    ) -> None:
        """Upsert the optional document-level metadata row."""
        if not self.cfg.documents.enabled:
            return

        self._pending_document_records[doc_id] = {
            "doc_id": doc_id,
            "title": title,
            "content": content,
            "metadata": metadata,
            "chunk_count": chunk_count,
        }

    def _document_record_stmt(
        self,
        *,
        doc_id: str,
        title: str | None,
        content: str,
        metadata: dict[str, Any],
        chunk_count: int,
    ) -> tuple[str, tuple[Any, ...]]:
        lede_report = metadata.get("lede_report") if isinstance(metadata.get("lede_report"), dict) else {}
        attrs = lede_report.get("attributes") if isinstance(lede_report, dict) else {}
        lede_summary = lede_report.get("summary") if isinstance(lede_report, dict) else None
        lede_toc = lede_report.get("toc") if isinstance(lede_report.get("toc"), list) else []
        facts = []
        if isinstance(lede_report, dict):
            for key in ("key_facts", "fact_records"):
                values = lede_report.get(key) or []
                if isinstance(values, list):
                    facts.extend(values)
        lede_search_text = lede_report.get("search_text") if isinstance(lede_report, dict) else None

        def attr_value(name: str) -> Any:
            if isinstance(attrs, dict):
                rec = attrs.get(name)
                if isinstance(rec, dict):
                    return rec.get("value")
            return None

        full_content = content if self.cfg.documents.store_full_content else None
        report_payload = lede_report if self.cfg.documents.store_lede_report else {}
        doc_metadata = dict(metadata or {})
        if not self.cfg.documents.store_lede_report:
            doc_metadata.pop("lede_report", None)

        base_col_names = [
            "doc_id", "source", "title", "uri", "source_path", "content_hash",
            "full_content", "metadata", "lede_summary", "lede_toc", "lede_facts",
            "lede_report", "lede_search_text", "char_count", "token_count_est",
            "chunk_count",
        ]
        promote = self.cfg.documents.promote_metadata
        all_col_names = base_col_names + [pc.column_name for pc in promote]
        # Keep `source` write-once, matching chunk rows. `updated_at` is not an
        # inserted value, so it needs an explicit now() assignment on conflict.
        update_cols = base_col_names[2:] + [pc.column_name for pc in promote]
        update_set = ", ".join(
            f"{self.backend.quote_ident(c)} = EXCLUDED.{self.backend.quote_ident(c)}"
            for c in update_cols
        )
        upsert_sql = (
            f"ON CONFLICT ({self.backend.quote_ident('doc_id')}) DO UPDATE SET "
            f"{update_set}, {self.backend.quote_ident('updated_at')} = now()"
        )

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in all_col_names)
        placeholders = [
            "%s", "%s", "%s", "%s", "%s", "%s",
            "%s", "%s::jsonb", "%s", "%s::jsonb", "%s::jsonb",
            "%s::jsonb", "%s", "%s", "%s", "%s",
        ] + ["%s"] * len(promote)
        vals_sql = ", ".join(placeholders)
        stmt = f"INSERT INTO {self._doc_fq()} ({cols_sql}) VALUES ({vals_sql}) {upsert_sql}"

        base_values = [
            doc_id,
            self.cfg.source_tag,
            title,
            metadata.get("uri") or metadata.get("url") or attr_value("uri") or attr_value("url"),
            metadata.get("source_path") or metadata.get("path") or attr_value("source_path"),
            metadata.get("content_hash"),
            full_content,
            self.backend.json_literal(doc_metadata),
            lede_summary,
            self.backend.json_literal(lede_toc),
            self.backend.json_literal(facts),
            self.backend.json_literal(report_payload),
            lede_search_text,
            len(content),
            max(1, math.ceil(len(content) / 4)),
            chunk_count,
        ]
        promote_values = [_jsonb_path_get(metadata, pc.path) for pc in promote]

        return stmt, tuple(base_values + promote_values)

    def _execute_document_record(self, cur, record: dict[str, Any]) -> None:
        stmt, values = self._document_record_stmt(**record)
        cur.execute(stmt, values)

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

        pending_record = self._pending_document_records.pop(doc_id, None)
        try:
            with self.backend.connect() as conn, conn.cursor() as cur:
                if pending_record is not None:
                    self._execute_document_record(cur, pending_record)
                cur.executemany(stmt, rows)
                if self.cfg.delete_orphans:
                    cur.execute(
                        f"DELETE FROM {self._fq()} WHERE doc_id = %s AND seq_num >= %s",
                        (doc_id, len(chunks)),
                    )
                conn.commit()
        except Exception:
            if pending_record is not None:
                self._pending_document_records[doc_id] = pending_record
            raise

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
            if self.cfg.documents.enabled and self._document_table_exists(cur):
                if self.cfg.source_tag:
                    cur.execute(
                        f"DELETE FROM {self._doc_fq()} WHERE doc_id = %s AND source = %s",
                        (doc_id, self.cfg.source_tag),
                    )
                else:
                    cur.execute(f"DELETE FROM {self._doc_fq()} WHERE doc_id = %s", (doc_id,))
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
