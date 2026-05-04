"""ClickHouse backend (>=24.10 — vector_similarity experimental index required).

clickhouse-connect HTTP client. CH is append-only by design: supports_upsert=False,
upsert_clause() returns "" always. Re-ingest produces duplicate rows; users opt
in to ReplacingMergeTree(created_at) for lazy dedup at merge time.

Backend.connect() yields the clickhouse-connect Client directly — there is no
cursor abstraction. Sinks call client.command(sql) for DDL, client.query(sql)
for SELECT, and client.insert(table, rows, column_names=[...]) for batch insert.
The 'cur' parameter on Backend's introspection methods (table_exists,
embedding_dim, with_create_lock) is the client.
"""
from __future__ import annotations
import json
import os
import re
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import numpy as np


class ClickHouseBackend:
    """Backend Protocol implementation for ClickHouse 24.10+ (vector_similarity index)."""

    name: Literal["clickhouse"] = "clickhouse"
    supports_upsert: bool = False  # CH is append-only — no ON CONFLICT / ON DUPLICATE

    def __init__(self, dsn_env: str):
        self._dsn_env = dsn_env

    @contextmanager
    def connect(self) -> Iterator[Any]:
        import clickhouse_connect
        dsn = os.environ[self._dsn_env]
        kwargs = _parse_clickhouse_dsn(dsn)
        client = clickhouse_connect.get_client(**kwargs)
        try:
            yield client
        finally:
            client.close()

    def quote_ident(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    def fq_table(self, db: str, table: str) -> str:
        return f"{self.quote_ident(db)}.{self.quote_ident(table)}"

    def vector_type_ddl(self, dim: int) -> str:
        # Dim is encoded in the vector_similarity index spec, not the column type.
        # Array(Float32) is the storage type; the dim is enforced at index time.
        del dim
        return "Array(Float32)"

    def json_type_ddl(self) -> str:
        # Use String + JSONExtract*. CH has experimental JSON type but path
        # ergonomics are similar via JSONExtractString.
        return "String"

    def tags_array_type_ddl(self) -> str:
        return "Array(String)"

    def text_pk_type_ddl(self) -> str:
        return "String"

    def timestamp_now_default_ddl(self) -> str:
        return "DateTime64(6) DEFAULT now64()"

    def vector_literal(self, arr: "np.ndarray") -> list[float]:
        # clickhouse-connect accepts Python lists directly for Array(Float32) params
        return [float(x) for x in arr]

    def tags_literal(self, tags: list[str]) -> list[str]:
        # Native Array(String) — passthrough
        return list(tags)

    def json_literal(self, obj: Any) -> str:
        # metadata column is String; store as JSON-serialized text
        return json.dumps(obj)

    def json_path_sql(self, col_expr: str, dotted_path: str) -> str:
        # CH's JSONExtract* takes positional path segments, not jsonpath syntax.
        # JSONExtractString returns '' for missing paths; that matches the
        # "missing → null-ish" expectation upstream callers have.
        segs = dotted_path.split(".")
        args = ", ".join(f"'{s}'" for s in segs)
        return f"JSONExtractString({col_expr}, {args})"

    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str:
        # CH has no upsert. Sinks must INSERT-only. ReplacingMergeTree dedupes
        # at merge time (lazy, not visible until OPTIMIZE FINAL).
        del key_cols, update_cols
        return ""

    def create_database_sql(self, name: str) -> str:
        return f"CREATE DATABASE IF NOT EXISTS {self.quote_ident(name)}"

    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str:
        return f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {self.quote_ident(col)} {type_ddl}"

    def drop_table_sql(self, fq: str) -> str:
        # SYNC variant blocks until the table is fully dropped on this replica.
        # Important for overwrite mode so the subsequent CREATE doesn't race.
        return f"DROP TABLE IF EXISTS {fq} SYNC"

    def emit_chunks_table_ddl(
        self, fq: str, cols: list, hnsw: bool, dim: int, engine: str | None = None,
    ) -> list[str]:
        col_lines = []
        order_by_cols = []
        for c in cols:
            line = f"  {self.quote_ident(c.name)} {c.type_ddl}"
            if c.default is not None:
                line += f" DEFAULT {c.default}"
            # ClickHouse columns are nullable only via Nullable(T); we don't
            # use that here — non-default columns are required-by-convention.
            col_lines.append(line)
            if c.is_primary_key:
                order_by_cols.append(c.name)

        # Inline vector_similarity index (CH 24.10+, experimental flag enabled
        # via allow_experimental_vector_similarity_index=1 at profile level).
        # Dim is inferred from data — the 3-arg form was rejected in 24.10.4 with
        # "Vector similarity index must have two or five arguments". 2-arg
        # form: TYPE vector_similarity('hnsw', 'cosineDistance').
        del dim
        if hnsw:
            col_lines.append(
                "  INDEX vec_idx embedding TYPE vector_similarity('hnsw', 'cosineDistance') GRANULARITY 1"
            )
        # NOTE: do NOT add a bloom_filter skip-index on (doc_id, seq_num) here.
        # CH 24.10.4 has a bug where having both vector_similarity AND
        # bloom_filter indexes on the same table breaks client.insert with
        # "Expected data type Array(Float*)". chunkshop's CH access pattern is
        # vector queries (no filtered scans on doc_id), so the bloom_filter
        # is a nice-to-have we can skip without losing core functionality.

        body = ",\n".join(col_lines)
        # Default engine: MergeTree ORDER BY id (id is unique per chunk, so it's
        # fine as the sort key). User can override to ReplacingMergeTree(created_at)
        # for lazy dedup at merge time.
        if engine is None:
            order_by = ", ".join(self.quote_ident(c) for c in order_by_cols) or "tuple()"
            engine = f"MergeTree() ORDER BY ({order_by})"
        return [
            f"CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n) ENGINE = {engine}",
        ]

    def table_exists(self, cur: Any, db: str, table: str) -> bool:
        # `cur` here is the clickhouse-connect Client.
        result = cur.query(
            "SELECT count() FROM system.tables WHERE database = {db:String} AND name = {tbl:String}",
            parameters={"db": db, "tbl": table},
        )
        return result.result_rows[0][0] > 0

    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None:
        # CH 24.10's vector_similarity index doesn't bake the dim into the DDL
        # (it's inferred from data). Read dim from the first row's embedding length.
        # An empty table → None; that's an acceptable signal for append-mode preflight
        # (which requires rows to exist anyway).
        try:
            r = cur.query(f"SELECT length(embedding) FROM {self.fq_table(db, table)} LIMIT 1")
            if r.result_rows:
                return int(r.result_rows[0][0])
        except Exception:
            pass
        return None

    @contextmanager
    def with_create_lock(self, cur: Any, key: str) -> Iterator[None]:
        # CH serializes DDL natively (single-server) or via Keeper/ZK
        # (replicated). No app-level lock needed.
        del cur, key
        yield


def _parse_clickhouse_dsn(dsn: str) -> dict:
    """Parse clickhouse://user:pass@host:port/database into clickhouse-connect kwargs.

    Also accepts http:// and https:// — clickhouse-connect runs over HTTP. The
    `secure` flag is auto-set from the scheme.
    """
    from urllib.parse import urlparse, unquote
    parsed = urlparse(dsn)
    if parsed.scheme not in ("clickhouse", "http", "https", "clickhouse+http", "clickhouse+https"):
        raise ValueError(
            f"expected clickhouse:// or http(s):// DSN for ClickHouse, got {parsed.scheme!r}"
        )
    secure = parsed.scheme in ("https", "clickhouse+https")
    return {
        "host": parsed.hostname,
        "port": parsed.port or (8443 if secure else 8123),
        "username": (parsed.username and unquote(parsed.username)) or "default",
        "password": (parsed.password and unquote(parsed.password)) or "",
        "database": parsed.path.lstrip("/") or "default",
        "secure": secure,
    }
