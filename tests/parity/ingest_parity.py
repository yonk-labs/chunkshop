#!/usr/bin/env python3
"""3-mode INGEST/CHUNKING parity test for chunkshop.

Proves chunkshop produces the SAME chunk set from one fixed source document,
whether it is invoked as:

  1. EMBEDDED  — the `chunkshop` Python package in-process (chunkshop.pipeline)
  2. SERVICE   — chunkshop-shim `POST /v1/ingest` (docker exec curl)
  3. BENTO     — bento-api `POST /v1/ingest` orchestration (isolated KB)

The shim and bento expose INGEST only (retrieval is pg-raggraph's job
downstream), so this is CHUNKING parity, not retrieval parity: same source
doc -> same resulting chunk set.

FAIRNESS: the chunker / framer / embedder config is ONE identical dict across
all three modes (embedded parses it into CellConfig; shim & bento receive the
byte-identical YAML string). Only `target` (which DB/schema/table to write) and
`source.path` (host copy vs the shim's uploads-volume copy of the SAME bytes)
vary — chunking depends on neither.

WHAT "BENTO" MEANS — two distinct chunk sets, measured separately:

  * BENTO_shim   — bento-api step 1 forwards our cell_yaml verbatim to
                   chunkshop-shim, which writes a chunkshop debug table. This
                   IS chunkshop chunking, inside bento's orchestration, with
                   OUR config. It belongs in the strict parity gate.
  * BENTO_public — bento's `public.chunks`, written by pg-raggraph (step 4,
                   Pattern D). pg-raggraph is a DIFFERENT tool; whether it
                   passes chunkshop's chunks through 1:1 or re-chunks is what
                   we MEASURE (public.chunks.metadata carries chunkshop_seq_num
                   when it's a faithful pass-through). Reported, not gated —
                   forcing chunkshop==pg-raggraph would be comparing two tools.

STRICT GATE: EMBEDDED == SERVICE == BENTO_shim  (all chunkshop, our config).
INFORMATIONAL: BENTO_public vs the chunkshop chunk set, with likely cause.

Isolation: all doc-ids are `test_cs_parity_*`; everything is torn down in a
finally block (schemas dropped, KB deleted + its pg-raggraph rows deleted
explicitly since KB-delete does not cascade them, staged files removed, minted
API key revoked). Never restarts/reconfigures a shared container.

Run from repo root:
    cd python && uv run --no-sync python ../tests/parity/ingest_parity.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import psycopg


# --- fixed identity + endpoints -------------------------------------------
TS = int(time.time())
DOC_IDS = [f"test_cs_parity_{TS}_{i:03d}" for i in range(1, 4)]

EMB_DSN = "postgresql://postgres:postgres@localhost:5533/chunkshop_test"
BENTO_PG_DSN = "postgresql://bento:bento@localhost:5433/bento"  # bento-postgres, published
EMB_DSN_ENV = "CS_PARITY_EMB_DSN"

SHIM = "bento-chunkshop-shim"
API = "http://localhost:8000"
UPLOAD_PATH = f"/var/bento/uploads/test_cs_parity_{TS}.json"

EMB_SCHEMA = f"test_cs_parity_emb_{TS}"
SVC_SCHEMA = f"test_cs_parity_svc_{TS}"
BENTO_TABLE = f"test_cs_parity_bento_{TS}_chunks"  # public.<table>, bento's chunkshop debug table

LOCAL_ADMIN_PASSWORD = os.environ.get("LOCAL_ADMIN_PASSWORD", "")


# --- one source corpus. Prose long enough that sentence_aware splits each
#     doc into several chunks so we have real boundaries to compare. --------
def _para(topic: str, n: int) -> str:
    # Distinct, deterministic sentences; ~4 per doc, each well over min_chars
    # when concatenated, forcing multiple sentence_aware splits at max_chars.
    s = [
        f"The {topic} subsystem records every state transition to an append-only ledger for later audit and replay. "
        f"Operators rely on that ledger when a {topic} incident requires a precise timeline of what changed and when. ",
        f"During the {topic} rollout the team discovered that batched writes reduced tail latency more than caching did. "
        f"They kept the batch window small so that a crash could never lose more than a single in-flight {topic} record. ",
        f"A later {topic} review found that idempotent retries made the pipeline safe to restart at any point. "
        f"Because each {topic} record carried a stable key, replaying the ledger converged to the same final state. ",
    ]
    return "".join(s) * n


CORPUS = [
    {"id": DOC_IDS[0], "content": _para("payments", 2), "title": "parity doc payments"},
    {"id": DOC_IDS[1], "content": _para("scheduler", 2), "title": "parity doc scheduler"},
    {"id": DOC_IDS[2], "content": _para("indexing", 2), "title": "parity doc indexing"},
]


# --- the ONE identical chunker/framer/embedder config (target varies) ------
# sentence_aware is purely lexical -> its chunk boundaries/text/metadata are
# EMBEDDER-INDEPENDENT, so the embedder choice cannot affect the parity gate.
# We still send the same embedder in every mode for a truly identical cell.
def _cell(target: dict, source_path: str) -> dict:
    return {
        "cell_name": f"parity_{TS}",
        "source": {"type": "json_corpus", "path": source_path},
        "chunker": {"type": "sentence_aware", "max_chars": 300, "min_chars": 100},
        "embedder": {"type": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dim": 384},
        "target": target,
    }


def _yaml(d: dict) -> str:
    import yaml
    return yaml.safe_dump(d, sort_keys=False)


# --- small shell helpers ---------------------------------------------------
def _sh(*args: str, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=input, capture_output=True, text=True)


def _shim_curl(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """Call the (host-unpublished) shim via `docker exec curl`."""
    cmd = f'curl -s -o /tmp/p_resp -w "%{{http_code}}" -X {method} http://localhost:8000{path}'
    if body is not None:
        cmd += ' -H "Content-Type: application/json" -d @-'
    code = _sh("docker", "exec", "-i", SHIM, "sh", "-c", cmd,
               input=json.dumps(body) if body is not None else None).stdout.strip()
    resp = _sh("docker", "exec", SHIM, "cat", "/tmp/p_resp").stdout
    _sh("docker", "exec", SHIM, "rm", "-f", "/tmp/p_resp")
    return int(code or 0), resp


def _api(method: str, path: str, body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


# --- chunk-set readers -----------------------------------------------------
def _read_chunkshop(dsn: str, schema: str, table: str) -> list[dict]:
    """Read chunkshop's canonical chunk rows as a comparable set."""
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            f'SELECT doc_id, seq_num, original_content, embedded_content, metadata '
            f'FROM "{schema}"."{table}" WHERE doc_id = ANY(%s) ORDER BY doc_id, seq_num',
            (DOC_IDS,),
        )
        return [
            {"doc_id": r[0], "seq_num": r[1], "original_content": r[2],
             "embedded_content": r[3], "metadata": r[4]}
            for r in cur.fetchall()
        ]


def _read_public_chunks(namespace: str) -> list[dict]:
    """Read pg-raggraph's public.chunks under a KB namespace, aligned to
    chunkshop identity via metadata.chunkshop_{doc_id,seq_num} when present."""
    with psycopg.connect(BENTO_PG_DSN) as c, c.cursor() as cur:
        cur.execute(
            "SELECT c.metadata->>'chunkshop_doc_id', c.metadata->>'chunkshop_seq_num', c.content "
            "FROM public.chunks c JOIN public.documents d ON c.document_id = d.id "
            "WHERE d.namespace = %s ORDER BY 1, 2",
            (namespace,),
        )
        return [{"doc_id": r[0], "seq_num": r[1], "content": r[2]} for r in cur.fetchall()]


# --- comparison ------------------------------------------------------------
def _key(rows: list[dict]) -> dict[tuple, dict]:
    return {(r["doc_id"], r["seq_num"]): r for r in rows}


def _compare(a_name: str, a: list[dict], b_name: str, b: list[dict]) -> dict:
    ka, kb = _key(a), _key(b)
    keys = set(ka) | set(kb)
    text_id = meta_id = 0
    diffs: list[str] = []
    for k in sorted(keys, key=lambda x: (str(x[0]), x[1] if x[1] is not None else -1)):
        ra, rb = ka.get(k), kb.get(k)
        if ra is None or rb is None:
            diffs.append(f"{k}: present in {a_name if ra else b_name} only")
            continue
        if ra["original_content"] == rb["original_content"] and ra["embedded_content"] == rb["embedded_content"]:
            text_id += 1
        else:
            diffs.append(f"{k}: text differs")
        if json.dumps(ra["metadata"], sort_keys=True) == json.dumps(rb["metadata"], sort_keys=True):
            meta_id += 1
        else:
            diffs.append(f"{k}: metadata differs ({ra['metadata']} vs {rb['metadata']})")
    return {"pair": f"{a_name} vs {b_name}", "a": len(a), "b": len(b),
            "shared": len(set(ka) & set(kb)), "text_identical": text_id,
            "meta_identical": meta_id, "diffs": diffs}


# --- teardown (always) -----------------------------------------------------
def _cleanup(state: dict) -> None:
    # embedded schema
    try:
        with psycopg.connect(EMB_DSN) as c, c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{EMB_SCHEMA}" CASCADE'); c.commit()
    except Exception as e:
        print(f"  cleanup: embedded schema drop failed: {e}")
    # bento-postgres: svc schema + bento debug table
    try:
        with psycopg.connect(BENTO_PG_DSN) as c, c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{SVC_SCHEMA}" CASCADE')
            cur.execute(f'DROP TABLE IF EXISTS public."{BENTO_TABLE}" CASCADE')
            if state.get("kb_id"):
                ns = f"kb-{state['kb_id']}"
                cur.execute("DELETE FROM public.chunks WHERE document_id IN "
                            "(SELECT id FROM public.documents WHERE namespace=%s)", (ns,))
                cur.execute("DELETE FROM public.documents WHERE namespace=%s", (ns,))
            c.commit()
    except Exception as e:
        print(f"  cleanup: bento-postgres cleanup failed: {e}")
    # KB delete + key revoke via bento-api
    tok = state.get("token")
    if state.get("kb_id") and tok:
        _api("DELETE", f"/v1/me/kbs/{state['kb_id']}", token=tok)
    if tok:
        # Revoke every ACTIVE local-admin-login key. The listing includes
        # already-revoked keys from prior runs; revoking only the first name
        # match would no-op on a stale revoked one and leave THIS run's active
        # key behind. Filter on revoked_at and revoke all.
        _, keys = _api("GET", "/v1/me/api-keys", token=tok)
        for k in keys or []:
            if k.get("name") == "local-admin-login" and not k.get("revoked_at"):
                _api("DELETE", f"/v1/me/api-keys/{k['id']}", token=tok)
    # staged upload files
    _sh("docker", "exec", SHIM, "rm", "-f", UPLOAD_PATH)


def main() -> int:
    state: dict = {}
    results: list[dict] = []
    embedded = service = bento_shim = bento_public = None

    # stage the ONE corpus: host copy (embedded) + uploads-volume copy (shim/bento)
    host_json = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"documents": CORPUS}, host_json)
    host_json.close()
    os.chmod(host_json.name, 0o644)
    _sh("docker", "cp", host_json.name, f"{SHIM}:{UPLOAD_PATH}")

    try:
        # ============ MODE 1: EMBEDDED ============
        os.environ[EMB_DSN_ENV] = EMB_DSN
        with psycopg.connect(EMB_DSN) as c, c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{EMB_SCHEMA}" CASCADE'); c.commit()
        from chunkshop.config import CellConfig
        from chunkshop.runner import run_cell
        cell = _cell(
            {"type": "postgres", "dsn_env": EMB_DSN_ENV, "database": EMB_SCHEMA,
             "table": "chunks", "hnsw": False, "mode": "create_if_missing", "source_tag": "parity"},
            host_json.name,  # host copy of the SAME bytes staged into the shim volume
        )
        # run_cell is the source-driven library entry point (same one the shim
        # calls internally) — identical source handling to SERVICE/BENTO, so
        # only the target differs.
        run_cell(CellConfig(**cell))
        embedded = _read_chunkshop(EMB_DSN, EMB_SCHEMA, "chunks")
        print(f"[EMBEDDED] chunkshop lib (run_cell) -> {len(embedded)} chunks")

        # ============ MODE 2: SERVICE (chunkshop-shim) ============
        svc_cell = _cell(
            {"type": "postgres", "dsn_env": "DATABASE_URL", "database": SVC_SCHEMA,
             "table": "chunks", "hnsw": False, "mode": "create_if_missing", "source_tag": "parity"},
            UPLOAD_PATH,
        )
        code, resp = _shim_curl("POST", "/v1/ingest",
                                {"cell_yaml": _yaml(svc_cell), "corpus_name": f"parity_svc_{TS}"})
        if code != 200:
            print(f"[SERVICE] shim ingest FAILED ({code}): {resp}")
        else:
            service = _read_chunkshop(BENTO_PG_DSN, SVC_SCHEMA, "chunks")
            print(f"[SERVICE]  chunkshop-shim -> {len(service)} chunks")

        # ============ MODE 3: BENTO (bento-api orchestration) ============
        if not LOCAL_ADMIN_PASSWORD:
            print("[BENTO] SKIP: LOCAL_ADMIN_PASSWORD unset (no isolated-KB auth path)")
        elif _api("GET", "/v1/admin/local-login/status")[1].get("enabled") is not True:
            print("[BENTO] SKIP: LOCAL_ADMIN_ENABLED dev bypass off on this bento-api")
        else:
            _, login = _api("POST", "/v1/admin/local-login", {"password": LOCAL_ADMIN_PASSWORD})
            state["token"] = login.get("raw_key")
            _, kb = _api("POST", "/v1/me/kbs", {"name": f"test_cs_parity_{TS}"}, token=state["token"])
            state["kb_id"] = kb.get("id")
            bento_cell = _cell(
                {"type": "postgres", "dsn_env": "DATABASE_URL", "database": "public",
                 "table": BENTO_TABLE, "hnsw": False, "mode": "create_if_missing", "source_tag": "parity"},
                UPLOAD_PATH,
            )
            code, resp = _api("POST", "/v1/ingest",
                              {"cell_yaml": _yaml(bento_cell), "corpus_name": f"parity_bento_{TS}",
                               "kb_id": state["kb_id"]}, token=state["token"])
            if code != 200:
                print(f"[BENTO] bento-api ingest FAILED ({code}): {resp}")
            else:
                # 3a: chunkshop-in-bento (step-1 debug table) — strict gate member
                bento_shim = _read_chunkshop(BENTO_PG_DSN, "public", BENTO_TABLE)
                # 3b: pg-raggraph public.chunks — informational
                bento_public = _read_public_chunks(f"kb-{state['kb_id']}")
                print(f"[BENTO]    chunkshop-in-bento(debug table) -> {len(bento_shim)} chunks; "
                      f"pg-raggraph public.chunks -> {len(bento_public)} chunks")

        # ============ COMPARE ============
        gate = [("EMBEDDED", embedded), ("SERVICE", service), ("BENTO_shim", bento_shim)]
        present = [(n, r) for n, r in gate if r is not None]
        base_name, base = present[0]
        for name, rows in present[1:]:
            results.append(_compare(base_name, base, name, rows))

        print("\n================ INGEST/CHUNKING PARITY ================")
        print("STRICT GATE (all chunkshop, identical config):")
        gate_ok = True
        for r in results:
            ok = (r["a"] == r["b"] == r["shared"] == r["text_identical"] == r["meta_identical"])
            gate_ok = gate_ok and ok
            print(f"  {r['pair']:32s} count {r['a']}/{r['b']}  "
                  f"text-identical {r['text_identical']}/{r['shared']}  "
                  f"meta-identical {r['meta_identical']}/{r['shared']}  "
                  f"{'PASS' if ok else 'FAIL'}")
            for d in r["diffs"][:10]:
                print(f"      - {d}")

        # informational: pg-raggraph
        print("\nINFORMATIONAL (pg-raggraph, a different tool — not gated):")
        if bento_public is not None and base is not None:
            aligned = sum(1 for r in bento_public if r["seq_num"] is not None)
            cs_text = {(r["doc_id"], str(r["seq_num"])): r["original_content"] for r in base}
            text_match = sum(1 for r in bento_public
                             if (r["doc_id"], r["seq_num"]) in cs_text
                             and cs_text[(r["doc_id"], r["seq_num"])] == r["content"])
            passthrough = aligned == len(bento_public) and text_match == len(base) == len(bento_public)
            print(f"  BENTO_public: {len(bento_public)} rows vs chunkshop {len(base)}; "
                  f"carry chunkshop_seq_num {aligned}/{len(bento_public)}; "
                  f"text-identical-to-chunkshop {text_match}/{len(bento_public)}")
            print(f"  -> {'faithful 1:1 pass-through of chunkshop chunks' if passthrough else 'DIVERGES from chunkshop chunks'} "
                  f"(likely cause: {'n/a' if passthrough else 'pg-raggraph re-chunks / applies its own chunk sizing in Pattern D'})")
        else:
            print("  BENTO_public: not collected (bento mode skipped or failed)")

        print("=======================================================")
        modes_run = [n for n, r in gate if r is not None]
        print(f"Modes compared in strict gate: {', '.join(modes_run)}")
        if len(present) < 2:
            print("RESULT: INCONCLUSIVE — fewer than 2 modes produced a chunk set")
            return 2
        print(f"RESULT: {'PASS — chunkshop chunking is identical across modes' if gate_ok else 'FAIL — a mode diverged (see diffs above)'}")
        return 0 if gate_ok else 1

    finally:
        _cleanup(state)
        try:
            os.unlink(host_json.name)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
