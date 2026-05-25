# SP-2 `chunkshop-connectors` Bulk Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **⚠ RE-CHECK BEFORE STARTING:** This plan is written against the SP-1 *design*. SP-1's actual landed API (registry signature, `IncrementalSource`/`RawStore` shapes, `ConnectorSource` config) is the contract this plan consumes. Per the user directive, **after SP-1 completes, re-read `docs/superpowers/specs/2026-05-25-...-foundation-design.md` + the merged SP-1 code and reconcile any drift in this plan before executing.** Several tasks depend on reading actual RAGFlow source files at execution time — their exact code cannot be pre-written and is marked **[READ-AT-EXEC]**.

**Goal:** Ship `chunkshop-connectors` — one in-repo, opt-in plugin package that bulk-ports RAGFlow's MIT (Onyx-attributed) `common/data_source/` tree, registers all connectors through SP-1's `chunkshop.sources` entry-point seam, with a tested **verified tier** (gdrive, github, blob, rss, slack) and an **experimental tier** (the rest).

**Architecture:** Separate distribution living in the monorepo at `python/connectors/` (its own `pyproject.toml`, package `chunkshop_connectors`). Each connector is a factory `(config: dict) -> Source` registered via entry points; connectors implement SP-1's `IncrementalSource`/`PrunableSource`/`RawStore` where the source supports it. Core `chunkshop` is an install-time dependency. Production orchestration stays in `chunkshop_api`, not here.

**Tech Stack:** Python 3.11+, chunkshop (SP-1), per-connector SDKs behind per-connector extras (boto3, google-api-python-client, slack-sdk, atlassian-python-api, PyGithub/httpx, feedparser, …), pytest + pytest_httpserver for hermetic mocks.

**Spec:** `docs/superpowers/specs/2026-05-25-chunkshop-connector-plugin-foundation-design.md` (§3 SP-2, §8 license).

**RAGFlow source:** the brief cites `/Users/matt.yonkovit/yonk-tools/research/ragflow/common/data_source/` (a macOS path). On this Linux host the checkout location differs — **Task 0 locates it.** Canonical upstream for cross-checking: https://github.com/onyx-dot-app/onyx.

---

## File structure (target)

```
python/connectors/                     # separate distribution, in monorepo
├── pyproject.toml                      # package=chunkshop_connectors, per-connector extras, entry points
├── NOTICE
├── THIRD-PARTY-LICENSES.md
├── README.md
└── src/chunkshop_connectors/
    ├── __init__.py                     # Onyx MIT attribution block (verbatim)
    ├── _PROVENANCE.md                  # RAGFlow source commit SHA + date
    ├── _base/                          # lifted infrastructure layer
    │   ├── interfaces.py  models.py  runner.py  exceptions.py
    │   ├── retry.py  rate_limit.py  utils.py  file_types.py
    ├── _adapt.py                       # RAGFlow Document → chunkshop Document; factory helpers
    ├── _tier.py                        # @verified / @experimental markers + registry helper
    ├── blob/        (verified)         # S3-compatible / R2 / GCS / OCI / minio
    ├── rss/         (verified)
    ├── github/      (verified)
    ├── gdrive/      (verified)
    ├── slack/       (verified)
    ├── oauth/                          # concrete OAuthProvider impls (google, slack, confluence)
    ├── notion/ confluence/ jira/ dropbox/ box/ … (experimental)
    └── testing/mocks/                  # per-provider pytest_httpserver mocks
python/connectors/tests/               # connector test suite (hermetic)
```

---

## Task 0: Locate RAGFlow source, license audit, provenance **[READ-AT-EXEC]**

**Files:** none yet (investigation + a provenance note later).

- [ ] **Step 1: Find the RAGFlow checkout.** Run, in order, until one hits:

```bash
fd -t d 'data_source' /home/yonk 2>/dev/null | grep -i ragflow
find /home/yonk -type d -path '*ragflow*/common/data_source' 2>/dev/null | head
ls /home/yonk/yonk-tools/research/ragflow/common/data_source 2>/dev/null
```

If not present locally, STOP and ask the user for the checkout path or permission to `git clone https://github.com/infiniflow/ragflow` (shallow) into a scratch dir. Do not invent file contents.

- [ ] **Step 2: License audit.** Confirm `common/data_source/__init__.py` carries the MIT header + Onyx/Danswer attribution. Read it. Record the RAGFlow commit SHA: `git -C <ragflow> rev-parse HEAD`.

- [ ] **Step 3:** Verify only `common/data_source/` (MIT) is in scope — the rest of RAGFlow is Apache-2.0. Write `src/chunkshop_connectors/_PROVENANCE.md` later (Task 1) with: upstream URL, RAGFlow SHA, audit date, "MIT, Onyx-attributed."

- [ ] **Step 4: Inventory.** List the connector files present and map each to a `DocumentSource` enum value. Produce a checklist of verified-tier (blob, rss, github, gdrive, slack) vs experimental-tier (rest). This inventory drives Tasks 4–9.

No commit (investigation only); capture findings in the task notes for downstream tasks.

---

## Task 1: Scaffold `chunkshop-connectors` package

**Files:**
- Create: `python/connectors/pyproject.toml`, `python/connectors/src/chunkshop_connectors/__init__.py`, `NOTICE`, `THIRD-PARTY-LICENSES.md`, `README.md`, `_PROVENANCE.md`
- Test: `python/connectors/tests/test_package_imports.py`

- [ ] **Step 1: Write the failing test**

```python
# python/connectors/tests/test_package_imports.py
def test_package_imports():
    import chunkshop_connectors
    assert chunkshop_connectors.__doc__  # attribution block present


def test_attribution_present():
    import chunkshop_connectors, pathlib
    init = pathlib.Path(chunkshop_connectors.__file__).read_text()
    assert "Onyx" in init and "MIT" in init
```

- [ ] **Step 2:** `cd python/connectors && uv run pytest tests/test_package_imports.py -v` → FAIL (package missing).

- [ ] **Step 3: Write `pyproject.toml`** — package `chunkshop_connectors`, `dependencies = ["chunkshop>=<SP-1 version>"]`, per-connector extras (`[gdrive]`, `[github]`, `[slack]`, `[blob]`, `[rss]`, `[notion]`, … `[all]`), a `[dev]` extra with `pytest`, `pytest-asyncio`, `pytest_httpserver`, and an empty `[project.entry-points."chunkshop.sources"]` table (populated per-connector in later tasks). Write `__init__.py` with the **verbatim Onyx MIT attribution block** copied from RAGFlow's `common/data_source/__init__.py` (from Task 0). Write `NOTICE`, `THIRD-PARTY-LICENSES.md` (full MIT text + Onyx + RAGFlow), `_PROVENANCE.md` (SHA from Task 0), `README.md`.

- [ ] **Step 4:** `uv run pytest tests/test_package_imports.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git -C ../.. add python/connectors
git -C ../.. commit -m "feat(connectors): scaffold chunkshop-connectors package with attribution"
```

---

## Task 2: Lift the infrastructure layer (`_base/`) **[READ-AT-EXEC]**

**Files:**
- Create: `src/chunkshop_connectors/_base/{interfaces,models,runner,exceptions,retry,rate_limit,utils,file_types}.py`
- Test: `python/connectors/tests/test_base_layer.py`

Lift per the brief's mapping table (AGENT-BRIEF §"Phase 0"). For each file: copy verbatim with header intact, then apply the import-rewrite table:

| RAGFlow import | chunkshop-connectors substitute |
|---|---|
| `from common.data_source.interfaces import …` | `from chunkshop_connectors._base.interfaces import …` |
| `from common.data_source.models import …` | `from chunkshop_connectors._base.models import …` |
| `from common.data_source.config import …` | `from chunkshop_connectors._base.config import …` (or inline the consts you keep) |
| `from anthropic import BaseModel` (interfaces.py line ~9) | `from pydantic import BaseModel` — **bug fix** |
| `from api.utils.common import hash128` | `hashlib` directly |
| `from rag.utils.redis_conn import REDIS_CONN` | **remove** (no Redis; library-first) |
| `from api.db.services.* ` | **remove** (no DB services) |
| `from common.log_utils import init_root_logger` | stdlib `logging` |

- [ ] **Step 1: Write the failing test** (contract: the lifted interface base classes import and expose expected names)

```python
# python/connectors/tests/test_base_layer.py
def test_interfaces_import():
    from chunkshop_connectors._base import interfaces as I
    # The Onyx interface hierarchy — names confirmed against the lifted file at exec.
    for name in ("BaseConnector", "CheckpointedConnector", "LoadConnector"):
        assert hasattr(I, name), f"missing {name}"


def test_no_ragflow_internal_imports():
    import pathlib, re
    base = pathlib.Path(__file__).parents[1] / "src/chunkshop_connectors/_base"
    bad = re.compile(r"^\s*from\s+(api|rag|common)\.|^\s*import\s+(api|rag)\b", re.M)
    offenders = [p.name for p in base.glob("*.py") if bad.search(p.read_text())]
    assert not offenders, f"unrewritten RAGFlow imports in: {offenders}"


def test_no_anthropic_basemodel_bug():
    import pathlib
    txt = (pathlib.Path(__file__).parents[1] / "src/chunkshop_connectors/_base/interfaces.py").read_text()
    assert "from anthropic import BaseModel" not in txt
```

- [ ] **Step 2:** `uv run pytest tests/test_base_layer.py -v` → FAIL (modules missing).

- [ ] **Step 3: Lift + rewrite** the eight `_base` files per the table above. Prune `utils.py` (1284 lines) to only helpers the verified-tier connectors actually reference (grep the verified connectors for `utils.` usage; drop the rest — they can be restored when an experimental connector needs them). Keep every file's original copyright header. Adjust `models.py` `externale_access` typo decision: **keep the typo** for upstream-diff compatibility, add a `# NOTE: upstream typo preserved for diff-tracking` comment.

- [ ] **Step 4:** `uv run pytest tests/test_base_layer.py -v` → PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git -C ../.. add python/connectors/src/chunkshop_connectors/_base
git -C ../.. commit -m "feat(connectors): lift RAGFlow data_source infrastructure layer (_base)"
```

---

## Task 3: Adapter + tier markers (`_adapt.py`, `_tier.py`)

**Files:**
- Create: `src/chunkshop_connectors/_adapt.py`, `src/chunkshop_connectors/_tier.py`
- Test: `python/connectors/tests/test_adapt.py`

- [ ] **Step 1: Write the failing test**

```python
# python/connectors/tests/test_adapt.py
from chunkshop_connectors._adapt import to_chunkshop_document
from chunkshop_connectors._tier import verified, experimental, tier_of
from chunkshop.sources.base import Document


class _RagDoc:  # stand-in for RAGFlow's Document shape
    def __init__(self):
        self.id = "g1"
        self.sections = [type("S", (), {"text": "hello "})(), type("S", (), {"text": "world"})()]
        self.semantic_identifier = "Title"
        self.metadata = {"k": "v"}


def test_to_chunkshop_document_concats_sections():
    d = to_chunkshop_document(_RagDoc())
    assert isinstance(d, Document)
    assert d.id == "g1"
    assert d.content == "hello world"
    assert d.title == "Title"
    assert d.metadata == {"k": "v"}


@verified
class _V: ...
@experimental
class _E: ...


def test_tier_markers():
    assert tier_of(_V) == "verified"
    assert tier_of(_E) == "experimental"
```

- [ ] **Step 2:** `uv run pytest tests/test_adapt.py -v` → FAIL.

- [ ] **Step 3: Implement** — `to_chunkshop_document(rag_doc)` maps RAGFlow's `Document` (sections→content, `semantic_identifier`→title, id, metadata; carry an etag/version into `fingerprint` when present). **[READ-AT-EXEC]** confirm RAGFlow's `Document`/`Section` field names from the lifted `models.py` and adjust the mapping. `_tier.py`: `verified`/`experimental` class decorators that set `__connector_tier__`; `tier_of(cls)` reads it (default `"experimental"`).

```python
# src/chunkshop_connectors/_adapt.py
from __future__ import annotations
from chunkshop.sources.base import Document


def to_chunkshop_document(rag_doc) -> Document:
    sections = getattr(rag_doc, "sections", None) or []
    content = "".join(getattr(s, "text", "") or "" for s in sections) \
        if sections else (getattr(rag_doc, "content", "") or "")
    fingerprint = getattr(rag_doc, "etag", None) or getattr(rag_doc, "version", None)
    return Document(
        id=str(rag_doc.id),
        content=content,
        title=getattr(rag_doc, "semantic_identifier", None) or getattr(rag_doc, "title", None),
        metadata=getattr(rag_doc, "metadata", None) or None,
        fingerprint=str(fingerprint) if fingerprint is not None else None,
    )
```

```python
# src/chunkshop_connectors/_tier.py
def verified(cls): cls.__connector_tier__ = "verified"; return cls
def experimental(cls): cls.__connector_tier__ = "experimental"; return cls
def tier_of(cls) -> str: return getattr(cls, "__connector_tier__", "experimental")
```

- [ ] **Step 4/5:** PASS → commit `feat(connectors): add RAGFlow→chunkshop document adapter + tier markers`.

---

## Tasks 4–8: Verified-tier connectors (one task each) **[READ-AT-EXEC]**

For **each** of `blob` (Task 4), `rss` (Task 5), `github` (Task 6), `gdrive` (Task 7), `slack` (Task 8), follow this identical sub-procedure. The connector internals are lifted RAGFlow code, so exact bodies are read at execution; the *adapter contract*, *tests*, and *registration* below are fully specified.

**Files (per connector `<c>`):**
- Create: `src/chunkshop_connectors/<c>/connector.py` (lifted+rewritten), `src/chunkshop_connectors/<c>/__init__.py` (the `factory` + config model + tier marker)
- Create: `src/chunkshop_connectors/testing/mocks/<c>.py` (pytest_httpserver-based)
- Modify: `pyproject.toml` (add `[project.entry-points."chunkshop.sources"]` line + `[<c>]` extra)
- Test: `python/connectors/tests/test_<c>_connector.py`

- [ ] **Step 1: Write the failing test** — the connector resolves through chunkshop's registry, validates config, and yields a chunkshop `Document` against the mock. Template (substitute `<c>`):

```python
# python/connectors/tests/test_<c>_connector.py
import pytest
from chunkshop.sources import load_source, registry
from chunkshop.config import ConnectorSource
from chunkshop.sources.base import Document, IncrementalSource
from chunkshop_connectors._tier import tier_of


def test_<c>_registered_and_verified():
    # entry points are installed via the package; clear cache to rediscover
    registry.clear_cache()
    assert "<c>" in registry.available_connectors()
    from chunkshop_connectors.<c> import Connector
    assert tier_of(Connector) == "verified"


def test_<c>_config_validation_rejects_bad():
    from chunkshop_connectors.<c> import ConfigModel
    with pytest.raises(Exception):
        ConfigModel.model_validate({"wrong": "shape"})


def test_<c>_yields_documents_against_mock(<c>_mock):
    cfg = ConnectorSource(type="connector", connector="<c>", config=<c>_mock.valid_config)
    src = load_source(cfg)
    docs = list(src.iter_documents())
    assert docs and isinstance(docs[0], Document)


def test_<c>_incremental_cursor(<c>_mock):
    from chunkshop.testing import assert_cursor_advances, assert_idempotent_on_re_emit
    from chunkshop_connectors.<c> import Connector
    src = Connector(<c>_mock.valid_config)
    if isinstance(src, IncrementalSource):
        assert_cursor_advances(src)
        assert_idempotent_on_re_emit(src)
```

(`<c>_mock` is a fixture from `testing/mocks/<c>.py`, exposing `.valid_config` and a running mock endpoint.)

- [ ] **Step 2:** Run → FAIL.

- [ ] **Step 3: Lift + adapt + register.**
  - Copy the RAGFlow connector file, apply the import-rewrite table (Task 2), swap RAGFlow `Document` returns through `to_chunkshop_document` (or have the factory wrap the runner's output), strip Redis/DB-service coupling.
  - Write `<c>/__init__.py`: a `ConfigModel` (pydantic, validates the `config:` blob), a `Connector` class adapted to chunkshop's `Source` (and `IncrementalSource`/`PrunableSource` where the RAGFlow connector supported checkpoint/prune — `blob`=fingerprint, `github`=cursor, `gdrive`=cursor+prune, `slack`=cursor, `rss`=cursor), decorated `@verified`, and a `factory(config: dict) -> Connector` that validates via `ConfigModel`.
  - Add to `pyproject.toml`: `<c> = "chunkshop_connectors.<c>:factory"` under the entry-point table, and a `[<c>]` extra with its SDK deps.
  - For `gdrive`/`slack`, wire OAuth via the provider impls from Task 10 (land Task 10 first if doing OAuth connectors before non-OAuth ones — reorder so `blob`/`rss`/`github` (PAT) come before `gdrive`/`slack`).
  - Write `testing/mocks/<c>.py`: a `pytest_httpserver` (or `responses`) fixture serving list/fetch/error(401/429/5xx)/pagination, exposing `.valid_config`.

- [ ] **Step 4:** Run → PASS. Re-install package if entry points changed: `uv pip install -e python/connectors`.

- [ ] **Step 5: Docs** — `docs/connectors/<c>.md` (setup, required scopes/env vars, sync mode, prune support, config keys).

- [ ] **Step 6: Commit** `feat(connectors): add verified <c> connector + mock + docs`.

---

## Task 9: Experimental-tier bulk lift + register + smoke test **[READ-AT-EXEC]**

**Files:**
- Create: `src/chunkshop_connectors/<each>/…` for every remaining `DocumentSource` (notion, confluence, jira, dropbox, box, gitlab, bitbucket, gmail, imap, discord, airtable, asana, zendesk, sharepoint, teams, r2, gcs, oci, seafile, webdav, moodle, dingtalk, generic rest_api, …)
- Modify: `pyproject.toml` (entry points + extras for each)
- Test: `python/connectors/tests/test_experimental_smoke.py`

- [ ] **Step 1: Write the smoke test** (parametrized over every experimental connector — import + register + instantiate-with-dummy-config-raises-cleanly, NOT full behavior)

```python
# python/connectors/tests/test_experimental_smoke.py
import importlib, pkgutil, pytest
import chunkshop_connectors
from chunkshop.sources import registry
from chunkshop_connectors._tier import tier_of

EXPERIMENTAL = [  # filled from Task 0 inventory minus the 5 verified
    "notion", "confluence", "jira", "dropbox", "box", "gitlab", "bitbucket",
    "gmail", "imap", "discord", "airtable", "asana", "zendesk", "sharepoint",
    "teams", "r2", "gcs", "oci", "seafile", "webdav", "moodle", "dingtalk", "rest_api",
]


@pytest.mark.parametrize("name", EXPERIMENTAL)
def test_experimental_importable_and_registered(name):
    mod = importlib.import_module(f"chunkshop_connectors.{name}")
    assert hasattr(mod, "factory")
    assert hasattr(mod, "Connector")
    assert tier_of(mod.Connector) == "experimental"


def test_experimental_all_in_registry():
    registry.clear_cache()
    avail = set(registry.available_connectors())
    for name in EXPERIMENTAL:
        assert name in avail, f"{name} not registered"
```

- [ ] **Step 2:** Run → FAIL.

- [ ] **Step 3: Bulk-lift each** with the Task-2 import-rewrite + Task-4 `__init__.py` scaffold, but decorated `@experimental` and with a **minimal** `ConfigModel` (can be a permissive `dict`-passthrough where the RAGFlow config shape is complex — flag with a `# TODO: tighten config schema` and a docs note). Do NOT write full behavioral tests for these; the smoke test + import-rewrite gate is the bar. Where a connector won't import cleanly (missing pruned `utils` helper, tangled dep), either (a) restore the needed `_base/utils.py` helper, or (b) mark the connector `@experimental` with a module-level `UNAVAILABLE_REASON` string and `pytest.skip` it in the parametrized test — record it in `docs/connectors/_status.md`. **Never** fake a passing import.

- [ ] **Step 4:** Run → PASS (skips allowed only for documented-unavailable connectors).

- [ ] **Step 5: Status doc** — `docs/connectors/_status.md`: a table of every connector, tier, sync mode, prune support, and (for experimental) any `UNAVAILABLE_REASON`.

- [ ] **Step 6: Commit** `feat(connectors): bulk-lift + register experimental-tier connectors`.

---

## Task 10: OAuth provider implementations (google, slack, confluence) **[READ-AT-EXEC]**

**Files:**
- Create: `src/chunkshop_connectors/oauth/{google,slack,confluence}.py`
- Test: `python/connectors/tests/test_oauth_providers.py`

- [ ] **Step 1: Write the failing test** — each provider implements SP-1's `OAuthProvider` Protocol and handles its quirk (google `access_type=offline`; slack xoxp/xoxb; confluence Atlassian Cloud).

```python
# python/connectors/tests/test_oauth_providers.py
import pytest
from chunkshop.oauth import OAuthProvider


@pytest.mark.parametrize("modname,cls", [
    ("chunkshop_connectors.oauth.google", "GoogleOAuthProvider"),
    ("chunkshop_connectors.oauth.slack", "SlackOAuthProvider"),
    ("chunkshop_connectors.oauth.confluence", "ConfluenceOAuthProvider"),
])
def test_provider_implements_protocol(modname, cls):
    import importlib
    P = getattr(importlib.import_module(modname), cls)
    p = P(client_id="id", client_secret="secret")
    assert isinstance(p, OAuthProvider)
    url = p.authorization_url(state="s", redirect_uri="https://cb", scopes=["read"])
    assert url.startswith("https://")


def test_google_requests_offline_access():
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider
    url = GoogleOAuthProvider(client_id="id", client_secret="x").authorization_url("s", "https://cb", ["drive.readonly"])
    assert "access_type=offline" in url
```

- [ ] **Step 2/3/4:** Run→FAIL; lift OAuth flow code from RAGFlow's `google_util/oauth_flow.py` + adapt to `OAuthProvider`; build slack/confluence per their docs (cite official docs per the cite-your-sources rule); Run→PASS. Token exchange/refresh HTTP can be tested with a mocked transport.

- [ ] **Step 5: Commit** `feat(connectors): OAuth providers for google/slack/confluence`.

---

## Task 11: Per-provider mock servers (consolidation) + hermetic CI

**Files:**
- Ensure `src/chunkshop_connectors/testing/mocks/{github,gdrive,slack,notion,confluence}.py` exist (verified ones from Tasks 4–8; add notion/confluence here)
- Test: `python/connectors/tests/test_mocks_hermetic.py`

- [ ] **Step 1:** Write a test asserting the mock fixtures serve list/fetch/401/429/5xx/pagination and that **no test performs network egress** (monkeypatch `socket.socket` to raise for non-localhost in a session autouse fixture in `conftest.py`).
- [ ] **Step 2/3:** Implement the egress-guard conftest + remaining mocks → PASS.
- [ ] **Step 4: Commit** `test(connectors): hermetic per-provider mocks + egress guard`.

---

## Task 12: Attribution CI check + docs index

**Files:**
- Create: `python/connectors/tests/test_attribution.py`, `docs/connectors/README.md`
- Modify: repo CI workflow (if present) to run the connectors test suite

- [ ] **Step 1:** Test that every lifted file under `src/chunkshop_connectors/` (excluding `_adapt.py`, `_tier.py`, `__init__` scaffolds you authored) contains the Onyx MIT header line; `NOTICE` + `THIRD-PARTY-LICENSES.md` exist and reference Onyx + RAGFlow.
- [ ] **Step 2:** Implement; fix any missing headers. `docs/connectors/README.md` indexes all connector docs + the status table + the loader-preservation rule reference (#25).
- [ ] **Step 3:** Wire CI to `cd python/connectors && uv run pytest` (hermetic).
- [ ] **Step 4: Commit** `chore(connectors): attribution CI guard + docs index`.

---

## Task 13: Package gate

- [ ] **Step 1:** `cd python/connectors && uv run pytest -q` → all verified-tier behavioral tests + experimental smoke tests pass (documented skips allowed).
- [ ] **Step 2:** `uv build` in `python/connectors` → wheel + sdist build; entry points present in metadata (`python -c "import importlib.metadata as m; print([e.name for e in m.entry_points(group='chunkshop.sources')])"` after install).
- [ ] **Step 3:** Confirm core `chunkshop` suite (`cd python && uv run pytest -q`) is unaffected — the plugin is opt-in and not a core dep.
- [ ] **Step 4: Commit + tag** `git -C ../.. tag sp2-connectors-v0.1`.

---

## Self-review

**Spec coverage** — §3 SP-2 (bulk port, verified+experimental tiers, per-connector extras) → Tasks 1–9; OAuth provider impls in plugin (§4.3 split) → Task 10; mocks (#24 per-provider) → Tasks 4–8,11; attribution (§8) → Tasks 0,1,2,12; loader-preservation (#25) → referenced in Task 12 docs. No new connector overlaps an existing core loader (S3/HTTP/PG/etc. are *not* lifted — `blob` is S3-*compatible*/R2/GCS/OCI which core lacks; confirm in Task 0 inventory).

**Placeholder scan** — `[READ-AT-EXEC]` markers are deliberate (lifted code can't be pre-written); every such task specifies the contract, tests, and rewrite rules precisely. No `TODO` in shipped code except the documented experimental-config-tightening markers.

**Type consistency** — consumes SP-1 surface exactly: `registry.available_connectors()/clear_cache()`, `load_source(ConnectorSource(...))`, `Document`, `IncrementalSource`, `assert_cursor_advances`, `OAuthProvider`. **Re-verify these names against landed SP-1 before executing (see header warning).**
