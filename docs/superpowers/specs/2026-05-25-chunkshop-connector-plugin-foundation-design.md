# Connector Plugin Foundation (SP-1) + Data-Sync Expansion Program

**Date:** 2026-05-25
**Status:** active design
**Tracks:** chunkshop#18, #19, #20, #21, #22, #23, #24, #25, #26 (and #14 deferred)
**Scope of this spec:** the program decomposition (all sub-projects) + the full design of **SP-1, the plugin foundation**. SP-2/SP-3 get their own specs when they start.

## 1. Problem framing

chunkshop ships ~9 loaders, mostly tabular (`files`, `http`, `s3`, four DB tables, `json_corpus`, `session_staging`). Downstream RAG / agent-memory / search consumers want far more sources — Google Drive, GitHub, Slack, Notion, Confluence, etc. Nine GitHub issues (#18–#26) propose absorbing RAGFlow's MIT-licensed (Onyx-attributed) connector library and the incremental-sync machinery around it.

Two problems block a naive "lift it all into core" approach:

1. **The loader registry is closed to extension.** `sources/__init__.py` dispatches by `isinstance` over a closed pydantic discriminated union (`SourceConfig`, `extra="forbid"`). Adding *any* source requires editing core in three places. A third party cannot contribute a connector without patching chunkshop. The project goal — *"treat connectors like a plugin architecture; I don't want to own all the sources; others should be able to plug in and contribute"* — requires changing this structural fact.
2. **chunkshop is a library, not a service.** RAGFlow's connectors carry Redis / DB-service / job-runner coupling because RAGFlow *is* a service (its `sync_data_source.py` daemon queues sync jobs across workers). chunkshop must stay a primitive provider: *"here's a doc/data, access it with these creds/OAuth, process and store it, remember the last thing you saw, and here are the primitives to run again and diff for deltas."* The orchestration that drives those primitives at scale lives in the separate **`chunkshop_api`** repo, not here.

## 2. Settled decisions (do not re-litigate)

These were decided with the user during brainstorming on 2026-05-25:

| # | Decision | Rationale |
|---|---|---|
| D1 | **Hybrid plugin model.** Core defines an entry-point seam (`chunkshop.sources` group). First-party connectors ship as **one in-repo plugin package** (`chunkshop-connectors`), opt-in. External packages can register against the same seam. | Keeps core lean; lets community contribute without core PRs; one repo to release. |
| D2 | **Single bulk-port plugin.** Lift RAGFlow's whole `common/data_source/` tree into `chunkshop-connectors` and register all ~30 connector *names* through the seam — not N separate extras, not into core. | Speed of delivery; isolates the SaaS-API-churn maintenance tax to one opt-in package; resolves the agent-brief's "don't bloat core" concern (a separate package isn't core). |
| D3 | **Generic `connector` config kind.** Core adds exactly one new union member: `ConnectorSource` (`type: connector`, `connector: <name>`, opaque `config:` dict, `sync:` block). The plugin registers a pydantic model the registry uses to validate the `config` blob. | Cannot add 30 members to a closed union without 30 core edits; the generic kind makes the registry the single point of extension. |
| D4 | **Two-tier delivery bar for SP-2.** Bulk-lift & register all ~30 fast. A verified tier (gdrive, github, blob, rss, slack) gets full tests + mocks + docs + OAuth impls. The rest land importable/registered/smoke-tested, flagged **experimental**, graduating by pull. | Speed lever — the package never blocks on Confluence/Jira. |
| D5 | **Per-connector extras inside the plugin.** `chunkshop-connectors[gdrive]`, `[slack,notion]`, `[all]`. | Lean installs even within the plugin. |
| D6 | **Python-only for v1.** Rust cross-backend matrix unchanged; connector tests marked `python_only`. Re-evaluate after real usage. | Connectors are lifted Python; matches agent-brief default. |
| D7 | **Three-layer boundary.** (a) chunkshop *lib* = primitives; (b) chunkshop *repo, non-lib* = narrow examples + sample sync loop + connector test baseline; (c) `chunkshop_api` = production orchestration/queuing/scheduling/Redis. | Stops core from drifting toward being a service. |
| D8 | **`SourceTaskRunner` (#21) is not a core API.** It moves to an in-repo **example sync loop** (copy-me reference + connector test baseline). Production concurrency lives in `chunkshop_api`. | Per D7 — concurrency-at-scale is the service's job. |
| D9 | **`RawStore` primitive** (Sink-parallel) for raw-artifact storage. Protocol + `local` (default, zero-dep) + `s3` (reuses `[s3]` extra) backends in v1; `db` backend experimental. Raw-staging is opt-in via a `raw_store:` config block. | Connectors/uploads need the raw bytes for re-process-without-re-fetch, delta efficiency, and serve-the-original/audit. Mirrors the existing pluggable Sink pattern. |
| D10 | **Preserve existing loaders (#25).** RAGFlow lifts only ADD sources chunkshop lacks; never REPLACE. Existing-loader gaps become enhancement issues against the existing loader. | Downstream consumers depend on existing loader shapes. |

## 3. Program decomposition

| | Sub-project | Issues | Home | Gate |
|---|---|---|---|---|
| **SP-1** | **Primitives + plugin seam + RawStore** | #18 #19 #20 #22(interfaces) #24(helpers) + #19/#20 proof on s3/pg_table | core lib (`chunkshop`) | none — first |
| **SP-1b** | **Example sync loop / sample app** | #21 (demoted) | in-repo, non-lib (`examples/`) | with SP-1 |
| **SP-2** | **`chunkshop-connectors`** bulk port | #23 #25 | in-repo plugin dist | needs SP-1 |
| **SP-3** | **`files.py` rich parsing** | #26 | core lib (extras) | parallel after SP-1 |
| deferred | browser-rendered source | #14 | — | — |
| elsewhere | production orchestration | — | **`chunkshop_api` repo** | — |

This spec fully designs **SP-1 + SP-1b**. SP-2 and SP-3 get their own spec → plan cycles.

## 4. SP-1 architecture (the primitives + the seam)

All of SP-1 lives in core `chunkshop` and is **dependency-light and runs nothing on its own** — these are contracts and pure helpers the consumer's code (or `chunkshop_api`) drives.

### 4.1 Sync Protocols (#18, #20) + SyncMode (#19)

New in `chunkshop/sources/base.py`:

```python
class SyncMode(str, Enum):
    FULL_RESYNC = "full_resync"   # re-emit all; consumer dedups by content hash
    CURSOR = "cursor"             # implements IncrementalSource; consumer persists cursor
    FINGERPRINT = "fingerprint"   # enumerate all w/ per-doc fingerprint; consumer diffs

@runtime_checkable
class IncrementalSource(Protocol):
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterable[Document]: ...   # may raise StaleCursorError
    def cursor_from(self, last_document: Document) -> dict: ...

@runtime_checkable
class PrunableSource(Protocol):
    def empty_prune_cursor(self) -> dict: ...
    def iter_deleted_since(self, cursor: dict) -> Iterable[str]: ...   # yields source-ids, not Documents
```

- `Document` gains `fingerprint: str | None = None` (for FINGERPRINT-mode sources). `Document` stays frozen; add the field with a default so existing construction sites are unaffected.
- New `StaleCursorError` exception in `chunkshop/sources/base.py` (or a new `chunkshop/sources/errors.py`). Signals "server-side cursor expired → fall back to full_resync."
- Base `Source` gains `sync_mode: SyncMode = SyncMode.FULL_RESYNC` so consumers can branch. Existing loaders inherit the default → no behavior change.
- **Cursor/prune persistence is the consumer's job.** chunkshop only computes "given a cursor, here are the changes/deletes since." It never stores the cursor.

### 4.2 Entry-point registry + generic `connector` source (#23 seam, D3)

The structural change that makes connectors plugins.

- **Config** (`chunkshop/config.py`): add one union member.
  ```python
  class ConnectorSource(_Base):
      type: Literal["connector"]
      connector: str                       # registry key, e.g. "gdrive"
      config: dict = {}                     # opaque; validated by the plugin's model
      sync: SyncSettings | None = None      # mode, refresh_freq_seconds, prune_freq_seconds
      raw_store: RawStoreConfig | None = None
  ```
  `extra="forbid"` stays on `ConnectorSource` itself, but its `config` field is a free dict because the plugin owns that schema.
- **Registry** (`chunkshop/sources/registry.py`, new): discovers `importlib.metadata.entry_points(group="chunkshop.sources")`. Each entry point resolves to a factory callable `(config: dict) -> Source` and, optionally, a pydantic model class for validating `config`. Discovery is lazy + cached; a missing connector name raises a clear `UnknownConnectorError` listing what *is* installed.
- **Loader** (`chunkshop/sources/__init__.py`): add a final branch — `if isinstance(cfg, ConnectorCfg): return registry.load(cfg.connector, cfg.config)`. **No further core edits to add connectors, ever.**
- A plugin registers in its own `pyproject.toml`:
  ```toml
  [project.entry-points."chunkshop.sources"]
  gdrive = "chunkshop_connectors.gdrive:factory"
  ```

### 4.3 OAuth interfaces + helpers (#22) — interfaces in core, providers in plugin

`chunkshop/oauth/` (new), dependency-free:

- `base.py` — `OAuthProvider` Protocol (`authorization_url`, `exchange_code`, `refresh_token`, `validate_scopes`).
- `tokens.py` — `OAuthTokens` dataclass (`access_token`, `refresh_token`, `expires_at`, `scopes`, `provider`, `provider_extras`).
- `storage.py` — `OAuthTokenStorage` Protocol (interface only — storage is tenancy-scoped, so the consumer owns the impl).
- `refresh.py` — `proactive_refresh(tokens, *, leeway_minutes=5)` to avoid reactive-401 refresh races.
- `_mock.py` — `MockOAuthProvider` for tests.

**Concrete provider modules (`google.py`, `slack.py`, …) live in `chunkshop-connectors`**, not core — they carry provider SDK deps. This is a deliberate split from issue #22 as written, to keep core dep-free.

### 4.4 RawStore primitive (D9) — Sink-parallel

`chunkshop/raw_store/` (new), mirroring `chunkshop/sinks/`:

- `base.py` — `RawStore` Protocol:
  ```python
  class RawStore(Protocol):
      def put(self, doc_id: str, data: bytes, *, content_type: str, meta: dict | None = None) -> str: ...  # returns ref
      def get(self, ref: str) -> bytes: ...
      def exists(self, doc_id: str, fingerprint: str | None = None) -> bool: ...
      def delete(self, doc_id: str) -> None: ...
  ```
- `local.py` — filesystem backend, **zero-dep, the default**. Layout: `<root>/<doc_id>/<fingerprint>`.
- `s3.py` — reuses the existing `[s3]` extra (boto3).
- `db.py` — blob column on an existing backend. **Experimental tier** for v1 (land local + s3 solid first).
- `RawStoreConfig` discriminated union in `config.py` + `load_raw_store(cfg)` factory mirroring `load_sink`.
- **Opt-in.** Connectors still just `yield Document`. If a `raw_store:` block is present, the connector/upload path calls `put()` before parsing and `exists(doc_id, fingerprint)` to skip re-fetch. No `raw_store:` → behaves exactly as today.

### 4.5 Test helpers (#24)

`chunkshop/testing/` (new):

- `assert_cursor_advances(source, cursor)` — runs `iter_changes_since` twice, asserts cursor changes.
- `assert_idempotent_on_re_emit(source, cursor)` — re-running with the same cursor yields no duplicates.
- `mock_oauth_provider` pytest fixture — wraps `MockOAuthProvider`.
- Per-*provider* HTTP mock servers ship in `chunkshop-connectors` next to the connectors they mock (SP-2), not here.

### 4.6 Proof-of-seam (do not depend on the port)

SP-1 proves the Protocols against **real existing loaders**, before 30 connectors are built on them:

- `s3.py` → implement `IncrementalSource` with an **ETag cursor** (`sync_mode = CURSOR` or `FINGERPRINT`).
- `pg_table.py` → implement `IncrementalSource` with an **`updated_at` cursor**.

These are **additive enhancements** (the loader keeps its existing full-resync behavior; the Protocol methods are new surface) — allowed under D10 (#25 ADD-never-REPLACE). They satisfy the reference-impl exit criteria in #18/#19/#20.

## 5. SP-1b — example sync loop (the demoted #21)

`examples/sync_loop.py` (in-repo, **not** part of the installed lib):

- A small `asyncio.Semaphore`-bounded loop (default 5) that: for each configured source, calls `iter_changes_since`/`iter_deleted_since`, runs the chunkshop pipeline, writes vectors to a Sink and (if configured) raw bytes to a RawStore, persists the cursor to a local JSON/SQLite file, and prints a `TaskResult`-shaped summary.
- Explicitly documented as **"copy this into your service; production orchestration lives in `chunkshop_api`."**
- Doubles as the integration baseline that connector tests run against.

## 6. Testing requirements

- `SyncMode`, `IncrementalSource`, `PrunableSource` exist, `runtime_checkable`; `isinstance` works.
- End-to-end Protocol test: `empty_cursor → iter once → cursor_from → iter again → empty` (no double-yield).
- Prune test: docs [A,B,C], delete B, `iter_deleted_since → ["B"]`, cursor advances, re-run → `[]`.
- `StaleCursorError` raised + catchable.
- s3 ETag-cursor and pg_table `updated_at`-cursor reference impls pass `assert_cursor_advances` + `assert_idempotent_on_re_emit` (DB-backed tests skip if DSN unreachable, per existing convention).
- Registry: a fake entry point registers a dummy connector; `load_source({type: connector, connector: dummy})` resolves it; unknown name raises `UnknownConnectorError` listing installed names.
- RawStore: `local` round-trips `put`/`get`/`exists`/`delete`; `exists(doc_id, fingerprint)` correctly short-circuits; `s3` backend tested behind `[s3]` extra (skip if no creds).
- OAuth: `proactive_refresh` refreshes within leeway and no-ops outside it; `MockOAuthProvider` yields predictable tokens.
- Cross-backend matrix stays green; connector/Protocol tests that need infra are marked appropriately and excluded from the Rust matrix.

## 7. Out of scope (SP-1)

- Any connector implementation (that's SP-2).
- Scheduling, queuing, retry, persistence, Redis, multi-tenant credential isolation, concurrency-at-scale (that's `chunkshop_api`).
- Rust parity (D6 — Python-only v1).
- `files.py` parser layer (SP-3 / #26).
- Push-based change detection / webhooks (future Protocol if needed).
- `db` RawStore backend hardening (experimental in v1).

## 8. License & attribution (applies when SP-2 starts, noted here for the program)

- Preserve per-file Onyx MIT headers on every lifted file; add chunkshop adaptation note.
- Add top-level `NOTICE` + `THIRD-PARTY-LICENSES.md`.
- Record the RAGFlow source-commit SHA in `chunkshop-connectors/_PROVENANCE.md`.
- Only lift from `common/data_source/` (MIT); the rest of RAGFlow is Apache-2.0 with different terms.
