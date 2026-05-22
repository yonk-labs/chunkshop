# Search Product (Brief B phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the shipped hybrid-search library (`chunkshop.search.*` + `summarize_hits`) into a configurable product: opt-in FTS via `target.fts`, a `chunkshop search` CLI, and a typed `SearchResult` return-mode contract.

**Architecture:** Pure wiring + thin orchestration. Add an optional `FtsConfig` to `TargetConfig` and call the **already-shipped** `ensure_fts` from the sink's `create_table` path when enabled. Add a `SearchResult` dataclass + a `search()` entry point in `search_common.py` that composes the shipped `hybrid_search` + `summarize_hits` into three return modes. Add a click `search` subcommand that loads a cell's embedder, embeds the query, and calls `search()`. No search/fusion/summarization internals are modified.

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"`), click, psycopg3, `uv`. Runtime deps already present: `lede>=0.4.1`, `lede-spacy>=0.4.2`, `tiktoken`.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-search-product.md` — implements SC-001..SC-012, gates DC-001/002/003/FINAL. Re-read at each ⛔.

**Sequencing:** D1 (Tasks 1–4) → ⛔ DC-001 → D3 (Tasks 5–7) → ⛔ DC-002 → D2 (Tasks 8–10) → ⛔ DC-003 → docs (Task 11) → ⛔ DC-FINAL.

**Working dir:** `/home/yonk/yonk-tools/chunkshop-search-product` (worktree, branch `feat/search-product`). All `cd python` are relative to it. Run tests with `uv run --no-sync` (preserves lede/lede-spacy/tiktoken). Install once:
```bash
cd python && uv sync --extra dev --extra extractors --extra all-backends
uv pip install -e ".[lede]" -e ".[lede-spacy]" tiktoken
uv run --no-sync python -m spacy download en_core_web_sm
```
Live pg test DB: `$CHUNKSHOP_TEST_DSN` (default `postgresql://postgres:postgres@localhost:5434/chunkshop_test`).

**Shipped APIs this plan WIRES (do not modify their signatures — SC-011):**
- `chunkshop.search.ensure_fts(dsn, *, schema, table, language="english")` (pg; sqlite/mariadb/clickhouse equivalents in `search_<backend>.py`).
- `hybrid_search(dsn, *, schema, table, query, query_vec, k, legs, where, fusion, weights, rrf_k, candidate_multiplier, language) -> list[Hit]`.
- `summarize_hits(hits, summarize_fn, *, max_length, hints, hint_focus, hint_mode, prepend_headings, use_embedded) -> str`.
- `Hit{doc_id, seq_num, text, score, metadata, legs, embedded_text}`.
- `chunkshop.summarizers.lede.summarize` (injectable `summarize_fn`); `lede.extract.top_terms(q, n=6, with_scores=True)`.
- `chunkshop.config.TargetConfig` (`type`, `database_name`(alias `database`), `table`, `mode`, `resolve_dsn()`), `HintExpansion`, `FastembedEmbedder`, `load_embedder`, `CellConfig`.
- Sink `create_table()` (pg dispatch on `mode`); `runner` calls `load_sink(cfg.target, embed_dim=...)` then `sink.create_table()`.

---

## File Structure
- **Modify** `python/src/chunkshop/config.py` — add `FtsConfig`, `TargetConfig.fts`.
- **Modify** `python/src/chunkshop/sinks/pg.py` (+ `sqlite.py`, `mariadb.py`, `clickhouse.py`) — call `ensure_fts` from `create_table` when `cfg.fts.enabled`; append-mode validation.
- **Modify** `python/src/chunkshop/search_common.py` — add `SearchResult` + `search()`.
- **Modify** `python/src/chunkshop/cli.py` — add `search` subcommand.
- **Tests** under `python/tests/chunkshop/`: `test_config_fts.py`, `test_sink_fts_pg.py`, `test_search_result.py`, `test_cli_search.py`, plus a no-fts ingest regression.
- **Docs** `docs/hybrid-search.md`.

---

## Tier D1 — target.fts config + ingest wiring

### Task 1: `FtsConfig` model + `TargetConfig.fts` (SC-001)

**Files:** Modify `python/src/chunkshop/config.py` (near `TargetConfig`); Test `python/tests/chunkshop/test_config_fts.py`.

- [ ] **Step 1: failing test**
```python
"""SC-001: FtsConfig model + TargetConfig.fts."""
import pytest
from pydantic import ValidationError
from chunkshop.config import TargetConfig, FtsConfig


def test_fts_defaults_off():
    assert FtsConfig().enabled is False
    assert FtsConfig().language == "english"


def test_target_fts_attaches_and_defaults_none():
    t = TargetConfig(type="postgres", database="db", table="chunks")
    assert t.fts is None
    t2 = TargetConfig(type="postgres", database="db", table="chunks",
                      fts={"enabled": True, "language": "english"})
    assert t2.fts.enabled is True


def test_fts_rejects_unknown_language():
    with pytest.raises(ValidationError):
        FtsConfig(enabled=True, language="klingon")


def test_fts_forbids_extra():
    with pytest.raises(ValidationError):
        FtsConfig(enabled=True, languagee="english")
```
- [ ] **Step 2: run, expect FAIL** (`ImportError`/`ValidationError`): `cd python && uv run --no-sync pytest tests/chunkshop/test_config_fts.py -v`
- [ ] **Step 3: implement.** In `config.py`, import the language allowlist source and add the model above `TargetConfig`:
```python
from chunkshop.search import _ALLOWED_LANGUAGES  # reuse the shipped allowlist


class FtsConfig(_Base):
    """Opt-in full-text-search index for a target table (LD-4)."""
    enabled: bool = False
    language: str = "english"

    @field_validator("language")
    @classmethod
    def _lang_allowlisted(cls, v):
        if v not in _ALLOWED_LANGUAGES:
            raise ValueError(
                f"fts.language must be one of {sorted(_ALLOWED_LANGUAGES)}, got {v!r}"
            )
        return v
```
If importing `_ALLOWED_LANGUAGES` from `chunkshop.search` causes a circular import (search imports config), instead define the allowlist set in `config.py` and have `search.py` import it from config in a follow-up — but FIRST try the direct import and only refactor if Python raises `ImportError`. Note which you did in the commit.
Then add to `TargetConfig` (after `engine`): `fts: Optional["FtsConfig"] = None`.
- [ ] **Step 4: run, expect PASS** (all 4).
- [ ] **Step 5: commit**
```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_config_fts.py
git commit -m "feat(config): add opt-in FtsConfig on TargetConfig (SC-001)"
```

### Task 2: capture no-fts ingest regression golden (SC-004)

**Files:** Test `python/tests/chunkshop/test_fts_ingest_noop.py` (live pg).

- [ ] **Step 1: write the test** — ingest a tiny in-memory corpus with NO `target.fts`, then assert the created table has the canonical columns and NO `search_vector` column / no `*_fts_idx` index. Use the probe-and-skip idiom from `test_search_pg.py`.
```python
"""SC-004: ingest without target.fts is unchanged — no FTS column/index."""
import os, importlib.util
import pytest

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")

def _pg_up():
    if importlib.util.find_spec("psycopg") is None:
        return False
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg test DB unreachable")


def test_no_fts_ingest_has_no_search_vector():
    import psycopg
    from chunkshop.config import CellConfig
    from chunkshop.runner import run_cell
    schema = "chunkshop_fts_noop"
    cfg = CellConfig.model_validate({
        "cell_name": "fts_noop",
        "source": {"type": "inline", "documents": [
            {"id": "d1", "content": "Alpha beta gamma. Delta epsilon zeta."}]},
        "chunker": {"type": "sentence_aware"},
        "embedder": {"type": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dim": 384},
        "target": {"type": "postgres", "dsn": DSN, "database": schema,
                   "table": "chunks", "mode": "overwrite", "hnsw": False},
    })
    try:
        run_cell(cfg)
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name='chunks'", (schema,))
            cols = {r[0] for r in cur.fetchall()}
            assert "search_vector" not in cols
            cur.execute("SELECT indexname FROM pg_indexes "
                        "WHERE schemaname=%s AND tablename='chunks'", (schema,))
            idxs = {r[0] for r in cur.fetchall()}
            assert not any(i.endswith("_fts_idx") for i in idxs)
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'); conn.commit()
```
(Confirm the `inline` source shape against `src/chunkshop/sources/`; if the field is `docs`/`items` not `documents`, adjust. If no inline source exists, use a `files` source pointing at a tmp `.md`.)
- [ ] **Step 2: run, expect PASS now** (pre-implementation — proves current behavior is FTS-free; this test must stay green through D1).
- [ ] **Step 3: commit**
```bash
git add python/tests/chunkshop/test_fts_ingest_noop.py
git commit -m "test(fts): pin no-fts ingest has no search_vector (SC-004)"
```

### Task 3: wire `ensure_fts` into the pg sink `create_table` (SC-002, SC-003)

**Files:** Modify `python/src/chunkshop/sinks/pg.py`; Test `python/tests/chunkshop/test_sink_fts_pg.py`.

- [ ] **Step 1: failing tests** (live pg): create_if_missing+fts builds the index; append+fts on a no-FTS table raises actionably.
```python
"""SC-002/003: pg sink wires ensure_fts on create; append validates it exists."""
import os, importlib.util
import pytest

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")
def _pg_up():
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=2): return True
    except Exception: return False
pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg unreachable")

from chunkshop.config import TargetConfig
from chunkshop.sinks import load_sink
import psycopg


def _has_fts_idx(schema):
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_indexes WHERE schemaname=%s AND indexname='chunks_fts_idx'", (schema,))
        return cur.fetchone() is not None


def test_create_if_missing_builds_fts_index():
    schema = "chunkshop_fts_cim"
    try:
        cfg = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                           mode="create_if_missing", hnsw=False,
                           fts={"enabled": True, "language": "english"})
        load_sink(cfg, embed_dim=384).create_table()
        assert _has_fts_idx(schema)
    finally:
        with psycopg.connect(DSN) as c, c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'); c.commit()


def test_append_without_fts_index_raises():
    schema = "chunkshop_fts_append"
    try:
        # First create a table WITHOUT fts.
        base = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                            mode="create_if_missing", hnsw=False)
        load_sink(base, embed_dim=384).create_table()
        # Now append WITH fts.enabled → must raise (index missing).
        appcfg = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                              mode="append", source_tag="t", hnsw=False,
                              fts={"enabled": True, "language": "english"})
        with pytest.raises(RuntimeError, match="fts"):
            load_sink(appcfg, embed_dim=384).create_table()
    finally:
        with psycopg.connect(DSN) as c, c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'); c.commit()
```
- [ ] **Step 2: run, expect FAIL** (no FTS built; no append validation).
- [ ] **Step 3: implement.** In `pg.py`, import the shipped helper at top: `from chunkshop.search import ensure_fts as _ensure_fts`. (If circular, import lazily inside the method.) In `create_table()`, after the mode dispatch but BEFORE `conn.commit()` is closed — actually after the `with ... commit()` block — add FTS handling. Cleanest: after the existing `conn.commit()` in `create_table`, append:
```python
        if self.cfg.fts and self.cfg.fts.enabled:
            self._ensure_or_validate_fts()
```
and add the method:
```python
    def _ensure_or_validate_fts(self) -> None:
        dsn = self.cfg.resolve_dsn()
        schema = self.cfg.database_name
        if self.cfg.mode == "append":
            # Validate the FTS index already exists; do not silently build it
            # on append (the table predates this cell).
            with self.backend.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND indexname=%s",
                    (schema, f"{self.cfg.table}_fts_idx"),
                )
                if cur.fetchone() is None:
                    raise RuntimeError(
                        f"target.fts.enabled=true but {schema}.{self.cfg.table} has no "
                        f"FTS index ({self.cfg.table}_fts_idx). Re-create the table with "
                        f"mode=overwrite/create_if_missing + fts.enabled, or remove fts."
                    )
            return
        _ensure_fts(dsn, schema=schema, table=self.cfg.table,
                    language=self.cfg.fts.language)
```
- [ ] **Step 4: run, expect PASS** (both); re-run SC-004 noop test (still green) + the existing pg sink suite.
- [ ] **Step 5: commit**
```bash
git add python/src/chunkshop/sinks/pg.py python/tests/chunkshop/test_sink_fts_pg.py
git commit -m "feat(sinks/pg): build/validate FTS index on create_table when target.fts.enabled (SC-002, SC-003)"
```

### Task 4: wire FTS for sqlite/mariadb/clickhouse sinks (SC-002 parity)

**Files:** Modify `python/src/chunkshop/sinks/sqlite.py`, `mariadb.py`, `clickhouse.py`.

- [ ] **Step 1:** For each backend sink, mirror Task 3: after `create_table` finishes, if `self.cfg.fts and self.cfg.fts.enabled`, call that backend's `ensure_fts` (from `search_sqlite`/`search_mariadb`/`search_clickhouse`), and on `mode=="append"` validate the FTS structure exists (FTS5 table for sqlite, FULLTEXT index for mariadb, token index for clickhouse) raising the same actionable error. Reuse each backend's existing `ensure_fts`; do not reimplement.
- [ ] **Step 2: tests** — add a sqlite case to `test_sink_fts_pg.py`'s pattern (sqlite needs no DB) asserting the FTS5 table is created when `fts.enabled`. mariadb/clickhouse: add cases gated on their DSNs (skip if unset), mirroring `test_search_mariadb.py`/`test_search_clickhouse.py` skip idioms.
- [ ] **Step 3: run** the sqlite case (always) + mariadb/clickhouse (if DSNs set): `uv run --no-sync pytest tests/chunkshop/test_sink_fts_pg.py -v` and the per-backend files. Expect PASS / clean skips.
- [ ] **Step 4: commit**
```bash
git add python/src/chunkshop/sinks/sqlite.py python/src/chunkshop/sinks/mariadb.py python/src/chunkshop/sinks/clickhouse.py python/tests/chunkshop/test_sink_fts_pg.py
git commit -m "feat(sinks): wire FTS index build/validate for sqlite/mariadb/clickhouse (SC-002)"
```

### ⛔ DC-001 — Gate before D3
- [ ] Re-read the mission brief.
- [ ] Verify SC-001 (config), SC-002 (index built on create), SC-003 (append validates), **SC-004 (no-fts ingest unchanged — run `test_fts_ingest_noop.py`)**. Confirm `ensure_fts` was REUSED, not reimplemented (grep the sinks for any new `to_tsvector`/`FTS5`/`FULLTEXT` DDL — there should be none beyond calls into `search_*`).
- [ ] Full suite: `uv run --no-sync pytest -q` green (modulo documented skips).
- [ ] Drift check (Purpose / SC mapping / Out of Scope). Fix gaps before D3.

---

## Tier D3 — SearchResult + return modes

### Task 5: `SearchResult` dataclass + `search()` chunks-mode (SC-008, SC-010 partial)

**Files:** Modify `python/src/chunkshop/search_common.py`; Test `python/tests/chunkshop/test_search_result.py`.

- [ ] **Step 1: failing test** (live pg; reuse an ingested fixture or ingest a tiny corpus like `test_search_pg.py`).
```python
"""SC-008/010: SearchResult + search() chunks mode (no summarization, no lede)."""
import os, importlib.util
import pytest
DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")
def _pg_up():
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=2): return True
    except Exception: return False
pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg unreachable")

from chunkshop.search_common import SearchResult, search
# (assume a module-scoped fixture `seeded` ingests schema 'chunkshop_sr_test'
#  with fts enabled + returns (schema, embedder); copy the fixture from test_search_pg.py
#  and add ensure_fts.)


def test_chunks_mode_returns_hits_no_summary(seeded):
    schema, emb = seeded
    qv = emb.embed(["alpha"])[0]
    res = search(DSN, schema=schema, table="chunks", query="alpha", query_vec=qv,
                 k=5, return_mode="chunks")
    assert isinstance(res, SearchResult)
    assert res.summary is None
    assert res.query == "alpha"
    assert all(hasattr(h, "doc_id") for h in res.chunks)
```
- [ ] **Step 2: run, expect FAIL** (no `SearchResult`/`search`).
- [ ] **Step 3: implement** in `search_common.py`:
```python
from dataclasses import dataclass, field
from typing import Literal, Optional, Any

@dataclass
class SearchResult:
    chunks: list  # list[Hit]
    summary: Optional[str]
    query: str

# import the backend hybrid_search lazily to avoid backend coupling at import
def _hybrid_for(dsn: str):
    # pg is the default product backend; callers needing another backend use the
    # per-backend module directly. (Phase 2 CLI resolves backend from cfg.target.type.)
    from chunkshop import search as _pg
    return _pg.hybrid_search

def search(dsn, *, schema, table, query, query_vec, k=10,
           legs=("semantic", "fts"), where=None, fusion="rrf",
           return_mode: Literal["chunks", "summary+chunks", "summary"] = "chunks",
           summarize_fn=None, summary_hints=None, summary_expand=None,
           summary_max_length=1200, language="english",
           hybrid_search_fn=None) -> SearchResult:
    hs = hybrid_search_fn or _hybrid_for(dsn)
    hits = hs(dsn, schema=schema, table=table, query=query, query_vec=query_vec,
              k=k, legs=tuple(legs), where=where, fusion=fusion, language=language)
    if return_mode == "chunks":
        return SearchResult(chunks=hits, summary=None, query=query)
    summary = _summarize_for_query(
        hits, query, summarize_fn=summarize_fn, summary_hints=summary_hints,
        summary_expand=summary_expand, max_length=summary_max_length)
    chunks = [] if return_mode == "summary" else hits
    return SearchResult(chunks=chunks, summary=summary, query=query)
```
(`_summarize_for_query` is Task 6. For now, define a stub that raises `NotImplementedError` so chunks-mode test passes and summary-mode is built next — OR implement Task 6 first. Sequence Task 6 immediately.)
- [ ] **Step 4: run chunks-mode test, expect PASS.**
- [ ] **Step 5: commit** (with Task 6).

### Task 6: `_summarize_for_query` — auto query hints + summarize_hits (SC-009)

**Files:** Modify `python/src/chunkshop/search_common.py`; Test `python/tests/chunkshop/test_search_result.py` (append).

- [ ] **Step 1: failing tests** — summary mode populates `summary`, drops chunks; hints auto-derived from query; explicit `summary_hints` overrides; `chunks` mode imported no lede.
```python
def test_summary_mode_summarizes_and_drops_chunks(seeded):
    schema, emb = seeded
    from chunkshop.summarizers.lede import summarize
    qv = emb.embed(["alpha beta"])[0]
    res = search(DSN, schema=schema, table="chunks", query="alpha beta", query_vec=qv,
                 k=5, return_mode="summary", summarize_fn=summarize)
    assert res.chunks == []
    assert isinstance(res.summary, str) and res.summary

def test_summary_plus_chunks_has_both(seeded):
    schema, emb = seeded
    from chunkshop.summarizers.lede import summarize
    qv = emb.embed(["alpha"])[0]
    res = search(DSN, schema=schema, table="chunks", query="alpha", query_vec=qv,
                 k=5, return_mode="summary+chunks", summarize_fn=summarize)
    assert res.chunks and res.summary
```
- [ ] **Step 2: run, expect FAIL.**
- [ ] **Step 3: implement** `_summarize_for_query` in `search_common.py`:
```python
def _derive_query_hints(query: str):
    from lede.extract import top_terms  # lazy — only on summary path
    return [t.term for t in top_terms(query, n=6, with_scores=True)]

def _summarize_for_query(hits, query, *, summarize_fn, summary_hints,
                         summary_expand, max_length):
    if summarize_fn is None:
        raise ValueError(
            "summary return modes require summarize_fn "
            "(e.g. chunkshop.summarizers.lede.summarize)")
    hints = summary_hints if summary_hints is not None else _derive_query_hints(query)
    if summary_expand is not None and hints:
        from chunkshop.hints import expand_hints
        hints = expand_hints(hints, kinds=tuple(summary_expand.kinds),
                             top_k=summary_expand.top_k,
                             expand_weight=summary_expand.expand_weight)
    return summarize_hits(hits, summarize_fn, max_length=max_length, hints=hints)
```
Note: `top_terms` / `expand_hints` are imported INSIDE this function, so the `chunks` return mode never imports lede (SC-010).
- [ ] **Step 4: run, expect PASS** (both new + chunks-mode still green).
- [ ] **Step 5: commit**
```bash
git add python/src/chunkshop/search_common.py python/tests/chunkshop/test_search_result.py
git commit -m "feat(search): SearchResult + search() return modes with auto query-hint summaries (SC-008, SC-009, SC-010)"
```

### Task 7: assert chunks-mode imports no lede (SC-010, SC-011)

**Files:** Test `python/tests/chunkshop/test_search_result.py` (append).

- [ ] **Step 1: test** — monkeypatch to prove the `chunks` path doesn't import lede:
```python
def test_chunks_mode_no_lede_import(seeded, monkeypatch):
    import sys
    schema, emb = seeded
    # Drop any cached lede modules, then ban importing them.
    for m in [k for k in sys.modules if k == "lede" or k.startswith("lede.")]:
        monkeypatch.delitem(sys.modules, m, raising=False)
    real_import = __import__
    def guard(name, *a, **k):
        assert not (name == "lede" or name.startswith("lede.")), f"chunks mode imported {name}"
        return real_import(name, *a, **k)
    monkeypatch.setattr("builtins.__import__", guard)
    qv = emb.embed(["alpha"])[0]
    search(DSN, schema=schema, table="chunks", query="alpha", query_vec=qv,
           k=5, return_mode="chunks")  # must not trip the guard
```
- [ ] **Step 2: run, expect PASS.** Also run `test_no_lede_core_imports.py` (search_common still must not top-level import lede). 
- [ ] **Step 3: commit**
```bash
git add python/tests/chunkshop/test_search_result.py
git commit -m "test(search): chunks mode triggers no lede import (SC-010, SC-011)"
```

### ⛔ DC-002 — Gate before D2
- [ ] Re-read the brief. Verify SC-008 (three modes' SearchResult shapes), SC-009 (auto hints + override + expand), SC-010 (chunks-mode no lede). Re-run `test_no_lede_core_imports.py`. Confirm `hybrid_search`/`summarize_hits` signatures unchanged (SC-011). Drift check.

---

## Tier D2 — chunkshop search CLI

### Task 8: `chunkshop search` subcommand skeleton (SC-005)

**Files:** Modify `python/src/chunkshop/cli.py`; Test `python/tests/chunkshop/test_cli_search.py`.

- [ ] **Step 1: failing test** (click runner; arg parsing + chunks mode, live pg).
```python
"""SC-005/006/007: chunkshop search CLI."""
import os, json
import pytest
from click.testing import CliRunner
from chunkshop.cli import cli
DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")
# (a fixture writes a cell YAML to tmp_path pointing at an ingested fts-enabled schema)

def test_search_chunks_json(search_cell_yaml):  # fixture returns path
    r = CliRunner().invoke(cli, ["search", "--config", str(search_cell_yaml),
                                 "--query", "alpha", "--k", "5", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["query"] == "alpha"
    assert "chunks" in data and data["summary"] is None
```
- [ ] **Step 2: run, expect FAIL** (no `search` command).
- [ ] **Step 3: implement** in `cli.py` (mirror `ingest`/`bakeoff` structure):
```python
@cli.command()
@click.option("--config", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--query", required=True)
@click.option("--k", default=10, type=int)
@click.option("--return", "return_mode",
              type=click.Choice(["chunks", "summary+chunks", "summary"]), default="chunks")
@click.option("--legs", default="semantic,fts")
@click.option("--where", multiple=True, help="filter as KEY=VALUE (source=, tags=a,b, metadata.k=v)")
@click.option("--json", "as_json", is_flag=True)
def search(config, query, k, return_mode, legs, where, as_json):
    """Hybrid-search a cell's target and optionally summarize the hits."""
    import json as _json
    from chunkshop.config import CellConfig
    from chunkshop.embedders import load_embedder
    from chunkshop.search_common import search as _search
    try:
        cfg = CellConfig.model_validate(yaml.safe_load(Path(config).read_text()))
        emb = load_embedder(cfg.embedder)
        qv = emb.embed([query])[0]
        tgt = cfg.target
        summ = None
        if return_mode != "chunks":
            from chunkshop.summarizers.lede import summarize as summ
        res = _search(
            tgt.resolve_dsn(), schema=tgt.database_name, table=tgt.table,
            query=query, query_vec=qv, k=k, legs=tuple(legs.split(",")),
            where=_parse_where(where) or None, return_mode=return_mode,
            summarize_fn=summ,
            language=(tgt.fts.language if tgt.fts else "english"),
        )
    except Exception as exc:  # actionable, non-traceback exit
        raise click.ClickException(str(exc))
    if as_json:
        click.echo(_json.dumps({
            "query": res.query, "summary": res.summary,
            "chunks": [{"doc_id": h.doc_id, "seq_num": h.seq_num, "score": h.score,
                        "text": h.text, "legs": list(h.legs)} for h in res.chunks],
        }, indent=2))
    else:
        if res.summary:
            click.echo(f"SUMMARY:\n{res.summary}\n")
        for i, h in enumerate(res.chunks, 1):
            click.echo(f"{i}. [{h.score:.4f}] {h.doc_id}#{h.seq_num}  {h.text[:120]}")
```
Add the `_parse_where` helper (KEY=VALUE; `tags=a,b` → list; `metadata.x=y` → `{"metadata": {"x": "y"}}`; `source=v` → `{"source": v}`). Ensure `yaml` + `Path` are imported in cli.py (they are, per `ingest`).
- [ ] **Step 4: run, expect PASS.**
- [ ] **Step 5: commit**
```bash
git add python/src/chunkshop/cli.py python/tests/chunkshop/test_cli_search.py
git commit -m "feat(cli): chunkshop search subcommand — embed query, hybrid search, print (SC-005, SC-006)"
```

### Task 9: CLI `--return summary` + `--where` + error paths (SC-006, SC-007)

**Files:** Test `python/tests/chunkshop/test_cli_search.py` (append).

- [ ] **Step 1: tests** — `--return summary+chunks` prints a SUMMARY block and chunks; `--where source=<tag>` restricts; a bad `--where foo` and an unreachable DSN exit non-zero WITHOUT a traceback (`assert r.exit_code != 0 and "Traceback" not in r.output`).
- [ ] **Step 2: run, expect FAIL** where behavior missing; fix `_parse_where` / error handling in Task 8's code as needed.
- [ ] **Step 3: run, expect PASS.**
- [ ] **Step 4: commit**
```bash
git add python/tests/chunkshop/test_cli_search.py python/src/chunkshop/cli.py
git commit -m "test(cli): search summary modes, where filter, clean error exits (SC-006, SC-007)"
```

### Task 10: CLI `validate` recognizes `target.fts` + e2e smoke

**Files:** verify `chunkshop validate` accepts a cell with `target.fts`; Test `python/tests/chunkshop/test_cli_search.py` (append a validate case).

- [ ] **Step 1:** Confirm `chunkshop validate --config <fts cell>` exits 0 (FtsConfig is part of TargetConfig, so it should). Add a test asserting it. If validate has a field allowlist that rejects `fts`, fix it.
- [ ] **Step 2: run, expect PASS.**
- [ ] **Step 3: commit**
```bash
git add python/tests/chunkshop/test_cli_search.py
git commit -m "test(cli): validate accepts target.fts cells"
```

### ⛔ DC-003 — Gate after D2
- [ ] Re-read the brief. Verify SC-005/006/007: CLI args, embeds query via cell embedder, `--return` modes, `--json` mirrors SearchResult, `--where` maps, error paths exit non-zero with no traceback. Run the e2e CLI walk-through against live pg. Drift check.

---

## Task 11: Docs (SC-012)

**Files:** Modify `docs/hybrid-search.md`.

- [ ] **Step 1:** Add three sections: (a) **`target.fts`** — opt-in FTS at ingest (`target: {fts: {enabled: true, language: english}}`), schema-flex behavior (create builds it; append validates it), backend support note (pg/sqlite/mariadb ranked, clickhouse degraded). (b) **`chunkshop search` CLI** — the full one-shot example with `--return`/`--where`/`--json`. (c) **`SearchResult` / `search()`** — the typed return contract + the three modes + auto query-hint summarization (`summary_hints`/`summary_expand` knobs). Include a runnable CLI example.
- [ ] **Step 2:** Verify the CLI example runs against the live pg DB (paste actual output shape).
- [ ] **Step 3: commit**
```bash
git add docs/hybrid-search.md
git commit -m "docs(hybrid-search): target.fts, chunkshop search CLI, SearchResult return modes (SC-012)"
```

### ⛔ DC-FINAL — Before marking complete
- [ ] Re-read the brief. For each SC-001…SC-012, point to the test/command/doc proving it.
- [ ] `uv run --no-sync pytest -q` green (modulo documented DSN/model skips).
- [ ] Confirm NEVER items intact: search/fusion/summarization internals unmodified (signature snapshot), no third ranking leg, no forced FTS (SC-004 noop green), no Rust, no REPL, no `metric` knob. `test_no_lede_core_imports` green.
- [ ] End-of-work summary (CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS).

---

## Self-Review
- **Spec coverage:** SC-001→T1; SC-002→T3/T4; SC-003→T3; SC-004→T2 (+DC-001); SC-005→T8; SC-006→T8/T9; SC-007→T9; SC-008→T5; SC-009→T6; SC-010→T6/T7; SC-011→DC-002/DC-FINAL (+T7); SC-012→T11. All 12 covered. DCs: DC-001 after T4, DC-002 after T7, DC-003 after T10, DC-FINAL after T11.
- **Placeholder scan:** the `inline` source field name (T2) and a possible circular import (`_ALLOWED_LANGUAGES` in T1; `ensure_fts` import in T3) are flagged with concrete fallbacks to verify against source — not blanks.
- **Type consistency:** `SearchResult{chunks, summary, query}`, `search(..., return_mode=)`, `_summarize_for_query`, `_derive_query_hints` consistent across T5–T8. CLI `--return`→`return_mode` mapping consistent. `tgt.database_name`/`tgt.resolve_dsn()`/`tgt.fts.language` match the real `TargetConfig`.
