"""Pydantic config models for chunkshop cells."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilesSource(_Base):
    type: Literal["files"]
    glob: str
    id_from: Literal["path", "stem", "sha1"] = "stem"
    encoding: str = "utf-8"


class JsonCorpusSource(_Base):
    type: Literal["json_corpus"]
    path: str
    documents_key: str = "documents"
    id_field: str = "id"
    content_field: str = "content"
    title_field: Optional[str] = "title"


class PgTableSource(_Base):
    type: Literal["pg_table"]
    dsn_env: str
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    # Extra columns to pull alongside id/content/title and put into each
    # Document's metadata dict (key = column name, value = psycopg return).
    # Pair with `target.promote_metadata` to surface specific keys as typed
    # columns in the target table for fast filtered queries.
    metadata_columns: list[str] = Field(default_factory=list)


class SqliteTableSource(_Base):
    type: Literal["sqlite_table"]
    dsn_env: str
    database_name: str = Field(alias="database")   # ignored at runtime; loose parity
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)


class MariaDbTableSource(_Base):
    type: Literal["mariadb_table"]
    dsn_env: str
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)


class ClickhouseTableSource(_Base):
    type: Literal["clickhouse_table"]
    dsn_env: str
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


SourceConfig = Annotated[
    Union[FilesSource, JsonCorpusSource, PgTableSource, SqliteTableSource,
          MariaDbTableSource, ClickhouseTableSource, HttpSource, S3Source, InlineSource],
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


class PassthroughSummarizer(_Base):
    """Baseline: summary = original chunk. For A/B comparisons."""
    mode: Literal["passthrough"]


SummarizerConfig = Annotated[
    Union[ExternalSummarizer, CallableSummarizer, PassthroughSummarizer],
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
        HierarchicalSummaryChunker,
        SemanticChunker,
    ],
    Field(discriminator="type"),
]
SentenceAwareChunker.model_rebuild()
FixedOverlapChunker.model_rebuild()
HierarchyChunker.model_rebuild()
NeighborExpandChunker.model_rebuild()
SemanticChunker.model_rebuild()
SummaryEmbedChunker.model_rebuild()
HierarchicalSummaryChunker.model_rebuild()


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


FramerConfig = Annotated[
    Union[
        IdentityFramerConfig,
        HeadingBoundaryFramerConfig,
        RegexBoundaryFramerConfig,
        JSONPathFramerConfig,
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


EmbedderConfig = Annotated[Union[FastembedEmbedder], Field(discriminator="type")]


class NoneExtractor(_Base):
    type: Literal["none"] = "none"


class RakeKeywordsExtractor(_Base):
    type: Literal["rake_keywords"]
    top_k: int = 10
    min_chars: int = 3


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


class CompositeExtractor(_Base):
    type: Literal["composite"]
    extractors: list["ExtractorConfig"] = Field(default_factory=list)


ExtractorConfig = Annotated[
    Union[
        NoneExtractor,
        RakeKeywordsExtractor,
        LangDetectExtractor,
        KeyBertPhrasesExtractor,
        SpacyEntitiesExtractor,
        CompositeExtractor,
    ],
    Field(discriminator="type"),
]
CompositeExtractor.model_rebuild()


_ALLOWED_PROMOTE_TYPES = {"text", "text[]", "int", "bigint", "boolean", "jsonb", "timestamptz", "date"}
_PATH_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


class TargetConfig(_Base):
    type: Literal["postgres", "sqlite", "mariadb", "clickhouse"]
    dsn_env: str
    database_name: str = Field(alias="database")
    table: str
    hnsw: bool = True
    mode: Literal["overwrite", "append", "create_if_missing"] = "overwrite"
    source_tag: Optional[str] = None
    promote_metadata: list[PromoteColumn] = Field(default_factory=list)
    force_overwrite: bool = False
    delete_orphans: bool = False
    # ClickHouse-specific: override the default MergeTree() ORDER BY (id) engine
    # spec. Set to "ReplacingMergeTree(created_at) ORDER BY (id)" to opt into
    # lazy dedup at merge time. Ignored on non-CH backends.
    engine: Optional[str] = None

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
