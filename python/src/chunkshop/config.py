"""Pydantic config models for chunkshop cells."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


_DSN_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class _DsnResolvable(_Base):
    """A connection target: direct `dsn` taking precedence over legacy `dsn_env`.

    `dsn` accepts a literal connection string OR `${VAR}` references expanded
    from the environment at connect time. Only the exact ``${NAME}`` form is
    substituted — a bare ``$`` (common in DSN passwords) is left untouched.
    When `dsn` is unset the legacy `os.environ[dsn_env]` lookup is used, so
    existing configs keep working unchanged.

    Security: putting a literal secret in `dsn` writes it into the config file.
    Prefer `${VAR}` or `dsn_env` when the DSN carries credentials.
    """

    dsn: Optional[str] = None
    dsn_env: Optional[str] = None

    @model_validator(mode="after")
    def _require_dsn_or_dsn_env(self):
        if not self.dsn and not self.dsn_env:
            raise ValueError("one of `dsn` or `dsn_env` is required")
        return self

    def resolve_dsn(self) -> str:
        """Return the effective connection string (dsn > dsn_env)."""
        if self.dsn:
            def _sub(m: re.Match[str]) -> str:
                name = m.group(1)
                try:
                    return os.environ[name]
                except KeyError:
                    raise ValueError(
                        f"dsn references ${{{name}}} but env var {name!r} is not set"
                    ) from None

            return _DSN_VAR.sub(_sub, self.dsn)
        return os.environ[self.dsn_env]  # KeyError if unset — preserves prior behavior

    def backend_dsn_kwargs(self) -> dict[str, str]:
        """Backend constructor kwargs for this target.

        New `dsn` path resolves (and ${VAR}-interpolates) eagerly. Legacy
        `dsn_env` path is passed through untouched so the backend keeps reading
        the env var lazily at connect() — preserving pre-0.4.3 behavior for
        callers that never set `dsn`.
        """
        if self.dsn:
            return {"dsn": self.resolve_dsn()}
        return {"dsn_env": self.dsn_env}


class FilesIncrementalSettings(_Base):
    """Opt-in incremental sync for the local ``files`` source.

    When ``cursor_path`` is set, ``chunkshop ingest`` persists a JSON cursor at
    that path and on each run reprocesses only new/changed files, pruning chunks
    for files deleted from disk. Absent → full resync every run (unchanged
    behavior). ``detect`` chooses change detection: ``hash`` (default) reads each
    file and compares a sha256 of its bytes — reliable across ``git checkout``;
    ``mtime`` skips unchanged files by ``(mtime, size)`` alone without reading
    them (fast, but unreliable on git work-trees where checkout resets mtimes).
    """
    cursor_path: str
    detect: Literal["hash", "mtime"] = "hash"


class FilesSource(_Base):
    type: Literal["files"]
    glob: str
    id_from: Literal["path", "stem", "sha1"] = "stem"
    encoding: str = "utf-8"
    incremental: Optional[FilesIncrementalSettings] = None


class CommentExtractsSource(_Base):
    """Globs source-code files and emits comments as Documents.

    Pairs with ``chunkshop.codeparse.comments``. One Document per
    comment block (default), per line, or per file — choose with
    ``granularity``. Languages are auto-detected by extension when
    ``languages`` is None; pass an explicit list to allowlist.

    The cell that consumes this source is otherwise unremarkable —
    sentence_aware chunker, prose embedder, your sink of choice. The
    point is to land code-comment rationale ("why batch_size=64?")
    in a docs KB rather than embedding it with the surrounding code.
    """
    type: Literal["comment_extracts"]
    glob: str
    # When None, auto-detect by extension. When set, drop files whose
    # detected language isn't in this allowlist. Useful for "only
    # Python comments from a polyglot repo".
    languages: Optional[list[str]] = None
    # Drop comment blocks shorter than this many characters. Default 20
    # filters ``# noqa``, ``// TODO``, single-word breadcrumbs etc.
    min_chars: int = 20
    # How to combine adjacent comments.
    #   "block"    — consecutive comment lines and each /* */ become one Document
    #   "per_line" — explode multi-line line-comment blocks into one Document per line
    #   "per_file" — one Document per file with all blocks concatenated by ``\\n\\n``
    granularity: Literal["block", "per_line", "per_file"] = "block"
    # Include Python module / class / function docstrings? Set False to
    # drop them when you've already indexed docstrings via another path.
    include_docstrings: bool = True
    # Skip pragma-style lines: shebangs (``#!``), encoding declarations
    # (``# -*- coding: utf-8 -*-``), tooling directives (``# noqa``,
    # ``# type: ignore``, ``// @ts-ignore``, ``// eslint-disable``,
    # ``//go:build``, etc.). When True (default), they don't reach the KB.
    skip_pragmas: bool = True


class JsonCorpusSource(_Base):
    type: Literal["json_corpus"]
    path: str
    documents_key: str = "documents"
    id_field: str = "id"
    content_field: str = "content"
    title_field: Optional[str] = "title"


class SessionStagingSource(_DsnResolvable):
    type: Literal["session_staging"]
    staging_table: str
    staging_schema: str = "public"
    mode: Literal["realtime", "consolidate"]
    min_age_seconds: int = Field(default=3600, ge=0)
    max_sessions: Optional[int] = Field(default=None, ge=1)

    @field_validator("staging_table", "staging_schema")
    @classmethod
    def _safe_ident(cls, v):
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(f"staging_table/staging_schema must match ^[a-z_][a-z0-9_]*$, got {v!r}")
        return v


class PgTableSource(_DsnResolvable):
    type: Literal["pg_table"]
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    # Optional timestamp column enabling cursor-based incremental sync. When set,
    # the source implements IncrementalSource with a tuple cursor of shape
    # {"after_ts": "<iso ts>", "after_id": "<id>"} so rows sharing a boundary
    # timestamp aren't silently dropped. See PgTableSource.iter_changes_since.
    updated_at_column: Optional[str] = None
    # Extra columns to pull alongside id/content/title and put into each
    # Document's metadata dict (key = column name, value = psycopg return).
    # Pair with `target.promote_metadata` to surface specific keys as typed
    # columns in the target table for fast filtered queries.
    metadata_columns: list[str] = Field(default_factory=list)


class SqliteTableSource(_DsnResolvable):
    type: Literal["sqlite_table"]
    database_name: str = Field(alias="database")   # ignored at runtime; loose parity
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)


class MariaDbTableSource(_DsnResolvable):
    type: Literal["mariadb_table"]
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)


class ClickhouseTableSource(_DsnResolvable):
    type: Literal["clickhouse_table"]
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    # `where` is documented as TRUSTED OPERATOR INPUT — raw passthrough into
    # SQL, no parameterization. Same contract as PgTableSource /
    # MariaDbTableSource / SqliteTableSource. CH SQL dialect example:
    #   where: "created_at > toDateTime('2025-01-01 00:00:00')"
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)


class HttpSource(_Base):
    type: Literal["http"]
    urls: list[str] = Field(default_factory=list)
    sitemap: Optional[str] = None
    # Depth-bounded link crawl. 0 = current behavior (fetch only the listed URLs).
    # >=1 follows that many link-hops from each seed via <a href="..."> extraction.
    crawl_depth: int = Field(default=0, ge=0, le=5)
    # By default the crawler only follows same-host links. Flip to True to
    # follow off-host links too (politely — still rate-limited, still subject
    # to max_pages).
    allow_external: bool = False
    # Politeness controls. request_delay_seconds is the minimum delay between
    # outbound requests (per source, not per host); respect_robots toggles
    # robots.txt enforcement; max_pages is a hard runaway cap.
    request_delay_seconds: float = Field(default=0.5, ge=0)
    respect_robots: bool = True
    max_pages: int = Field(default=1000, ge=1)
    user_agent: str = "chunkshop/0.6 (+https://github.com/yonk-labs/chunkshop)"


class S3Source(_Base):
    type: Literal["s3"]
    bucket: str
    prefix: str = ""
    # Optional S3-compatible endpoint (minio, R2, custom). When None, boto3
    # falls back to the default AWS endpoint per the credential's region.
    endpoint_url: Optional[str] = None


class InlineSource(_Base):
    """Library/embedded mode — the host application drives ingestion.

    No automatic iteration. The YAML still defines chunker / embedder /
    extractor / target, but the calling code (a Python service, a worker, a
    CLI tool) constructs `chunkshop.Pipeline.from_yaml(path)` and calls
    `pipeline.ingest_text(doc_id, text, metadata)` per document. Use when
    your app already knows when new content arrives — webhooks, queues,
    in-process generation — and you don't want a YAML-defined source.
    """
    type: Literal["inline"]


class LocalRawStoreConfig(_Base):
    type: Literal["local"]
    root: str


class S3RawStoreConfig(_Base):
    type: Literal["s3"]
    bucket: str
    prefix: str = ""
    endpoint_url: Optional[str] = None


RawStoreConfig = Annotated[
    Union[LocalRawStoreConfig, S3RawStoreConfig],
    Field(discriminator="type"),
]


class SyncSettings(_Base):
    """Declares how a connector source detects changes. Consumer-driven —
    chunkshop does not schedule; these values inform the consumer's orchestrator."""
    mode: Literal["full_resync", "cursor", "fingerprint"] = "full_resync"
    refresh_freq_seconds: Optional[int] = Field(default=None, ge=1)
    prune_freq_seconds: Optional[int] = Field(default=None, ge=1)


class ConnectorSource(_Base):
    """Generic plugin-source kind. Resolved at load time against the
    ``chunkshop.sources`` entry-point registry. The ``config`` dict is opaque
    to core — the plugin validates it. ``extra='forbid'`` still applies to the
    top-level keys here (type/connector/config/sync/raw_store)."""
    type: Literal["connector"]
    connector: str
    config: dict = Field(default_factory=dict)
    sync: Optional[SyncSettings] = None
    raw_store: Optional[RawStoreConfig] = None

    @field_validator("connector")
    @classmethod
    def _safe_name(cls, v):
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(f"connector name must match ^[a-z_][a-z0-9_]*$, got {v!r}")
        return v


SourceConfig = Annotated[
    Union[FilesSource, CommentExtractsSource, JsonCorpusSource, SessionStagingSource,
          PgTableSource, SqliteTableSource, MariaDbTableSource, ClickhouseTableSource,
          HttpSource, S3Source, InlineSource, ConnectorSource],
    Field(discriminator="type"),
]


class SentenceAwareChunker(_Base):
    type: Literal["sentence_aware"] = "sentence_aware"
    doc_type: Literal["prose", "code"] = "prose"
    max_chars: int = 2000
    min_chars: int = 200
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        return self.max_chars


class FixedOverlapChunker(_Base):
    type: Literal["fixed_overlap"]
    window_words: int = 300
    step_words: int = 150
    max_chars: Optional[int] = None
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        return self.max_chars

    @model_validator(mode="after")
    def _if_oversize_requires_ceiling(self):
        if self.if_oversize is not None and self.effective_max_chars() is None:
            raise ValueError(
                "fixed_overlap with if_oversize set must also set max_chars "
                "(no effective ceiling otherwise)"
            )
        return self


class HierarchyChunker(_Base):
    type: Literal["hierarchy"]
    prefix_heading: bool = True
    min_section_chars: int = 100
    max_chars: int = 2000
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        return self.max_chars


class NeighborExpandChunker(_Base):
    type: Literal["neighbor_expand"]
    base: "ChunkerConfig"
    window: int = 1  # seq ± window
    max_chars: Optional[int] = None
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        if self.max_chars is not None:
            return self.max_chars
        getter = getattr(self.base, "effective_max_chars", None)
        return getter() if getter else None

    @model_validator(mode="after")
    def _if_oversize_requires_ceiling(self):
        if self.if_oversize is not None and self.effective_max_chars() is None:
            raise ValueError(
                "neighbor_expand with if_oversize set must have an effective ceiling "
                "(set max_chars on the wrapper or on the base chunker)"
            )
        return self


class SemanticChunker(_Base):
    """Split a document at topic shifts detected by sentence-embedding similarity drops.

    `boundary_model` names a fastembed model for the small per-sentence embed pass;
    the special value `"same"` reuses the cell's main embedder instance so RAM doesn't
    double. `breakpoint_percentile` picks the distance threshold — higher = fewer
    splits, larger chunks. See `docs/chunkers.md` for tuning guidance.
    """
    type: Literal["semantic"]
    boundary_model: str = "sentence-transformers/all-MiniLM-L6-v2-int8"
    breakpoint_percentile: int = Field(default=95, ge=1, le=99)
    min_sentences_per_chunk: int = Field(default=3, ge=1)
    # Default 2000 (not 3000) — aligns with the 2026-04-21 chunker-max-chars hotfix
    # so semantic chunks respect the 512-token ceiling on bge-small/bge-base.
    max_chunk_chars: int = Field(default=2000, ge=100)
    sentence_splitter: Literal["naive", "nltk"] = "naive"
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        return self.max_chunk_chars


# --- Summarizer config (origin-agnostic; see brief SC-002, SC-005) ---


class HintExpansion(_Base):
    """Optional lede-spacy hint expansion. lemma is cheap; synonyms/similar
    require extra installs (enforced at runtime in chunkshop/hints.py)."""
    kinds: tuple[Literal["lemma", "synonyms", "similar"], ...] = ("lemma",)
    top_k: int = Field(default=5, ge=1)
    expand_weight: float = Field(default=0.5, ge=0.0)

    @field_validator("kinds")
    @classmethod
    def _kinds_nonempty(cls, v):
        if not v:
            raise ValueError("kinds must be non-empty")
        return v


class ExternalSummarizer(_Base):
    """Pull summary from a source document's metadata field (upstream-computed)."""
    mode: Literal["external"]
    field: str = "summary"


class CallableSummarizer(_Base):
    """Import a module lazily at first use; call ``function(text, **kwargs) -> str``."""
    mode: Literal["callable"]
    module: str
    function: str = "summarize"
    kwargs: dict = Field(default_factory=dict)
    hints_from_meta: Optional[str] = None
    hint_focus_from_meta: Optional[str] = None
    hint_mode_from_meta: Optional[str] = None
    expand: Optional[HintExpansion] = None


class PassthroughSummarizer(_Base):
    """Baseline: summary = original chunk. For A/B comparisons."""
    mode: Literal["passthrough"]


SummarizerConfig = Annotated[
    Union[ExternalSummarizer, CallableSummarizer, PassthroughSummarizer],
    Field(discriminator="mode"),
]


# --- Consolidator config (origin-agnostic) ---


class CallableConsolidator(_Base):
    """Import a module lazily; call ``function(text, **kwargs) -> dict``.

    The dict must be ``{"summary": str, "facts": [ {subject,predicate,object,
    support_span,confidence}, ... ]}``. Mirrors CallableSummarizer.
    """
    mode: Literal["callable"]
    module: str
    function: str = "consolidate"
    kwargs: dict = Field(default_factory=dict)


class PassthroughConsolidator(_Base):
    """Baseline: summary = episode text, facts = []. For A/B + no-LLM default off."""
    mode: Literal["passthrough"]


class LedeConsolidator(_Base):
    """Bundled: lede salient-sentence fact extractor + optional summarizer slot.

    summary is filled by the summarizer slot when set, else left empty (the
    chunker falls back to episode text). Facts below confidence_floor are dropped
    before embedding (storage lever)."""
    mode: Literal["lede"]
    summarizer: Optional[SummarizerConfig] = None
    confidence_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    max_facts: int = Field(default=10, ge=1)


class LedeSpacyConsolidator(_Base):
    """Bundled: lede+spaCy dependency-parsed SVO triples + optional summarizer."""
    mode: Literal["lede_spacy"]
    summarizer: Optional[SummarizerConfig] = None
    confidence_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    max_facts: int = Field(default=20, ge=1)
    model: str = "en_core_web_sm"


ConsolidatorConfig = Annotated[
    Union[CallableConsolidator, PassthroughConsolidator, LedeConsolidator, LedeSpacyConsolidator],
    Field(discriminator="mode"),
]


# --- Grouping strategies for HierarchicalSummaryChunker (SC-004) ---


class FixedNGrouping(_Base):
    strategy: Literal["fixed_n"] = "fixed_n"
    n: int = Field(default=5, ge=1)


class WordBudgetGrouping(_Base):
    strategy: Literal["word_budget"] = "word_budget"
    max_words: int = Field(default=2000, ge=50)


class SectionAwareGrouping(_Base):
    strategy: Literal["section_aware"] = "section_aware"


GroupingConfig = Annotated[
    Union[FixedNGrouping, WordBudgetGrouping, SectionAwareGrouping],
    Field(discriminator="strategy"),
]


class SummaryEmbedChunker(_Base):
    """Wrap any base chunker; replace each chunk's embedded_content with a summary."""
    type: Literal["summary_embed"]
    base: "ChunkerConfig"
    summarizer: SummarizerConfig
    max_chars: Optional[int] = None
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        if self.max_chars is not None:
            return self.max_chars
        getter = getattr(self.base, "effective_max_chars", None)
        return getter() if getter else None

    @model_validator(mode="after")
    def _if_oversize_requires_ceiling(self):
        if self.if_oversize is not None and self.effective_max_chars() is None:
            raise ValueError(
                "summary_embed with if_oversize set must have an effective ceiling"
            )
        return self


class ConsolidationChunker(_Base):
    """Wrap a base chunker; emit episode chunks (summary-enriched embedded_content)
    + atomic fact chunks (kind='fact') via a user-wired consolidator callable."""
    type: Literal["consolidation"]
    base: "ChunkerConfig"
    consolidator: ConsolidatorConfig
    fact_max_chars: int = Field(default=1200, ge=1)
    max_chars: Optional[int] = None
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        if self.max_chars is not None:
            return self.max_chars
        getter = getattr(self.base, "effective_max_chars", None)
        return getter() if getter else None

    @model_validator(mode="after")
    def _if_oversize_unsupported(self):
        if self.if_oversize is not None:
            raise ValueError(
                "if_oversize is not supported on the consolidation chunker: "
                "episode embedded_content is a bounded summary and facts are "
                "length-capped via fact_max_chars. Remove if_oversize."
            )
        return self


class CodeAwareChunker(_Base):
    """Split source code at function/class boundaries via the stdlib ``ast`` module.

    For ``.py`` files (or ``language='python'``) the chunker walks top-level AST
    nodes and emits one chunk per function/class. Module-level statements
    (imports, constants) gather into a leading ``module_block`` chunk. With
    ``include_imports=True`` (default), each chunk's ``embedded_content`` is
    prefixed with the file's import block so embeddings carry context like
    "uses BeautifulSoup". ``original_content`` always holds the raw source
    segment without that framing.

    For any other extension the chunker delegates to the configured
    ``if_oversize`` chunker (falling back to ``sentence_aware`` when unset).
    Malformed Python (``ast.parse`` raises ``SyntaxError``) emits one chunk
    holding the whole doc with ``strategy='code_aware_fallback'``.
    """
    type: Literal["code_aware"]
    max_chars: int = 4000  # soft cap; oversize functions stay whole unless if_oversize is set
    min_chars: int = 100   # smaller-than-this module-level statements may still emit as a block
    include_imports: bool = True
    language: Literal["python", "auto"] = "auto"
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        return self.max_chars


class SymbolAwareChunker(_Base):
    """Multi-language code chunker that splits at symbol boundaries via codeparse.

    Generalises :class:`CodeAwareChunker` (Python-only, stdlib ``ast``) to any
    language ``chunkshop.codeparse.parse_file`` understands (Python, Java, Go,
    TypeScript, JavaScript). For each top-level symbol in the file, emits one
    chunk whose ``original_content`` is the raw source slice and whose
    ``embedded_content`` optionally prepends the file's import block so an
    embedder sees framing context like "this function imports bs4".

    Granularity:

    - ``function`` (default) — one chunk per top-level function AND per
      top-level class. Methods inside a class are bundled into the class
      chunk (the class is the boundary).
    - ``class`` — one chunk per top-level class; free top-level functions are
      grouped into a single ``module_block`` chunk per file.
    - ``module`` — one chunk per file regardless of symbol count. Useful for
      very small or dotfile-style sources where per-symbol splitting is
      overkill. The chunk still carries a deterministic ``node_id``.

    Falls back to :class:`SentenceAwareChunker` when codeparse can't parse the
    document (unknown extension / no path metadata / Python syntax error /
    zero symbols). Fallback chunks are tagged ``strategy='symbol_aware_fallback'``
    with a ``fallback_reason`` metadata field.
    """
    type: Literal["symbol_aware"]
    granularity: Literal["function", "class", "module"] = "function"
    include_imports: bool = True
    max_chars: int = 8000
    if_oversize: Optional["ChunkerConfig"] = None
    # Restrict the chunker to specific codeparse language tags
    # ({"python","java","go","typescript","javascript"}). When None (default),
    # the chunker infers the language from doc.metadata.path / source_path.
    languages: Optional[list[str]] = None

    def effective_max_chars(self) -> Optional[int]:
        return self.max_chars


class HierarchicalSummaryChunker(_Base):
    """Emit base (fine) chunks plus coarse summary chunks linked by group_id."""
    type: Literal["hierarchical_summary"]
    base: "ChunkerConfig"
    summarizer: SummarizerConfig
    grouping: GroupingConfig = Field(default_factory=lambda: FixedNGrouping())
    max_chars: Optional[int] = None
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        if self.max_chars is not None:
            return self.max_chars
        getter = getattr(self.base, "effective_max_chars", None)
        return getter() if getter else None

    @model_validator(mode="after")
    def _section_aware_requires_hierarchy_base(self):
        if getattr(self.grouping, "strategy", None) == "section_aware":
            base_type = getattr(self.base, "type", None)
            if base_type != "hierarchy":
                raise ValueError(
                    f"hierarchical_summary with strategy='section_aware' requires "
                    f"base.type='hierarchy', got {base_type!r}"
                )
        return self

    @model_validator(mode="after")
    def _if_oversize_requires_ceiling(self):
        if self.if_oversize is not None and self.effective_max_chars() is None:
            raise ValueError(
                "hierarchical_summary with if_oversize set must have an effective ceiling"
            )
        return self


ChunkerConfig = Annotated[
    Union[
        SentenceAwareChunker,
        FixedOverlapChunker,
        HierarchyChunker,
        NeighborExpandChunker,
        SummaryEmbedChunker,
        ConsolidationChunker,
        HierarchicalSummaryChunker,
        SemanticChunker,
        CodeAwareChunker,
        SymbolAwareChunker,
    ],
    Field(discriminator="type"),
]
SentenceAwareChunker.model_rebuild()
FixedOverlapChunker.model_rebuild()
HierarchyChunker.model_rebuild()
NeighborExpandChunker.model_rebuild()
SemanticChunker.model_rebuild()
SummaryEmbedChunker.model_rebuild()
ConsolidationChunker.model_rebuild()
HierarchicalSummaryChunker.model_rebuild()
CodeAwareChunker.model_rebuild()
SymbolAwareChunker.model_rebuild()


class IdentityFramerConfig(_Base):
    type: Literal["identity"] = "identity"


class HeadingBoundaryFramerConfig(_Base):
    type: Literal["heading_boundary"] = "heading_boundary"
    pattern: str = r"^#+\s"
    title_from_heading: bool = True


class RegexBoundaryFramerConfig(_Base):
    type: Literal["regex_boundary"] = "regex_boundary"
    split_pattern: str
    title_pattern: Optional[str] = None
    body_starts_with_match: bool = True

    @field_validator("split_pattern", "title_pattern")
    @classmethod
    def _valid_regex(cls, v):
        if v is None:
            return v
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}")
        return v


class JSONPathFramerConfig(_Base):
    type: Literal["jsonpath"] = "jsonpath"
    row_path: str
    title_path: Optional[str] = None
    body_path: str = "$"

    @field_validator("row_path", "title_path", "body_path")
    @classmethod
    def _safe_path(cls, v):
        if v is None:
            return v
        # Allowlist: lowercase letters, digits, underscores, dots, asterisks
        if v != "$" and not re.match(r"^[a-z_0-9][a-z_0-9.*]*$", v):
            raise ValueError(
                f"path must match ^[a-z_0-9][a-z_0-9.*]*$ or be literal '$', got {v!r}"
            )
        return v


class SessionEpisodeFramerConfig(_Base):
    type: Literal["session_episode"] = "session_episode"
    max_gap_seconds: int = Field(default=1800, ge=1)
    max_turns: int = Field(default=40, ge=1)
    max_words: int = Field(default=1200, ge=50)
    boundary_on_tool: bool = True


FramerConfig = Annotated[
    Union[
        IdentityFramerConfig,
        HeadingBoundaryFramerConfig,
        RegexBoundaryFramerConfig,
        JSONPathFramerConfig,
        SessionEpisodeFramerConfig,
    ],
    Field(discriminator="type"),
]


class FastembedEmbedder(_Base):
    type: Literal["fastembed"]
    model_name: str
    dim: int
    batch_size: int = 64
    threads: Optional[int] = None  # None = fastembed auto-detects (bad on shared boxes);
                                    # set to N to cap ORT intra_op_num_threads at session init

    # YAML-driven HF pointer ("BYO embedder"). When `hf_repo` is set, chunkshop
    # registers the model_name with fastembed at config-load time using the
    # values below — no `_INT8_VARIANTS` edit, no rebuild required. When it's
    # NOT set, dispatch falls back to the existing registry (built-in fastembed
    # models, the chunkshop-registered Xenova int8 variants, etc.).
    hf_repo: Optional[str] = None
    onnx_path: Optional[str] = None
    pooling: Literal["cls", "mean"] = "cls"
    additional_files: list[str] = Field(
        default_factory=lambda: [
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "config.json",
        ]
    )

    @model_validator(mode="after")
    def _byo_fields_paired(self):
        # `hf_repo` and `onnx_path` are paired: either both set (BYO mode) or
        # both unset (registry mode). Dim must always be set.
        if (self.hf_repo is None) != (self.onnx_path is None):
            raise ValueError(
                "embedder.hf_repo and embedder.onnx_path must be set together "
                "(BYO mode) or both omitted (registry mode)."
            )
        return self


class OpenAIEmbedder(_Base):
    """Remote embedder calling an OpenAI-compatible /v1/embeddings endpoint.

    Opt-in alternative to `fastembed` (still the default). `base_url` repoints
    it at OpenAI, Azure, Voyage, Mistral, Together, or a local TEI/vLLM/Ollama
    server. `api_key_env` is the NAME of an env var holding the bearer token —
    never the key itself; omit it for keyless local servers.
    """

    type: Literal["openai"]
    model: str
    dim: int
    base_url: str = "https://api.openai.com/v1"
    api_key_env: Optional[str] = None
    batch_size: int = 64
    timeout: float = 60.0
    max_retries: int = 3

    @field_validator("base_url")
    @classmethod
    def _base_url_http(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("embedder.base_url must start with http:// or https://")
        return v

    @model_validator(mode="after")
    def _positive_bounds(self):
        if self.dim <= 0 or self.batch_size <= 0:
            raise ValueError("embedder.dim and embedder.batch_size must be > 0")
        if self.max_retries < 0:
            raise ValueError("embedder.max_retries must be >= 0")
        return self


EmbedderConfig = Annotated[
    Union[FastembedEmbedder, OpenAIEmbedder], Field(discriminator="type")
]


class NoneExtractor(_Base):
    type: Literal["none"] = "none"


class RakeKeywordsExtractor(_Base):
    type: Literal["rake_keywords"]
    top_k: int = 10
    min_chars: int = 3


class CooccurrenceExtractor(_Base):
    """Tier-1 spaCy-free co-occurrence edges. rake keyphrases = nodes; lede
    salient sentences = co-occurrence windows. Two keyphrases in the same
    salient sentence emit a weak undirected ``co_occurs`` candidate into
    ``metadata['cooccur']`` for a consumer (e.g. pg-raggraph) to materialize."""
    type: Literal["cooccurrence"]
    top_k: int = Field(default=15, ge=1)
    min_chars: int = Field(default=3, ge=1)
    max_summary_chars: int = Field(default=1000, ge=50)
    min_pair_count: int = Field(default=1, ge=1)


class LangDetectExtractor(_Base):
    type: Literal["lang_detect"]
    backend: Literal["langdetect"] = "langdetect"


class KeyBertPhrasesExtractor(_Base):
    type: Literal["keybert_phrases"]
    top_k: int = 10
    model_name: str = "all-MiniLM-L6-v2"
    keyphrase_ngram_range: tuple[int, int] = (1, 2)


class SpacyEntitiesExtractor(_Base):
    type: Literal["spacy_entities"]
    model: str = "en_core_web_sm"
    label_whitelist: list[str] = Field(
        default_factory=lambda: ["ORG", "PERSON", "GPE", "DATE", "LAW"]
    )


class LedeTopTermsExtractor(_Base):
    type: Literal["lede_top_terms"]
    n: int = Field(default=10, ge=1)
    kinds: tuple[Literal["words", "phrases"], ...] = ("words", "phrases")
    hints: Optional[Union[list[str], dict[str, float]]] = None
    hint_focus: float = Field(default=0.7, ge=0.0, le=1.0)
    hint_mode: Literal["soft", "hard"] = "soft"
    expand: Optional[HintExpansion] = None

    @field_validator("kinds")
    @classmethod
    def _kinds_nonempty(cls, v):
        if not v:
            raise ValueError("kinds must be non-empty")
        return v


class LedeReportExtractor(_Base):
    type: Literal["lede_report"]
    max_chars: int = Field(default=4000, ge=1)
    max_facts: int = Field(default=40, ge=1)
    backend: Literal["regex", "spacy", "auto"] = "regex"
    keep_headings: bool = True
    include_toc: bool = True
    tag_sources: tuple[
        Literal[
            "attributes",
            "key_facts",
            "fact_records",
            "dates",
            "amounts",
            "entities",
            "spacy_phrases",
            "search_text",
        ],
        ...,
    ] = ("attributes", "key_facts", "dates", "amounts", "entities")
    max_tag_chars: int = Field(default=240, ge=20)

    @field_validator("tag_sources")
    @classmethod
    def _tag_sources_nonempty(cls, v):
        if not v:
            raise ValueError("tag_sources must be non-empty")
        return v


class CompositeExtractor(_Base):
    type: Literal["composite"]
    extractors: list["ExtractorConfig"] = Field(default_factory=list)


class CodeSummaryExtractor(_Base):
    """Per-chunk natural-language summary for code chunks (SP-D).

    Stamps:
      - ``metadata.summary`` — a 1-3 sentence summary of every non-empty chunk.
      - ``metadata.file_summary`` — a file-level rollup, stamped only on the
        first chunk of each file (heuristic: ``chunk.metadata.start_line == 1``
        or ``chunk.metadata.symbol_type == "module"``). Disabled by setting
        ``file_summary: false``.

    Three backends:
      - ``"lede"`` (default) — chunkshop's extractive lede shim. Requires the
        ``[lede]`` extra. Falls back to ``first_n_sentences`` with a one-time
        ``RuntimeWarning`` if lede is missing.
      - ``"callable"`` — BYO summarizer; supply ``callable_path`` as
        ``"module.path:function"`` implementing
        ``summarize(text: str, **kwargs) -> str``. Useful for LLM-backed
        summarizers without coupling chunkshop to any vendor.
      - ``"first_n_sentences"`` — zero-dep regex sentence split. The whole-
        sentence boundary means actual summary length may land below
        ``max_length`` but never exceed it.

    The ``callable_path`` import happens lazily on the first ``extract`` call so
    ``load_extractor`` never triggers vendor SDK imports at config-load time.
    """

    type: Literal["code_summary"]
    backend: Literal["lede", "callable", "first_n_sentences"] = "lede"
    # ``module.path:function`` — only consulted when backend == "callable".
    callable_path: Optional[str] = None
    max_length: int = Field(default=300, ge=1)
    # When False, ``file_summary`` is never stamped (per-chunk summary only).
    file_summary: bool = True


class CodeRelationshipsExtractor(_Base):
    """Cross-file code-symbol relationship extractor (SP-C).

    Per-chunk: attaches ``metadata['callees']`` for each chunk. Corpus-level:
    accumulates symbols + call sites across all chunks in a cell run; the
    extractor's ``finalize()`` method then resolves callees to FQNs by
    unique-name (D5). The optional ``target_schema`` is consumed by the
    standalone ``write_edges()`` helper that the consumer calls after the
    cell completes — the runner itself does not invoke ``finalize`` yet
    (extractor contract is per-chunk only in v1).
    """

    type: Literal["code_relationships"]
    target_schema: Optional[str] = None
    unique_match_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    ambiguous_match_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


ExtractorConfig = Annotated[
    Union[
        NoneExtractor,
        RakeKeywordsExtractor,
        CooccurrenceExtractor,
        LangDetectExtractor,
        KeyBertPhrasesExtractor,
        SpacyEntitiesExtractor,
        LedeTopTermsExtractor,
        LedeReportExtractor,
        CompositeExtractor,
        CodeSummaryExtractor,
        CodeRelationshipsExtractor,
    ],
    Field(discriminator="type"),
]
CompositeExtractor.model_rebuild()


_ALLOWED_PROMOTE_TYPES = {"text", "text[]", "int", "bigint", "boolean", "jsonb", "timestamptz", "date"}
_PATH_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MemoryConfig(_Base):
    tier: Literal["provisional", "consolidated"]
    supersede: bool = False
    namespace: Optional[str] = None

    @field_validator("namespace")
    @classmethod
    def _safe_ns(cls, v):
        if v is None:
            return v
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(f"namespace must match ^[a-z_][a-z0-9_]*$, got {v!r}")
        return v


class PromoteColumn(_Base):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    type: str

    @field_validator("path")
    @classmethod
    def _safe_path(cls, v: str) -> str:
        if not v or not all(_PATH_SEGMENT.match(seg) for seg in v.split(".")):
            raise ValueError(
                f"path segments must match ^[A-Za-z_][A-Za-z0-9_]*$ separated by '.', got {v!r}"
            )
        return v

    @field_validator("type")
    @classmethod
    def _safe_type(cls, v: str) -> str:
        if v not in _ALLOWED_PROMOTE_TYPES:
            raise ValueError(
                f"promote_metadata type must be one of {_ALLOWED_PROMOTE_TYPES}, got {v!r}"
            )
        return v

    @property
    def column_name(self) -> str:
        """Return the Postgres column identifier for this promoted jsonb path.

        Replaces dots with double underscores and lowercases the result. Mixed-case
        input paths like ``entities.ORG`` become canonical lowercase columns
        (``entities__org``) so unquoted SELECTs across cells reach the same column
        regardless of which cell's YAML declared it. Centralized here so Tasks 11
        and 13 (sink preflight + write) both derive the identifier the same way.
        """
        return self.path.replace(".", "__").lower()


# FTS language allowlist mirrored from chunkshop.search._ALLOWED_LANGUAGES.
# A top-level `from chunkshop.search import _ALLOWED_LANGUAGES` would be circular
# because search.py (via its backend import) pulls in chunkshop.config. Keep this
# set in sync with search.py's copy; a future refactor can dedupe by having search
# import from config instead.
_ALLOWED_FTS_LANGUAGES = {
    "simple", "arabic", "armenian", "basque", "catalan", "danish", "dutch",
    "english", "finnish", "french", "german", "greek", "hindi", "hungarian",
    "indonesian", "irish", "italian", "lithuanian", "nepali", "norwegian",
    "portuguese", "romanian", "russian", "serbian", "spanish", "swedish",
    "tamil", "turkish", "yiddish",
}


class FtsConfig(_Base):
    """Opt-in full-text-search index for a target table (LD-4).

    When ``enabled=True`` the sink will create a tsvector generated column
    and a GIN index on it at table-creation time. ``language`` is the
    PostgreSQL text-search configuration name (e.g. ``"english"``); it is
    allowlisted because it is concatenated into generated-column DDL and
    cannot be a bound parameter.
    """

    enabled: bool = False
    language: str = "english"
    include_metadata_paths: list[str] = Field(default_factory=list)

    @field_validator("language")
    @classmethod
    def _lang_allowlisted(cls, v: str) -> str:
        if v not in _ALLOWED_FTS_LANGUAGES:
            raise ValueError(
                f"fts.language must be one of {sorted(_ALLOWED_FTS_LANGUAGES)}, got {v!r}"
            )
        return v

    @field_validator("include_metadata_paths")
    @classmethod
    def _metadata_paths_safe(cls, v: list[str]) -> list[str]:
        for path in v:
            if not path or not all(_PATH_SEGMENT.match(seg) for seg in path.split(".")):
                raise ValueError(
                    "fts.include_metadata_paths segments must match "
                    f"^[A-Za-z_][A-Za-z0-9_]*$ separated by '.', got {path!r}"
                )
        return v


class DocumentStoreConfig(_Base):
    """Optional 1:M document table beside the chunks table.

    This is currently implemented only by the Python Postgres sink. Non-Postgres
    Python targets reject enabled document stores, and Rust rejects
    ``target.documents.enabled: true`` until Rust/Postgres parity lands.
    """

    enabled: bool = False
    table: str = "documents"
    store_full_content: bool = True
    store_lede_report: bool = True
    promote_metadata: list[PromoteColumn] = Field(default_factory=list)
    fts: Optional[FtsConfig] = None

    @field_validator("table")
    @classmethod
    def _safe_table(cls, v: str) -> str:
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(
                f"documents.table must match ^[a-z_][a-z0-9_]*$, got {v!r}"
            )
        return v


class TargetConfig(_DsnResolvable):
    type: Literal["postgres", "sqlite", "mariadb", "clickhouse"]
    database_name: str = Field(alias="database")
    table: str
    hnsw: bool = True
    # Postgres/pgvector semantic-search metric. Ignored by non-Postgres
    # backends, which currently expose their own fixed native distance.
    vector_metric: Literal["cosine", "inner_product", "l2"] = "cosine"
    mode: Literal["overwrite", "append", "create_if_missing"] = "overwrite"
    source_tag: Optional[str] = None
    promote_metadata: list[PromoteColumn] = Field(default_factory=list)
    memory: Optional[MemoryConfig] = None
    force_overwrite: bool = False
    delete_orphans: bool = False
    # ClickHouse-specific: override the default MergeTree() ORDER BY (id) engine
    # spec. Set to "ReplacingMergeTree(created_at) ORDER BY (id)" to opt into
    # lazy dedup at merge time. Ignored on non-CH backends.
    engine: Optional[str] = None
    # Opt-in full-text-search index. When set, each sink creates the
    # appropriate backend-native FTS structure alongside the vector column:
    # Postgres→GIN tsvector index, SQLite→FTS5 external-content table,
    # MariaDB→FULLTEXT index, ClickHouse→tokenbf_v1 data-skipping index.
    fts: Optional[FtsConfig] = None
    # Optional document-level table. In Postgres this creates a
    # `{database}.{documents.table}` table with one row per source document and
    # lede summary/facts/TOC fields, linked to chunks by doc_id.
    documents: DocumentStoreConfig = Field(default_factory=DocumentStoreConfig)

    @field_validator("table", "database_name", "source_tag")
    @classmethod
    def _safe_ident(cls, v):
        if v is None:
            return v
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(
                f"table/database/source_tag must match ^[a-z_][a-z0-9_]*$, got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _append_requires_source_tag(self):
        if self.mode == "append" and not self.source_tag:
            raise ValueError("source_tag is required when mode='append'")
        if self.documents.enabled and self.type != "postgres":
            raise ValueError("target.documents is currently supported only for postgres targets")
        if self.documents.enabled and self.documents.table == self.table:
            raise ValueError("target.documents.table must differ from target.table")
        return self


class RuntimeConfig(_Base):
    omp_num_threads: int = 1
    doc_limit: Optional[int] = None
    log_path: Optional[str] = None
    heartbeat_every: int = 25
    # "text" (default) or "json" — controls the CLI's stdout log handler format.
    # JSON format emits one structured event per line; useful for log aggregators
    # (Datadog, Loki, CloudWatch, Cloud Logging).
    log_format: Literal["text", "json"] = "text"


class CellConfig(_Base):
    cell_name: str
    source: SourceConfig
    framer: FramerConfig = Field(default_factory=IdentityFramerConfig)
    chunker: ChunkerConfig
    embedder: EmbedderConfig
    extractor: ExtractorConfig = NoneExtractor()
    target: TargetConfig
    runtime: RuntimeConfig = RuntimeConfig()


def load_config(path: str | Path) -> CellConfig:
    data = yaml.safe_load(Path(path).read_text())
    return CellConfig(**data)
