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
    schema_name: str = Field(alias="schema")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None


class HttpSource(_Base):
    type: Literal["http"]
    urls: list[str] = Field(default_factory=list)
    sitemap: Optional[str] = None


class S3Source(_Base):
    type: Literal["s3"]
    bucket: str
    prefix: str = ""


SourceConfig = Annotated[
    Union[FilesSource, JsonCorpusSource, PgTableSource, HttpSource, S3Source],
    Field(discriminator="type"),
]


class SentenceAwareChunker(_Base):
    type: Literal["sentence_aware"] = "sentence_aware"
    doc_type: Literal["prose", "code"] = "prose"


class FixedOverlapChunker(_Base):
    type: Literal["fixed_overlap"]
    window_words: int = 300
    step_words: int = 150


class HierarchyChunker(_Base):
    type: Literal["hierarchy"]
    prefix_heading: bool = True
    min_section_chars: int = 100


class NeighborExpandChunker(_Base):
    type: Literal["neighbor_expand"]
    base: "ChunkerConfig"
    window: int = 1  # seq ± window


ChunkerConfig = Annotated[
    Union[SentenceAwareChunker, FixedOverlapChunker, HierarchyChunker, NeighborExpandChunker],
    Field(discriminator="type"),
]
NeighborExpandChunker.model_rebuild()


class IdentityFramerConfig(_Base):
    type: Literal["identity"] = "identity"


FramerConfig = Annotated[
    IdentityFramerConfig,
    Field(discriminator="type"),
]


class FastembedEmbedder(_Base):
    type: Literal["fastembed"]
    model_name: str
    dim: int
    batch_size: int = 64
    threads: Optional[int] = None  # None = fastembed auto-detects (bad on shared boxes);
                                    # set to N to cap ORT intra_op_num_threads at session init


EmbedderConfig = Annotated[Union[FastembedEmbedder], Field(discriminator="type")]


class NoneExtractor(_Base):
    type: Literal["none"] = "none"


class RakeKeywordsExtractor(_Base):
    type: Literal["rake_keywords"]
    top_k: int = 10
    min_chars: int = 3


ExtractorConfig = Annotated[
    Union[NoneExtractor, RakeKeywordsExtractor], Field(discriminator="type")
]


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
    dsn_env: str = "AGE_BAKEOFF_PGRG_DSN"
    schema_name: str = Field(alias="schema")
    table: str
    overwrite: bool = False  # legacy; see `mode` for the new path
    hnsw: bool = True
    mode: Literal["overwrite", "append", "create_if_missing"] = "overwrite"
    source_tag: Optional[str] = None
    promote_metadata: list[PromoteColumn] = Field(default_factory=list)
    force_overwrite: bool = False

    @field_validator("table", "schema_name", "source_tag")
    @classmethod
    def _safe_ident(cls, v):
        if v is None:
            return v
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(
                f"table/schema/source_tag must match ^[a-z_][a-z0-9_]*$, got {v!r}"
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
