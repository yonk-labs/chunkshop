"""Read SP-A's `agent_memory.memory` table in the shape pg-raggraph's
`GraphRAG.ingest_records()` accepts. Closes the loop chunkshop SP-A opened
on the write side: consumers can `pip install chunkshop` (no pg-raggraph
dep), call `read_pre_chunked(dsn)`, and feed the result straight into
pg-raggraph elsewhere — bridge logic lives with the schema owner.

Defaults enforce SP-A operational invariant O2 (consolidated-wins) by
selecting `tier='consolidated'` and excluding rows with `retracted=true`.
"""
from __future__ import annotations

from typing import Iterable, Iterator
import psycopg


def _parse_vector(v) -> list[float]:
    """pgvector returns its `vector` type as a string like '[0.1,0.2,...]'
    when no pgvector-python adapter is registered. Parse without adding
    a runtime dep."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [float(x) for x in v]
    s = str(v).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    if not s:
        return []
    return [float(x) for x in s.split(",")]


def read_pre_chunked(
    dsn: str,
    *,
    schema: str = "agent_memory",
    table: str = "memory",
    namespace: str | None = None,
    tier: str = "consolidated",
    include_retracted: bool = False,
    session_ids: Iterable[str] | None = None,
) -> Iterator[dict]:
    """Yield one pg-raggraph ingest_records record per session.

    Each yielded dict matches pg-raggraph's expected shape:

        {
            "text":                 "<reconstructed session text>",
            "source_id":            "memory:{namespace}:{session_id}",
            "metadata":             {session_id, namespace, tier, recorded_at, ...},
            "pre_chunked":          [{content, embedded_content, embedding,
                                      metadata, token_count?}, ...],
            "known_entities":       [{"name": ..., "entity_type": "memory_entity"},
                                     ...],   # synthesized from fact subjects/objects
            "known_relationships":  [{"src": subject, "dst": object,
                                      "rel_type": predicate, "description":
                                      support_span, "weight": confidence?}, ...],
            "skip_llm":             True,                # facts already extracted
        }

    Args:
        dsn: Postgres DSN where SP-A wrote the memory table.
        schema, table: defaults match chunkshop's memory preset
            (`agent_memory.memory`).
        namespace: filter to one tenant; None = all namespaces.
        tier: O2 default `'consolidated'`. Pass `'provisional'` or
            `None` (no filter) to override.
        include_retracted: O2 default False — drop `retracted=true` rows.
        session_ids: optional explicit list of session_ids to fetch.

    Notes:
        - Episode rows (`kind='episode'`) become `pre_chunked` entries.
        - Fact rows (`kind='fact'`) become `known_relationships` (+ the
          subjects/objects synthesised into `known_entities`). pg-raggraph
          merges these into its graph layer through normal entity
          resolution — no new pg-raggraph seam required.
        - `skip_llm=True` because SP-A's consolidation step already did
          the extraction; running pg-raggraph's LLM extractor again would
          duplicate effort and add noise.
    """
    where = ["1=1"]
    params: list[object] = []
    if namespace is not None:
        where.append("namespace = %s")
        params.append(namespace)
    if tier is not None:
        where.append("tier = %s")
        params.append(tier)
    if not include_retracted:
        where.append("coalesce(retracted, false) = false")
    if session_ids is not None:
        sids = list(session_ids)
        if not sids:
            return
        where.append("doc_id = ANY(%s)")
        params.append(sids)

    q = (
        f"SELECT doc_id, seq_num, kind, original_content, embedded_content, "
        f"  embedding, metadata, namespace, tier, recorded_at, "
        f"  subject, predicate, object, support_span, confidence, "
        f"  effective_from, retracted "
        f"FROM {schema}.{table} "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY doc_id, seq_num"
    )

    sessions: dict[str, dict] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(q, params)
        for row in cur.fetchall():
            (doc_id, seq_num, kind, original, embedded, embedding, meta,
             ns, row_tier, recorded_at,
             subject, predicate, obj, support_span, confidence,
             effective_from, retracted) = row
            sess = sessions.setdefault(doc_id, {
                "text_parts": [],
                "pre_chunked": [],
                "known_entities": [],
                "known_relationships": [],
                "metadata": {"session_id": doc_id, "namespace": ns,
                             "tier": row_tier, "recorded_at": recorded_at},
            })
            chunk_meta = dict(meta or {})
            chunk_meta.setdefault("kind", kind)
            chunk_meta.setdefault("seq_num", seq_num)
            if kind == "episode":
                sess["text_parts"].append(original or "")
                sess["pre_chunked"].append({
                    "content": original or "",
                    "embedded_content": embedded or original or "",
                    "embedding": _parse_vector(embedding),
                    "metadata": chunk_meta,
                })
            elif kind == "fact" and subject and predicate and obj:
                weight = None
                try:
                    weight = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    pass
                rel = {"src": subject, "dst": obj, "rel_type": predicate}
                if support_span:
                    rel["description"] = support_span
                if weight is not None:
                    rel["weight"] = weight
                sess["known_relationships"].append(rel)
                # synthesise entity rows so pg-raggraph's resolver has
                # matching nodes for src/dst (skip_llm=True path).
                for name in (subject, obj):
                    sess["known_entities"].append(
                        {"name": name, "entity_type": "memory_entity"})

    for sid, sess in sessions.items():
        ns = sess["metadata"].get("namespace") or "default"
        yield {
            "text": "\n\n".join(sess["text_parts"]),
            "source_id": f"memory:{ns}:{sid}",
            "metadata": sess["metadata"],
            "pre_chunked": sess["pre_chunked"],
            "known_entities": _dedup_entities(sess["known_entities"]),
            "known_relationships": sess["known_relationships"],
            "skip_llm": True,
        }


def _dedup_entities(entities: list[dict]) -> list[dict]:
    """Same name → one entry; preserves first-seen order."""
    seen: set[str] = set()
    out: list[dict] = []
    for e in entities:
        key = e["name"]
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out
