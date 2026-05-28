"""``code_relationships`` extractor (SP-C).

Two-phase contract:

PHASE 1 (per-chunk, via :meth:`extract`)
    For each chunk text the extractor is handed it re-parses through
    :func:`chunkshop.codeparse.parse_text` and attaches
    ``metadata['callees'] = [{name, line, snippet, resolved_intra_file}, ...]``.
    The text passed in is the chunk's ``original_content`` — typically the
    body of one symbol when the ``symbol_aware`` chunker (SP-B) is upstream,
    but the extractor also works on any code-shaped string.

    Tags are always empty: call info is structured metadata, not a
    flat-string tag.

PHASE 2 (corpus-level, via :meth:`finalize`)
    After every chunk in a cell has been ``extract()``'d, the extractor
    holds a global per-fqn symbol map plus a list of unresolved call
    sites (caller_fqn, callee_name). ``finalize()`` walks these and
    emits one edge per resolution attempt:

      - exactly one corpus symbol matches the callee name → ``confidence
        = unique_match_confidence`` (default 0.9), ``resolved = True``
      - multiple symbols match → one edge per candidate, ``confidence =
        ambiguous_match_confidence`` (default 0.5), ``resolved = False``
      - zero matches (external library call) → no edge emitted

    INHERITS / IMPLEMENTS edges from class declarations are also
    resolved here against the same global symbol map.

The runner does NOT currently call ``finalize()``: extractors are
per-chunk in v1. Consumers wanting the cross-file edges run::

    write_edges_schema(dsn, schema)           # idempotent DDL
    write_edges(extractor, dsn, schema, ...)  # write finalize() output

after ``run_cell`` returns.

Why a separate ``write_edges`` rather than baking the write into
``finalize`` itself? Two reasons. (1) Keeps the extractor pure-CPU and
testable without a Postgres connection. (2) Lets the consumer batch
edges across multiple cells before writing — a per-cell write would
double-insert when two cells share a corpus.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from chunkshop.codeparse import Symbol, build_fqn, code_symbol_node_id
from chunkshop.codeparse.tree_sitter_wrapper import parse_text
from chunkshop.config import CodeRelationshipsExtractor as Cfg
from chunkshop.extractors.result import ExtractResult

# ---------------------------------------------------------------------------
# CS-2: typed EdgeKind ontology
# ---------------------------------------------------------------------------
#
# The 12-value vocabulary is ported verbatim from codegraph's `EdgeKind`
# TypeScript union. This is additive — the legacy uppercase `edge_type`
# column stays untouched; `edge_kind` is the new typed source-of-truth
# column that future readers (CS-1, CS-3) populate from per-language AST.

from typing import Literal

EdgeKind = Literal[
    "contains", "calls", "imports", "exports",
    "extends", "implements", "references",
    "type_of", "returns", "instantiates",
    "overrides", "decorates",
]

EDGE_KINDS: tuple[EdgeKind, ...] = (
    "contains", "calls", "imports", "exports",
    "extends", "implements", "references",
    "type_of", "returns", "instantiates",
    "overrides", "decorates",
)

# Mapping from the 3 legacy uppercase edge_type values this extractor
# emits today to their codegraph EdgeKind equivalents. CS-1 will populate
# the other 9 kinds when it ports the 20-language extractor stack; until
# then they're valid against the CHECK constraint but no code path writes
# them.
_EDGE_TYPE_TO_KIND: dict[str, EdgeKind] = {
    "CALLS": "calls",
    "INHERITS": "extends",
    "IMPLEMENTS": "implements",
}


def edge_type_to_kind(edge_type: str) -> EdgeKind:
    """Translate a legacy uppercase ``edge_type`` value into its EdgeKind.

    Raises ``ValueError`` on unknown values so a typo in a new emission
    site fails loudly instead of silently writing NULL.
    """
    try:
        return _EDGE_TYPE_TO_KIND[edge_type]
    except KeyError:
        raise ValueError(
            f"unknown edge_type {edge_type!r} — must be one of "
            f"{sorted(_EDGE_TYPE_TO_KIND)}"
        ) from None


# ---------------------------------------------------------------------------
# CS-5: provenance ontology
# ---------------------------------------------------------------------------
#
# The 3-value vocabulary is ported from codegraph's `Edge.provenance` field
# (renaming codegraph's `'tree-sitter'` → `'ast'` to leave room for future
# non-tree-sitter AST sources). Additive — every existing emission path is
# AST-derived, so the chokepoint hardcodes `'ast'`. CS-3 synthesizers will
# emit `'heuristic'` through their own code path (not through finalize._emit)
# with a `{synthesizedBy: <channel>}` provenance_metadata payload.

Provenance = Literal["ast", "scip", "heuristic"]

PROVENANCES: tuple[Provenance, ...] = ("ast", "scip", "heuristic")


# Inheritance / implementation regexes. We carry these in the extractor
# rather than punching them into SP-A's ParseResult because SP-A's surface
# is frozen for v1 and these are SP-C-only signals. The patterns are
# intentionally narrow (single base class / interface) — overload resolution
# is the cross-file resolver's problem.
_PY_INHERIT_RE = re.compile(
    r"^[ \t]*class\s+(?P<name>\w+)\s*\(\s*(?P<base>\w+(?:\s*,\s*\w+)*)\s*\)\s*:",
    re.MULTILINE,
)

_JAVA_INHERIT_RE = re.compile(
    r"^[ \t]*(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+)*"
    r"class\s+(?P<name>\w+)"
    r"(?:\s+extends\s+(?P<base>\w+))?"
    r"(?:\s+implements\s+(?P<impls>[\w,\s]+))?",
    re.MULTILINE,
)


def _auto_detect_language(text: str) -> Optional[str]:
    """Cheap heuristic for callers that didn't supply a language.

    Used when ``extract`` is invoked without an explicit ``language``
    kwarg (e.g., by the runner today, which calls ``extract(text)``
    positionally). Coverage is best-effort; misclassification falls
    through to a no-op extraction rather than a crash.
    """
    # Java's `class X` declaration is the most specific — check it first.
    if re.search(
        r"^\s*(?:public|private|protected)\s+(?:abstract\s+|final\s+|static\s+)*"
        r"(?:class|interface)\s+\w+",
        text,
        re.MULTILINE,
    ):
        return "java"
    if re.search(r"^\s*func\s+\w+", text, re.MULTILINE):
        return "go"
    if re.search(r"^\s*(?:export\s+)?function\s+\w+", text, re.MULTILINE):
        return "javascript"
    # Python last because `def`/`class` keywords overlap with other langs;
    # the multiline anchor is what disambiguates.
    if re.search(r"^\s*(?:def|class)\s+\w+", text, re.MULTILINE):
        return "python"
    return None


def _key(fqn: str) -> str:
    """Last dotted segment — the bare symbol name used for resolution."""
    return fqn.rsplit(".", 1)[-1]


class CodeRelationshipsExtractor:
    """See module docstring."""

    # Opt-in signal: the runner reads this flag and, when True, passes
    # ``source_path=`` / ``language=`` kwargs derived from chunk metadata
    # into ``extract``. Other extractors that satisfy ``Extractor(Protocol)``
    # don't carry this attribute and the runner calls them with text only —
    # which keeps the Protocol surface (``extract(text) -> ExtractResult``)
    # untouched while still letting code-aware extractors receive the
    # file/language hints they need for stable FQNs.
    accepts_chunk_context: bool = True

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        # symbols: fqn -> {language, file_path, symbol}
        self._symbols: dict[str, dict[str, Any]] = {}
        # name -> set of fqns that share that bare name
        self._by_name: dict[str, set[str]] = defaultdict(set)
        # Unresolved CALLS edges: (caller_fqn, caller_lang, caller_path,
        #                          callee_name, line, snippet)
        self._pending_calls: list[dict[str, Any]] = []
        # Unresolved INHERITS / IMPLEMENTS: (src_fqn, src_lang, src_path,
        #                                     dst_name, edge_type)
        self._pending_class_edges: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Phase 1: per-chunk
    # ------------------------------------------------------------------

    def extract(
        self,
        text: str,
        *,
        source_path: Optional[str] = None,
        language: Optional[str] = None,
    ) -> ExtractResult:
        """Parse one chunk and attach ``callees`` metadata.

        ``source_path`` / ``language`` are optional so the standard
        ``extractor.extract(text)`` runner callsite still works — when
        omitted, the extractor falls back to an in-memory ``<text>``
        path and a language heuristic. With ``symbol_aware`` chunker
        upstream (SP-B), the runner will populate both from the
        chunk metadata in a follow-up wiring change.
        """
        lang = language or _auto_detect_language(text)
        if lang is None:
            # No language detected: emit empty callees rather than a crash.
            return ExtractResult(tags=[], metadata={"callees": []})

        path = source_path or "<text>"
        result = parse_text(text, language=lang, file_path=path)

        # Register every symbol we saw so finalize() can resolve names.
        for sym in result.symbols:
            self._register_symbol(sym=sym, language=lang, file_path=path)

        # Find INHERITS / IMPLEMENTS via supplementary regex. SP-A's
        # ParseResult doesn't carry these — see module docstring.
        self._scan_class_edges(text=text, language=lang, file_path=path)

        # Per-chunk callee metadata: every call_site whose enclosing function
        # is in this chunk. We also record the global pending edges for
        # finalize().
        callees: list[dict[str, Any]] = []
        intra_file_names = {s.name for s in result.symbols}
        # Build a caller_node_id -> caller_fqn map so we can look up
        # FQNs without re-walking the symbol tree.
        caller_id_to_fqn: dict[str, str] = {}
        for sym in result.symbols:
            if sym.symbol_type in ("function", "method"):
                cid = code_symbol_node_id(
                    self._project_id_for(path), lang, path, sym.fqn
                )
                caller_id_to_fqn[cid] = sym.fqn

        for cs in result.call_sites:
            caller_fqn = caller_id_to_fqn.get(cs.caller_node_id)
            if caller_fqn is None:
                # Call site outside any tracked function — skip.
                continue
            callees.append(
                {
                    "name": cs.callee_name,
                    "line": cs.line,
                    "snippet": cs.snippet,
                    "resolved_intra_file": cs.callee_name in intra_file_names
                    and cs.callee_name != caller_fqn.rsplit(".", 1)[-1],
                }
            )
            # Cross-file resolution happens in finalize: stash the raw
            # (caller_fqn, callee_name) tuple. We skip names already
            # resolved intra-file to avoid double-emitting edges.
            if not (
                cs.callee_name in intra_file_names
                and cs.callee_name != caller_fqn.rsplit(".", 1)[-1]
            ):
                self._pending_calls.append(
                    {
                        "caller_fqn": caller_fqn,
                        "caller_language": lang,
                        "caller_path": path,
                        "callee_name": cs.callee_name,
                        "line": cs.line,
                        "snippet": cs.snippet,
                    }
                )
            else:
                # Intra-file resolved call: still emit an edge so impact_of
                # works inside one file. Use the unique-match band.
                # Find the sibling symbol's fqn.
                sibling = next(
                    (s for s in result.symbols if s.name == cs.callee_name),
                    None,
                )
                if sibling is not None:
                    self._pending_calls.append(
                        {
                            "caller_fqn": caller_fqn,
                            "caller_language": lang,
                            "caller_path": path,
                            "callee_name": cs.callee_name,
                            "line": cs.line,
                            "snippet": cs.snippet,
                            "_intra_file_fqn": sibling.fqn,
                        }
                    )

        return ExtractResult(tags=[], metadata={"callees": callees})

    # ------------------------------------------------------------------
    # Phase 2: corpus-level resolution
    # ------------------------------------------------------------------

    def finalize(self, *, project_id: str = "default") -> list[dict[str, Any]]:
        """Resolve pending calls + class edges to FQNs, return edge list.

        Deterministic: sorted by (edge_type, src_fqn, dst_fqn) so two
        successive calls return the same list and tests can compare with
        ``==``. Idempotent: calling finalize twice doesn't mutate
        accumulated state.
        """
        edges: list[dict[str, Any]] = []
        emitted: set[tuple[str, str, str]] = set()  # (edge_type, src_id, dst_id)

        def _emit(
            *,
            edge_type: str,
            src_fqn: str,
            dst_fqn: str,
            confidence: float,
            evidence: dict[str, Any],
        ) -> None:
            src = self._symbols.get(src_fqn)
            dst = self._symbols.get(dst_fqn)
            if src is None or dst is None:
                # Defensive: every resolution path looks up via
                # ``self._symbols`` so this branch is hard to hit, but it
                # cleanly drops the edge if someone slips in a hand-crafted
                # FQN that isn't registered.
                return
            src_id = code_symbol_node_id(
                project_id, src["language"], src["file_path"], src_fqn
            )
            dst_id = code_symbol_node_id(
                project_id, dst["language"], dst["file_path"], dst_fqn
            )
            key = (edge_type, src_id, dst_id)
            if key in emitted:
                return
            emitted.add(key)
            edges.append(
                {
                    "edge_type": edge_type,
                    # CS-2: typed codegraph EdgeKind, derived from edge_type
                    # via the canonical mapping. Single chokepoint — every
                    # emission path goes through _emit so this is the only
                    # site that needs to know about EdgeKind.
                    "edge_kind": edge_type_to_kind(edge_type),
                    "src_fqn": src_fqn,
                    "dst_fqn": dst_fqn,
                    "src_node_id": src_id,
                    "dst_node_id": dst_id,
                    "confidence": confidence,
                    "evidence": evidence,
                    # CS-5: every edge from this extractor is AST-derived.
                    # CS-3 synthesizers will emit through their own code
                    # path (not through finalize._emit) with 'heuristic'
                    # + a {synthesizedBy: <channel>} payload.
                    "provenance": "ast",
                    "provenance_metadata": {},
                }
            )

        # ----- CALLS edges -----
        for pc in self._pending_calls:
            intra = pc.get("_intra_file_fqn")
            if intra is not None:
                # Intra-file: dst_fqn is already known precisely.
                _emit(
                    edge_type="CALLS",
                    src_fqn=pc["caller_fqn"],
                    dst_fqn=intra,
                    confidence=self.cfg.unique_match_confidence,
                    evidence={
                        "line": pc["line"],
                        "snippet": pc["snippet"],
                        "resolution": "intra_file",
                    },
                )
                continue

            candidates = sorted(self._by_name.get(pc["callee_name"], set()))
            if not candidates:
                continue  # external call
            if len(candidates) == 1:
                _emit(
                    edge_type="CALLS",
                    src_fqn=pc["caller_fqn"],
                    dst_fqn=candidates[0],
                    confidence=self.cfg.unique_match_confidence,
                    evidence={
                        "line": pc["line"],
                        "snippet": pc["snippet"],
                        "resolution": "unique_name",
                    },
                )
            else:
                for cand in candidates:
                    _emit(
                        edge_type="CALLS",
                        src_fqn=pc["caller_fqn"],
                        dst_fqn=cand,
                        confidence=self.cfg.ambiguous_match_confidence,
                        evidence={
                            "line": pc["line"],
                            "snippet": pc["snippet"],
                            "resolution": "ambiguous_name",
                            "candidates": candidates,
                        },
                    )

        # ----- INHERITS / IMPLEMENTS edges -----
        for ce in self._pending_class_edges:
            candidates = sorted(self._by_name.get(ce["dst_name"], set()))
            if not candidates:
                continue
            if len(candidates) == 1:
                _emit(
                    edge_type=ce["edge_type"],
                    src_fqn=ce["src_fqn"],
                    dst_fqn=candidates[0],
                    confidence=self.cfg.unique_match_confidence,
                    evidence={"resolution": "unique_name"},
                )
            else:
                for cand in candidates:
                    _emit(
                        edge_type=ce["edge_type"],
                        src_fqn=ce["src_fqn"],
                        dst_fqn=cand,
                        confidence=self.cfg.ambiguous_match_confidence,
                        evidence={
                            "resolution": "ambiguous_name",
                            "candidates": candidates,
                        },
                    )

        edges.sort(key=lambda e: (e["edge_type"], e["src_fqn"], e["dst_fqn"]))
        return edges

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _project_id_for(self, file_path: str) -> str:
        """Project ID used during the per-chunk pass.

        ``code_symbol_node_id`` requires a project; the per-chunk pass
        uses a stable placeholder so caller_node_id can be computed
        without the consumer knowing the project name. ``finalize()``
        takes the real project_id and re-mints IDs there.
        """
        return "default"

    def _register_symbol(
        self, *, sym: Symbol, language: str, file_path: str
    ) -> None:
        # Skip duplicates: re-running extract on the same chunk shouldn't
        # multiply the symbol table.
        if sym.fqn in self._symbols:
            return
        self._symbols[sym.fqn] = {
            "language": language,
            "file_path": file_path,
            "symbol": sym,
        }
        self._by_name[sym.name].add(sym.fqn)

    def _scan_class_edges(
        self, *, text: str, language: str, file_path: str
    ) -> None:
        """Capture INHERITS / IMPLEMENTS via regex, append to pending list.

        Why regex here when SP-A has a tree-sitter wrapper? SP-A's
        ParseResult doesn't surface base classes / interfaces — adding
        them would change SP-A's public surface, which is frozen. We
        keep the SP-C-only signal in SP-C.
        """
        if language == "python":
            for m in _PY_INHERIT_RE.finditer(text):
                cls_name = m.group("name")
                src_fqn = build_fqn(file_path, cls_name, None)
                for base in [b.strip() for b in m.group("base").split(",")]:
                    if not base:
                        continue
                    self._pending_class_edges.append(
                        {
                            "src_fqn": src_fqn,
                            "src_language": language,
                            "src_path": file_path,
                            "dst_name": base,
                            "edge_type": "INHERITS",
                        }
                    )
        elif language == "java":
            for m in _JAVA_INHERIT_RE.finditer(text):
                cls_name = m.group("name")
                src_fqn = build_fqn(file_path, cls_name, None)
                base = m.group("base")
                if base:
                    self._pending_class_edges.append(
                        {
                            "src_fqn": src_fqn,
                            "src_language": language,
                            "src_path": file_path,
                            "dst_name": base,
                            "edge_type": "INHERITS",
                        }
                    )
                impls = m.group("impls")
                if impls:
                    for iface in [i.strip() for i in impls.split(",")]:
                        if not iface:
                            continue
                        self._pending_class_edges.append(
                            {
                                "src_fqn": src_fqn,
                                "src_language": language,
                                "src_path": file_path,
                                "dst_name": iface,
                                "edge_type": "IMPLEMENTS",
                            }
                        )


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

# Identifier validator — same allowlist as `chunkshop.memory.staging` and the
# pgvector sink. Postgres identifier injection is prevented by composition,
# not interpolation, but we still gate inputs so a stray uppercase/space
# never reaches psycopg.sql.Identifier.
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _validate_ident(name: str, *, kind: str) -> None:
    if not _IDENT.match(name):
        raise ValueError(
            f"{kind} must match ^[a-z_][a-z0-9_]*$, got {name!r}"
        )


def write_edges_schema(dsn: str, *, schema: str) -> None:
    """Create the ``<schema>.code_edges`` table + indexes if absent.

    Idempotent. Uses ``IF NOT EXISTS`` everywhere so re-running across
    cells is safe. SQL is composed with :mod:`psycopg.sql` — no f-string
    SQL ever touches the driver.
    """
    _validate_ident(schema, kind="schema")
    import psycopg
    from psycopg import sql

    schema_id = sql.Identifier(schema)
    table_id = sql.Identifier("code_edges")
    fq = sql.SQL("{}.{}").format(schema_id, table_id)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema_id)
        )
        cur.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {fq} ("
                " project_id text NOT NULL,"
                " edge_type text NOT NULL,"
                " src_fqn text NOT NULL,"
                " dst_fqn text NOT NULL,"
                " src_node_id text NOT NULL,"
                " dst_node_id text NOT NULL,"
                " confidence double precision NOT NULL,"
                " evidence jsonb,"
                # CS-2: typed codegraph EdgeKind ontology (12 values). Default
                # is 'references' so a pre-CS-2 row inserted by an older client
                # still satisfies NOT NULL; the extractor's write_edges path
                # always supplies an explicit value via edge_type_to_kind.
                " edge_kind text NOT NULL DEFAULT 'references'"
                "   CHECK (edge_kind IN ('contains','calls','imports','exports',"
                "                        'extends','implements','references',"
                "                        'type_of','returns','instantiates',"
                "                        'overrides','decorates')),"
                # CS-5: provenance tagging. Every existing emission path is
                # AST-derived, so DEFAULT 'ast' correctly backfills pre-CS-5
                # rows. CS-3 synthesizers will explicitly set 'heuristic' with
                # a {synthesizedBy: <channel>} provenance_metadata payload.
                " provenance text NOT NULL DEFAULT 'ast'"
                "   CHECK (provenance IN ('ast', 'scip', 'heuristic')),"
                " provenance_metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,"
                " PRIMARY KEY (project_id, edge_type, src_node_id, dst_node_id))"
            ).format(fq=fq)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {ix} ON {fq} "
                "(project_id, src_node_id)"
            ).format(ix=sql.Identifier("code_edges_src_idx"), fq=fq)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {ix} ON {fq} "
                "(project_id, dst_node_id)"
            ).format(ix=sql.Identifier("code_edges_dst_idx"), fq=fq)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {ix} ON {fq} "
                "(project_id, confidence) WHERE confidence >= 0.7"
            ).format(ix=sql.Identifier("code_edges_confident_idx"), fq=fq)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {ix} ON {fq} "
                "(project_id, edge_kind)"
            ).format(ix=sql.Identifier("code_edges_kind_idx"), fq=fq)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {ix} ON {fq} "
                "(project_id, provenance)"
            ).format(ix=sql.Identifier("code_edges_provenance_idx"), fq=fq)
        )
        conn.commit()


def write_edges(
    extractor: "CodeRelationshipsExtractor",
    *,
    dsn: str,
    schema: str,
    project_id: str = "default",
) -> int:
    """Finalize the extractor and write its edges to ``<schema>.code_edges``.

    Returns the number of rows submitted (note: ``ON CONFLICT DO UPDATE``
    might overwrite existing rows; this count is rows-submitted, not
    rows-newly-inserted).
    """
    _validate_ident(schema, kind="schema")
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Json

    edges = extractor.finalize(project_id=project_id)
    if not edges:
        return 0

    schema_id = sql.Identifier(schema)
    table_id = sql.Identifier("code_edges")
    fq = sql.SQL("{}.{}").format(schema_id, table_id)

    rows = [
        (
            project_id,
            e["edge_type"],
            e["src_fqn"],
            e["dst_fqn"],
            e["src_node_id"],
            e["dst_node_id"],
            e["confidence"],
            Json(e.get("evidence") or {}),
            # CS-2: every finalize() edge now carries edge_kind (Task 3).
            e["edge_kind"],
            # CS-5: every finalize() edge now carries provenance + metadata.
            e["provenance"],
            Json(e.get("provenance_metadata") or {}),
        )
        for e in edges
    ]

    insert = sql.SQL(
        "INSERT INTO {fq} (project_id, edge_type, src_fqn, dst_fqn,"
        " src_node_id, dst_node_id, confidence, evidence, edge_kind,"
        " provenance, provenance_metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (project_id, edge_type, src_node_id, dst_node_id) "
        "DO UPDATE SET"
        "  src_fqn = EXCLUDED.src_fqn,"
        "  dst_fqn = EXCLUDED.dst_fqn,"
        "  confidence = EXCLUDED.confidence,"
        "  evidence = EXCLUDED.evidence,"
        # CS-2: preserve edge_kind on update so a re-run with a changed
        # mapping (future ontology migration) doesn't leave stale values.
        "  edge_kind = EXCLUDED.edge_kind,"
        # CS-5: preserve provenance + provenance_metadata on update so a
        # CS-3-era reclassification (e.g., an edge previously synthesized
        # heuristically gets re-derived from AST in a later run) cleanly
        # overwrites instead of leaving stale provenance.
        "  provenance = EXCLUDED.provenance,"
        "  provenance_metadata = EXCLUDED.provenance_metadata"
    ).format(fq=fq)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.executemany(insert, rows)
        conn.commit()

    return len(rows)


__all__ = [
    "CodeRelationshipsExtractor",
    "write_edges",
    "write_edges_schema",
]
