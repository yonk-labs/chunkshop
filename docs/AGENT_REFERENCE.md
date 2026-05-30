# chunkshop — Agent Reference

Single self-contained reference for LLM agents. Read this end-to-end
and you should be able to produce a working ingest config for any
combination of source / chunker / extractor / sink without grepping
the source tree.

For human-oriented per-surface details, see [`docs/reference/`](reference/).

---

## 30-second orientation

chunkshop is a standalone Python tool that ingests text from a source,
chunks it, embeds it, optionally extracts metadata, and writes it to
a vector-search-capable database. One YAML config = one "cell" = one
end-to-end ingest. Run with `chunkshop ingest --config cell.yaml`.

The pipeline is: `Source → Chunker → Extractor → Embedder → Sink`. Every
stage is pluggable via pydantic-discriminated YAML config blocks.

---

## How to ingest a corpus — decision tree

```
Where is the data?
├── A directory of files on disk           → source: files
├── A Postgres / SQLite / MariaDB table    → source: pg_table / sqlite_table / mariadb_table
├── A ClickHouse table                     → source: clickhouse_table
├── URLs / a website                       → source: http
├── An S3-compatible bucket                → source: s3 (core) OR connector blob
├── A GitHub repo                          → source: connector github
├── A Google Drive folder                  → source: connector gdrive
├── An RSS / Atom feed                     → source: connector rss
├── Webhooks / queues / app-driven         → source: inline (use Pipeline.from_yaml + ingest_text)
└── A staging table for episodic memory    → source: session_staging

What is the data?
├── Prose / docs                           → chunker: sentence_aware (default) / hierarchy / neighbor_expand / semantic
├── Python code                            → chunker: code_aware (zero-dep) OR symbol_aware
├── Multi-language code                    → chunker: symbol_aware
└── Mix of code + docs in one cell         → two cells (one symbol_aware, one sentence_aware) — see code-and-docs-kbs cookbook

What metadata do you want?
├── Nothing                                → extractor: none (default)
├── RAKE keywords                          → extractor: rake_keywords
├── Detected language                      → extractor: lang_detect
├── Lede top terms / report                → extractor: lede_top_terms / lede_report
├── Named entities (spaCy)                 → extractor: spacy_entities
├── Code summary (lede / BYO / fallback)   → extractor: code_summary
├── Code edges (CALLS/INHERITS/IMPLEMENTS) → extractor: code_relationships
└── Multiple at once                       → extractor: composite

Where does the embedded data land?
├── Postgres + pgvector                    → target: postgres
├── SQLite + sqlite-vec                    → target: sqlite
├── MariaDB 11.7+ vector                   → target: mariadb
└── ClickHouse                             → target: clickhouse
```

---

## Available sources (discriminator + config schema)

### `files` — local glob

```yaml
source:
  type: files
  glob: "/path/to/corpus/**/*.md"
  encoding: utf-8                # default
  id_from: stem                  # path | stem | sha1
```

Parsers auto-dispatch by extension. PDF/DOCX/PPTX/XLSX/HTML require
optional extras (`chunkshop[pdf,docx,pptx,xlsx,html]` or the umbrella
`chunkshop[office]` / `chunkshop[all-parsers]`).

### `json_corpus` — one JSON object containing a documents array

Reads a **single** JSON file (not JSONL / line-delimited). The file is one
JSON object; `documents_key` indexes into it to get the array of rows. Each
row's leftover keys (everything except `id_field` / `content_field` /
`title_field`) become the chunk metadata.

```yaml
source:
  type: json_corpus
  path: /data/corpus.json
  documents_key: documents   # default — key holding the documents array
  id_field: id               # default
  content_field: content     # default
  title_field: title         # default; optional, set null to skip
```

Example corpus file:

```json
{
  "documents": [
    {"id": "1", "content": "…body…", "title": "First", "author": "ko"},
    {"id": "2", "content": "…body…", "title": "Second", "author": "mt"}
  ]
}
```

### `pg_table` / `sqlite_table` / `mariadb_table` / `clickhouse_table`

```yaml
source:
  type: pg_table           # or sqlite_table, mariadb_table, clickhouse_table
  dsn_env: SOURCE_DSN      # or `dsn:` literal
  database: public         # schema (Postgres) / database (others)
  table: articles
  id_column: id
  content_column: body
  title_column: headline   # optional
  where: "status='published'"  # optional, trusted raw SQL
  updated_at_column: updated_at   # PG-only — enables CURSOR sync mode
  metadata_columns: [author_id, category]
```

Postgres only: tuple cursor `{after_ts, after_id}` is set when
`updated_at_column` is present. See [reference](reference/source-pg-table.md).

### `http` — depth-bounded URL crawl

```yaml
source:
  type: http
  urls: ["https://example.com"]
  sitemap: https://example.com/sitemap.xml   # optional
  crawl_depth: 0           # 0..5; default 0 = fetch seeds only
  allow_external: false
  respect_robots: true
  max_pages: 1000
  request_delay_seconds: 0.5
  user_agent: "chunkshop/0.5"
```

Cursor: `{<url>: {etag, last_modified}}`. Implements `IncrementalSource`.

### `s3` — chunkshop core's S3 reader

```yaml
source:
  type: s3
  bucket: my-corpus
  prefix: docs/
  endpoint_url: https://s3.us-east-1.amazonaws.com   # optional, for R2/GCS/MinIO
# AWS credentials via boto3's standard chain
```

Cursor: `{key: etag}` map. Implements `IncrementalSource`.

### `inline` — host-driven ingest

```yaml
source:
  type: inline
# No automatic iteration. Drive from Python:
#   pipeline = chunkshop.Pipeline.from_yaml(path)
#   pipeline.ingest_text(doc_id, text, metadata)
```

### `session_staging` — episodic memory staging

```yaml
source:
  type: session_staging
  dsn_env: MEMORY_DSN
  schema: memory_staging
  session_id_filter: <opt>     # tenancy-scoped
```

See `docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md`.

### `connector` — plugin-discovered sources

```yaml
source:
  type: connector
  connector: <name>            # entry-point name; ^[a-z_][a-z0-9_]*$
  config: { ... }              # opaque to core; plugin validates
  sync: { mode: cursor }       # optional, informs consumer's scheduler
  raw_store:                   # optional — stage raw bytes
    type: local
    root: /var/lib/chunkshop/raw
```

Connectors live in the separate `chunkshop-connectors` package. Verified
connectors (production-ready): `blob`, `rss`, `github`, `gdrive`.
Experimental stubs (registered names, no implementation): see
[`docs/reference/experimental-connectors.md`](reference/experimental-connectors.md)
for the full list.

#### `connector: blob` config

```yaml
config:
  bucket: my-bucket
  prefix: docs/
  endpoint_url: https://s3.us-east-1.amazonaws.com   # optional
  region: us-east-1                                  # optional
  access_key: ${AWS_KEY}                             # optional (boto3 chain)
  secret_key: ${AWS_SECRET}                          # optional
```

#### `connector: rss` config

```yaml
config:
  url: https://example.com/feed.xml
  timeout: 30                  # optional
  user_agent: chunkshop/0.5    # optional
```

#### `connector: github` config

```yaml
config:
  owner: octocat
  repo: Hello-World
  branch: main                 # default
  paths_glob: ["**/*.py", "**/*.md"]    # optional
  token: ${GITHUB_TOKEN}       # optional (else $GITHUB_TOKEN)
```

Cursor: `{after_commit_sha}`. 422 on `/compare` raises `StaleCursorError`.

#### `connector: gdrive` config

```yaml
config:
  folder_id: 0Bxxx             # OR `query`
  scopes:
    - https://www.googleapis.com/auth/drive.readonly
  oauth_tokens: ${GDRIVE_OAUTH_TOKENS}   # JSON-serialized OAuthTokens
```

Cursor: `{page_token}`. Only text MIMEs + Google Docs (exported as text).

---

## Available chunkers (discriminator + config schema)

### `sentence_aware` (default — prose)

```yaml
chunker:
  type: sentence_aware
  doc_type: prose          # prose | code
  max_chars: 2000
  min_chars: 200
  if_oversize: <chunker>   # optional fallback
```

### `fixed_overlap`

```yaml
chunker:
  type: fixed_overlap
  window_words: 300
  step_words: 150
  max_chars: <int>         # required if if_oversize set
```

### `hierarchy` (markdown sections)

```yaml
chunker:
  type: hierarchy
  prefix_heading: true     # prepends heading to embedded_content
  min_section_chars: 100
  max_chars: 2000
```

### `neighbor_expand` (wrap a base chunker)

```yaml
chunker:
  type: neighbor_expand
  base:                    # nested chunker config
    type: sentence_aware
  window: 1                # seq ± window
  max_chars: <int>
```

### `semantic` (topic-shift split)

```yaml
chunker:
  type: semantic
  boundary_model: "sentence-transformers/all-MiniLM-L6-v2-int8"
  # or "same" — reuse the cell's main embedder instance
  breakpoint_percentile: 95
  min_sentences_per_chunk: 3
  max_chunk_chars: 2000
  sentence_splitter: naive   # naive | nltk
```

### `summary_embed` (replace embedded_content with summary)

```yaml
chunker:
  type: summary_embed
  base: { type: sentence_aware }
  summarizer: { mode: external, key: summary }
```

### `consolidation` (merge small chunks)

```yaml
chunker:
  type: consolidation
  base: { type: sentence_aware }
  ...
```

### `hierarchical_summary` (base + coarse summary chunks)

```yaml
chunker:
  type: hierarchical_summary
  base: { type: hierarchy }
  summarizer: { mode: passthrough }
  grouping: { strategy: fixed_n, n: 5 }
```

### `code_aware` (Python AST)

```yaml
chunker:
  type: code_aware
  max_chars: 4000
  include_imports: true
  language: auto           # python | auto
  if_oversize: <chunker>
```

Python only. Zero runtime deps (stdlib `ast`).

### `symbol_aware` (multi-language)

```yaml
chunker:
  type: symbol_aware
  granularity: function    # function | class | module
  include_imports: true
  max_chars: 8000
  languages: null          # or ["python", "java", ...]
```

Languages (10, all via real tree-sitter grammars in `chunkshop[code]`):
Python, Java, Go, TypeScript, JavaScript, Rust, C, C++, C#, Ruby. When the
`[code]` extra is absent, a regex fallback parser is used instead. Stamps
`metadata.symbol_name`, `fqn`, `symbol_type`, `start_line`, `end_line`,
`language`, `node_id`.

---

## Available extractors (discriminator + config schema)

### `none` (default)

```yaml
extractor: { type: none }
```

### `rake_keywords`

```yaml
extractor:
  type: rake_keywords
  top_k: 10
  min_chars: 3
```

### `lang_detect`

```yaml
extractor:
  type: lang_detect
  backend: langdetect
```

### `keybert_phrases`

```yaml
extractor:
  type: keybert_phrases
  top_k: 10
  model_name: all-MiniLM-L6-v2
  keyphrase_ngram_range: [1, 2]
```

### `spacy_entities`

```yaml
extractor:
  type: spacy_entities
  model: en_core_web_sm
  label_whitelist: [ORG, PERSON, GPE, DATE, LAW]
```

### `lede_top_terms`

```yaml
extractor:
  type: lede_top_terms
  n: 10
  kinds: [words, phrases]
  hints: null              # or list[str] or dict[str, float]
  hint_focus: 0.7
  hint_mode: soft          # soft | hard
  expand: null             # or HintExpansion config
```

### `lede_report`

```yaml
extractor:
  type: lede_report
  max_chars: 4000
  max_facts: 40
  backend: regex           # regex | spacy | auto
  keep_headings: true
  include_toc: true
```

### `composite`

```yaml
extractor:
  type: composite
  extractors:
    - { type: rake_keywords, top_k: 10 }
    - { type: lang_detect }
    - { type: code_summary, backend: lede }
```

### `code_summary`

```yaml
extractor:
  type: code_summary
  backend: lede            # lede | callable | first_n_sentences
  callable_path: null      # "module.path:function" if backend=callable
  max_length: 300
  file_summary: true
```

Stamps `metadata.summary` on every chunk; optionally `metadata.file_summary`
on the first chunk of each file.

### `code_relationships`

```yaml
extractor:
  type: code_relationships
  target_schema: null
  unique_match_confidence: 0.9
  ambiguous_match_confidence: 0.5
```

Stamps `metadata.callees` per chunk. Use `finalize()` + `write_edges()`
to materialize the `code_edges` table.

---

## Available embedders (discriminator + config schema)

Only one type today: `fastembed`.

```yaml
embedder:
  type: fastembed
  model_name: "BAAI/bge-small-en-v1.5"     # or "Xenova/bge-small-en-v1.5-int8" etc.
  dim: 384                                  # MUST match the model
  batch_size: 64
  threads: null                             # null = fastembed auto-detect (bad on shared boxes)
  # BYO mode — register a HF model not in fastembed's default registry:
  hf_repo: null                             # if set, onnx_path must also be set
  onnx_path: null
  pooling: cls                              # cls | mean
  additional_files:
    - tokenizer.json
    - tokenizer_config.json
    - special_tokens_map.json
    - config.json
```

Chunkshop ships these int8 variants registered out of the box:

- `Xenova/bge-small-en-v1.5-int8` (dim 384) — fast, smaller footprint
- `Xenova/bge-base-en-v1.5-int8` (dim 768) — **the shipped default** (MTEB-backed; beats bge-small by ~3–5 pts)
- `Xenova/bge-large-en-v1.5-int8` (dim 1024) — high-quality

See `docs/embedder-catalogue.md` for the full list + benchmarks.

---

## Available sinks (target types)

### `postgres` (pgvector)

```yaml
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: my_kb         # schema name
  table: chunks
  mode: overwrite         # overwrite | append | create_if_missing
  source_tag: my_source   # required if mode=append
  hnsw: true
  vector_metric: cosine   # cosine | inner_product | l2
  force_overwrite: false  # required to drop a table with rows from a different source_tag
  delete_orphans: false
  fts:                    # optional FTS index
    enabled: true
    language: english
  documents:              # optional per-document table (PG only)
    enabled: false
    table: documents
  promote_metadata:       # jsonb metadata → real columns
    - { path: symbol_name, type: text }
    - { path: fqn,         type: text }
    - { path: start_line,  type: int }
    - { path: summary,     type: text }
```

`promote_metadata` types: `text`, `text[]`, `int`, `bigint`, `boolean`,
`jsonb`, `timestamptz`, `date`.

### `sqlite`

```yaml
target:
  type: sqlite
  dsn: file:my.db
  database: main         # ignored, loose parity with PG
  table: chunks
  mode: overwrite
```

Requires `chunkshop[sqlite]`.

### `mariadb`

```yaml
target:
  type: mariadb
  dsn_env: MARIADB_DSN
  database: my_kb
  table: chunks
  mode: overwrite
```

Requires MariaDB 11.7+ for native vector support + `chunkshop[mariadb]`.

### `clickhouse`

```yaml
target:
  type: clickhouse
  dsn_env: CH_DSN
  database: my_kb
  table: chunks
  engine: "ReplacingMergeTree(created_at) ORDER BY (id)"   # optional override
```

Requires `chunkshop[clickhouse]`.

---

## Hybrid search shape

Use the CLI or the `chunkshop.search_common.search()` Python API.

### CLI

```bash
chunkshop search \
    --config cell.yaml \
    --query "vector embedding" \
    --k 10 \
    --return chunks            # chunks | summary | summary+chunks
    --legs semantic,fts \
    --where source=my_source \
    --where metadata.section=intro \
    --by-symbol HttpSource     # optional — chunks where symbol_name matches
    --vector-metric cosine \
    --json                     # optional, JSON output
```

### Python

```python
from chunkshop.search_common import search
from chunkshop.embedders import load_embedder
from chunkshop.config import load_config

cfg = load_config("cell.yaml")
emb = load_embedder(cfg.embedder)
qv = emb.embed(["vector embedding"])[0]

result = search(
    cfg.target.resolve_dsn(),
    schema=cfg.target.database_name,
    table=cfg.target.table,
    query="vector embedding",
    query_vec=qv,
    k=10,
    legs=("semantic", "fts"),
    where={"source": "my_source"},
    return_mode="chunks",
    vector_metric="cosine",
)
for hit in result.chunks:
    print(hit.score, hit.doc_id, hit.text[:80])
```

### Result shape

```python
@dataclass
class SearchResult:
    query: str
    summary: str | None
    chunks: list[Hit]

@dataclass
class Hit:
    doc_id: str
    seq_num: int
    score: float
    text: str
    legs: list[str]                # ["semantic", "fts"] — which legs hit this row
    metadata: dict
```

---

## End-to-end recipe: ingest a code repo + search by symbol

```yaml
# code_repo.yaml
cell_name: my_repo
source:
  type: connector
  connector: github
  config:
    owner: yonk-labs
    repo: chunkshop
    branch: main
    paths_glob: ["**/*.py"]
    # token from $GITHUB_TOKEN
  sync: { mode: cursor }
chunker:
  type: symbol_aware
  granularity: function
  include_imports: true
extractor:
  type: composite
  extractors:
    - { type: code_summary, backend: lede }
    - { type: code_relationships }
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: chunkshop_kb
  table: chunks
  mode: overwrite
  source_tag: chunkshop_main
  promote_metadata:
    - { path: symbol_name, type: text }
    - { path: fqn,         type: text }
    - { path: symbol_type, type: text }
    - { path: language,    type: text }
    - { path: path,        type: text }
    - { path: summary,     type: text }
    - { path: start_line,  type: int }
    - { path: end_line,    type: int }
```

```bash
export CHUNKSHOP_DSN=postgresql://user:pass@localhost:5432/chunkshop_kb
export GITHUB_TOKEN=ghp_…

# Ingest
chunkshop ingest --config code_repo.yaml

# Symbol-filtered semantic search
chunkshop search --config code_repo.yaml \
    --query "fetch URL with conditional headers" \
    --by-symbol "HttpSource*" \
    --k 5

# Who calls HttpSource.iter_changes_since?
chunkshop impact-of --config code_repo.yaml \
    --fqn chunkshop.sources.http.HttpSource.iter_changes_since \
    --direction callers \
    --depth 2
```

For full details: [`docs/tutorial-code-repo-ingest.md`](tutorial-code-repo-ingest.md)
and [`docs/cookbook/code-search.md`](cookbook/code-search.md).

---

## End-to-end recipe: ingest a Google Drive folder + summarize

```yaml
# gdrive.yaml
cell_name: company_docs
source:
  type: connector
  connector: gdrive
  config:
    folder_id: 0BabcXYZ123
    scopes: ["https://www.googleapis.com/auth/drive.readonly"]
    oauth_tokens: ${GDRIVE_OAUTH_TOKENS}
  sync: { mode: cursor, refresh_freq_seconds: 1800 }
chunker:
  type: hierarchy
  prefix_heading: true
  max_chars: 2000
extractor:
  type: composite
  extractors:
    - { type: rake_keywords, top_k: 8 }
    - { type: lang_detect }
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: company_kb
  table: chunks
  mode: append
  source_tag: gdrive
  promote_metadata:
    - { path: drive_id, type: text }
    - { path: mime_type, type: text }
```

Bootstrap OAuth via:

```bash
python python/connectors/examples/e2e_gdrive_real_flow.py \
    'https://drive.google.com/drive/folders/<ID>'
# Caches tokens at ~/.chunkshop/gdrive-tokens.json
```

Then export `GDRIVE_OAUTH_TOKENS` as JSON-encoded
`{access_token, refresh_token, expires_at, scopes, provider}`:

```bash
export GDRIVE_OAUTH_TOKENS=$(cat ~/.chunkshop/gdrive-tokens.json)
chunkshop ingest --config gdrive.yaml
chunkshop search --config gdrive.yaml \
    --query "vacation policy" \
    --return summary+chunks
```

---

## Discovery surface

### Listing connectors

```python
from chunkshop.sources import registry

registry.available_connectors()
# -> ['airtable', 'asana', 'bitbucket', 'blob', 'box', 'confluence',
#     'dingtalk', 'discord', 'dropbox', 'gcs', 'gdrive', 'github',
#     'gitlab', 'gmail', 'imap', 'jira', 'moodle', 'notion', 'oci',
#     'r2', 'rest_api', 'rss', 'seafile', 'sharepoint', 'teams',
#     'webdav', 'zendesk']
```

### Tier inspection

```python
from chunkshop_connectors._tier import tier_of
from chunkshop_connectors.blob.connector import BlobConnector
from chunkshop_connectors.notion import Connector as NotionConnector

tier_of(BlobConnector)    # "verified"
tier_of(NotionConnector)  # "experimental"
```

### CLI commands available

```bash
chunkshop --help
# Commands:
#   init           Interactive scaffold for a new cell YAML
#   validate       Validate a YAML config without running it (exit 0 if valid)
#   ingest         Run a single cell ingest from YAML
#   prefetch       Download the config's embedder model so first ingest never blocks
#   orchestrate    Spawn N cell processes from a config dir
#   bakeoff        Run a multi-backend chunker x embedder matrix bakeoff
#   search         Hybrid-search a cell's target (with --by-symbol)
#   fact-search    Search a cell's facts; each result carries its chunk/doc breadcrumb
#   impact-of      Walk code_edges for callers/callees of an FQN
#                  (--edge-type CALLS, plus --edge-kind; --edge-kind wins when both given)
#   eval validate  Validate an eval matrix without running retrieval or judges
#   eval plan      Expand an eval matrix into a concrete execution manifest
```

### Known gaps (CLI discovery)

chunkshop does **not** ship a `chunkshop list sources` / `list
chunkers` / `list extractors` command. Discoverability today is via:

- `python -c "from chunkshop.sources import registry; print(registry.available_connectors())"`
- This document.
- The pydantic discriminated unions in `chunkshop/config.py`:
  `SourceConfig`, `ChunkerConfig`, `ExtractorConfig`, `EmbedderConfig`.
- `chunkshop.config.CellConfig.model_json_schema()` produces a full
  JSON Schema for a cell config — usable by agents to validate
  generated YAML before running.

---

## When things go wrong

### `chunkshop_connectors._stub.StubError`

```
connector 'notion' is registered as experimental but not yet implemented.
See docs/connectors/_status.md.
```

You configured an experimental-tier connector. There's no implementation
yet. Either use a verified connector (`blob`, `rss`, `github`,
`gdrive`) or wait for the lift. For S3-compatible storage (`r2`,
`gcs`, `oci`), use `connector: blob` with an `endpoint_url`.

### `chunkshop.sources.base.StaleCursorError`

```
github /compare returned 422 for /repos/x/y/compare/<sha>...main; the
cursor refers to a SHA no longer reachable from main. Drop the cursor
and resync.
```

Branch was force-pushed or rebased. Treat as "fall back to a full
resync" — pass `iter_documents()` or `iter_changes_since(empty_cursor())`.

### `chunkshop.sources.registry.UnknownConnectorError`

```
unknown connector 'foo'; install a plugin that registers it. Installed
connectors: blob, gdrive, github, rss, ...
```

The named connector isn't registered by any installed plugin. Either
typo, or you forgot to `pip install chunkshop-connectors`.

### `pydantic.ValidationError`

YAML has a typo. Every chunkshop config block uses `extra="forbid"`,
so an extra key fails at config-load. Read the error message — pydantic
v2 points at the exact path.

### `RuntimeError: PDF parsing requires pypdf. Install with pip install chunkshop[pdf]`

Optional extra not installed. Install with the corresponding extra:

| If you see | Install |
|---|---|
| `requires pypdf` | `chunkshop[pdf]` |
| `requires python-docx` | `chunkshop[docx]` |
| `requires python-pptx` | `chunkshop[pptx]` |
| `requires openpyxl` | `chunkshop[xlsx]` |
| `requires beautifulsoup4` | `chunkshop[html]` |
| `requires boto3` (S3) | `chunkshop[s3]` |
| `requires lede` | `chunkshop[lede]` |
| `requires tree-sitter` | `chunkshop[code]` |

### `ValueError: gdrive: oauth_tokens missing from config and $GDRIVE_OAUTH_TOKENS env var is unset`

Set `GDRIVE_OAUTH_TOKENS` to a JSON-encoded
`{access_token, refresh_token, expires_at, scopes, provider}` dict. Use
`python/connectors/examples/e2e_gdrive_real_flow.py` to mint tokens.

### `chunkshop search --by-symbol HttpSource` returns nothing

The `symbol_name` column isn't promoted. Add to your cell's target:

```yaml
target:
  promote_metadata:
    - { path: symbol_name, type: text }
```

Re-ingest. `--by-symbol` filters on a real column, not jsonb metadata.

### `chunkshop impact-of` returns "(no edges found)"

You ingested but didn't materialize edges. Either:

- Re-ingest with the SP-E runner (commit `cd7a013`) — it auto-runs
  `extractor.finalize()` + `write_edges()`.
- Manually run `write_edges_schema(dsn, schema=…)` +
  `write_edges(extractor, dsn=…, schema=…, project_id=cell_name)`
  after `run_cell`.

---

## JSON Schema (machine-readable)

To get a full JSON Schema for the cell config, agents can run:

```python
from chunkshop.config import CellConfig
import json
schema = CellConfig.model_json_schema()
print(json.dumps(schema, indent=2))
```

That schema is the authoritative source for what fields are accepted,
their types, and their defaults. Agents generating YAML can validate
their output against it before submitting:

```python
import yaml
from pydantic import ValidationError
from chunkshop.config import CellConfig

try:
    cell = CellConfig.model_validate(yaml.safe_load(generated_yaml))
except ValidationError as e:
    print("YAML is invalid:", e.errors())
```

---

## Load-bearing details (don't forget these)

1. **`Document` is frozen.** `chunkshop.sources.base.Document` is a
   frozen dataclass. Sources yield them; nothing in the pipeline
   mutates them.
2. **`Chunk` has TWO text fields.** `original_content` is raw;
   `embedded_content` is what gets embedded (may differ — e.g.
   `hierarchy` prepends a heading).
3. **`extra="forbid"` on every config model.** Typos blow up at
   config-load.
4. **Identifier validation by regex.** `table`, `database`,
   `source_tag`, `promote_metadata.path` segments all match
   `^[a-z_][a-z0-9_]*$` (or path-shaped equivalents) — SQL injection
   prevention by allowlist.
5. **Cursor merging.** Consumers persist a single dict cursor;
   `chunkshop.testing.merge_cursor(source, prev, docs)` does the right
   thing for both map-style (S3, HTTP) and monotonic (pg, github,
   gdrive) cursor shapes.
6. **OAuth token redaction.** `OAuthTokens.__repr__` redacts both
   tokens; provider classes redact `client_secret`. A naive
   `log.debug(tokens)` won't leak.
7. **Thread discipline.** `chunkshop.runner.run_cell` sets OMP /
   MKL / OpenBLAS env vars from `runtime.omp_num_threads` BEFORE any
   numpy/ONNX import. `embedder.threads` caps ORT's
   `intra_op_num_threads`. Rule: `orchestrate --concurrency N ×
   embedder.threads ≈ physical cores`.
8. **Source column is write-once on ON CONFLICT.** When two cells
   collide on `(doc_id, seq_num)`, the first writer's `source_tag`
   wins forever.
9. **`mode: append` requires `source_tag`.** Validated at config-load.
10. **The `where` clauses are TRUSTED RAW SQL.** No parameterization.
    Don't accept user input here.

---

## Where to find things in the repo

| What | Path |
|---|---|
| Pipeline runner | `python/src/chunkshop/runner.py` |
| Config models | `python/src/chunkshop/config.py` |
| Source registry | `python/src/chunkshop/sources/registry.py` |
| Source protocol | `python/src/chunkshop/sources/base.py` (`Document`, `Source`, `IncrementalSource`, `PrunableSource`, `StaleCursorError`) |
| Chunker registry | `python/src/chunkshop/chunkers/__init__.py` |
| Extractor registry | `python/src/chunkshop/extractors/__init__.py` |
| Sink registry | `python/src/chunkshop/sinks/__init__.py` |
| Connector plugin pkg | `python/connectors/src/chunkshop_connectors/` |
| Verified connectors | `chunkshop_connectors.{blob,rss,github,gdrive}` |
| OAuth providers | `chunkshop_connectors.oauth.google` (+ Protocol in `chunkshop.oauth`) |
| Hermetic mocks | `chunkshop_connectors.testing.mocks.{blob,rss,github,gdrive}` |
| CLI | `python/src/chunkshop/cli.py` |
| Search common | `python/src/chunkshop/search_common.py` |
| Examples | `python/examples/`, `python/connectors/examples/` |
| Tests | `python/tests/chunkshop/`, `python/connectors/tests/` |

---

## See also

- [`docs/CHANGES-2026-05-25.md`](CHANGES-2026-05-25.md) — full session changelog
- [`docs/reference/README.md`](reference/README.md) — per-surface reference index
- [`docs/tutorial-code-repo-ingest.md`](tutorial-code-repo-ingest.md) — step-by-step walkthrough
- [`docs/cookbook/`](cookbook/) — task-oriented recipes
- [`docs/architecture.md`](architecture.md) — pipeline shape, design choices
- [`docs/embedder-catalogue.md`](embedder-catalogue.md) — embedder picks + benchmarks
- [`CLAUDE.md`](../CLAUDE.md) — repo conventions, load-bearing details for contributors
