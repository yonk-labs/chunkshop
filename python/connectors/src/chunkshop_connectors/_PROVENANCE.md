# Provenance — chunkshop-connectors

This package lifts the MIT-licensed `common/data_source/` subtree of
RAGFlow. The subtree carries an explicit MIT Expat licence + Onyx
(formerly Danswer) attribution at `common/data_source/__init__.py`.

| Field | Value |
|---|---|
| Upstream repo | https://github.com/infiniflow/ragflow |
| Upstream commit SHA | `ed179ce684e3d965e9ec4d088cc9ecbec820a32a` |
| Upstream audit date | 2026-05-25 |
| In-scope subtree | `common/data_source/` only |
| Upstream subtree licence | MIT Expat (Onyx-attributed) |
| Ultimate source | https://github.com/onyx-dot-app/onyx |

## Scope

The rest of RAGFlow is Apache-2.0. We **do not** lift any code outside
`common/data_source/`. Each file in this package preserves its original
copyright + licence header verbatim.

## Adaptations applied at lift time

These edits are mechanical, surgical, and recorded here so future audits
can diff against upstream cleanly:

1. **Import rewrites** — `common.data_source.*` → `chunkshop_connectors._base.*`.
2. **Upstream bug fix** — `interfaces.py` line ~9 had
   `from anthropic import BaseModel` (clearly a search-and-replace
   accident); we rewrite to `from pydantic import BaseModel` and tag the
   change with a `# upstream bug` comment.
3. **Decoupling** — Redis/DB-service imports (`rag.utils.redis_conn`,
   `api.db.services.*`) are stripped per the "library-first" rule. The
   callsites are commented `# library-first: ... coupling stripped`.
4. **Logging** — `from common.log_utils import init_root_logger` →
   stdlib `logging`.
5. **Hashing** — `from api.utils.common import hash128` → stdlib
   `hashlib` where used.
6. **Typo preservation** — known upstream typos in `models.py` are
   preserved with a `# NOTE: upstream typo preserved for diff-tracking`
   marker so this package stays diff-compatible with RAGFlow.

## Re-syncing from upstream

To refresh against a newer RAGFlow SHA:

1. `cd <ragflow-checkout> && git fetch && git checkout <new-sha>`
2. `diff -ru <ragflow>/common/data_source/ <this>/src/chunkshop_connectors/` and
   apply non-mechanical upstream changes.
3. Update the SHA + audit date at the top of this file.
4. Run the full connectors test suite + the attribution CI check.
