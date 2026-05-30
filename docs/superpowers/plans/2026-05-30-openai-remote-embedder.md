# OpenAI Remote Embedder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development. Steps use `- [ ]`.

**Goal:** Add an opt-in `type: openai` embedder that embeds via any OpenAI-compatible `/v1/embeddings` endpoint (OpenAI, Azure, Voyage, Mistral, Together, local TEI/vLLM/Ollama), with optional bearer auth, shipping as v0.8.1.

**Architecture:** New `OpenAIEmbedder` pydantic model in the `EmbedderConfig` discriminated union + a new `OpenAIEmbeddingProvider` satisfying the `Embedder` protocol (`dim` + `embed()`, plus `embed_seconds` for timing parity), wired via one branch in `load_embedder`. stdlib `urllib` only — no new dependency. `fastembed` stays the default.

**Tech Stack:** Python 3.12, pydantic v2, stdlib `urllib.request`/`json`, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-05-30-openai-remote-embedder-design.md`.

**Run tests:** `cd python && uv run --no-sync pytest …`.

---

### Task 1: `OpenAIEmbedder` config model + union

**Files:**
- Modify: `python/src/chunkshop/config.py:778` (union) + add the model above it (after `FastembedEmbedder`, ~line 776)
- Test: `python/tests/chunkshop/test_openai_embedder_config.py` (create)

- [ ] **Step 1: Write the failing config tests**

Create `python/tests/chunkshop/test_openai_embedder_config.py`:

```python
"""Config validation for the openai remote embedder."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from chunkshop.config import CellConfig, OpenAIEmbedder


def test_openai_embedder_minimal_defaults() -> None:
    e = OpenAIEmbedder(type="openai", model="text-embedding-3-small", dim=1536)
    assert e.base_url == "https://api.openai.com/v1"
    assert e.api_key_env is None
    assert e.batch_size == 64 and e.max_retries == 3 and e.timeout == 60.0


def test_openai_embedder_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbedder(type="openai", model="m", dim=8, endpoint="x")  # type: ignore[call-arg]


def test_openai_embedder_requires_http_base_url() -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbedder(type="openai", model="m", dim=8, base_url="ftp://nope")


def test_openai_embedder_rejects_nonpositive_dim() -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbedder(type="openai", model="m", dim=0)


def test_embedder_union_dispatches_on_type() -> None:
    cfg = CellConfig.model_validate(
        {
            "cell_name": "c",
            "source": {"type": "inline", "documents": [{"id": "1", "content": "hi"}]},
            "chunker": {"type": "sentence_aware"},
            "embedder": {"type": "openai", "model": "voyage-3", "dim": 1024,
                         "base_url": "https://api.voyageai.com/v1",
                         "api_key_env": "VOYAGE_API_KEY"},
            "target": {"type": "postgres", "table": "t", "dsn_env": "X"},
        }
    )
    assert cfg.embedder.type == "openai"
    assert cfg.embedder.model == "voyage-3"
```

(If `CellConfig`'s required shape differs, mirror an existing fixture in `tests/chunkshop/` — the point is that `embedder.type: openai` validates through the union. If the full `CellConfig` build is fiddly, keep only the four direct-model tests and drop `test_embedder_union_dispatches_on_type`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_config.py -v`
Expected: import error / `OpenAIEmbedder` undefined.

- [ ] **Step 3: Add the model + extend the union**

In `python/src/chunkshop/config.py`, insert immediately AFTER the `FastembedEmbedder` class (after its `_byo_fields_paired`, ~line 776) and BEFORE the `EmbedderConfig =` line:

```python
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
```

Then change the union line (currently `config.py:778`) from:

```python
EmbedderConfig = Annotated[Union[FastembedEmbedder], Field(discriminator="type")]
```

to:

```python
EmbedderConfig = Annotated[
    Union[FastembedEmbedder, OpenAIEmbedder], Field(discriminator="type")
]
```

(`field_validator`, `model_validator`, `Literal`, `Optional`, `Union`, `Annotated`, `Field` are already imported at the top of `config.py`.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_openai_embedder_config.py
git commit -m "feat(embedders): OpenAIEmbedder config model + union (type: openai)"
```

---

### Task 2: Provider — `embed()` happy path, auth, dim, empty, batching

**Files:**
- Create: `python/src/chunkshop/embedders/openai_provider.py`
- Test: `python/tests/chunkshop/test_openai_embedder_provider.py` (create)

- [ ] **Step 1: Write the failing provider tests**

Create `python/tests/chunkshop/test_openai_embedder_provider.py`:

```python
"""Unit tests for OpenAIEmbeddingProvider (mocked HTTP)."""
from __future__ import annotations

import io
import json

import numpy as np
import pytest

from chunkshop.config import OpenAIEmbedder
from chunkshop.embedders.openai_provider import OpenAIEmbeddingProvider


class _FakeResp:
    """Minimal context-manager stand-in for urlopen()'s return."""

    def __init__(self, payload: dict):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _embeddings_payload(vectors, start_index=0):
    return {
        "data": [
            {"index": start_index + i, "embedding": v} for i, v in enumerate(vectors)
        ]
    }


def _cfg(**kw):
    base = dict(type="openai", model="m", dim=3, base_url="https://api.test/v1")
    base.update(kw)
    return OpenAIEmbedder(**base)


def test_embed_posts_and_returns_float32(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]))

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    p = OpenAIEmbeddingProvider(_cfg())
    out = p.embed(["a", "b"])

    assert out.dtype == np.float32 and out.shape == (2, 3)
    assert captured["url"] == "https://api.test/v1/embeddings"
    assert captured["body"]["model"] == "m"
    assert captured["body"]["input"] == ["a", "b"]
    assert captured["body"]["encoding_format"] == "float"
    assert "authorization" not in captured["headers"]  # keyless
    assert p.embed_seconds >= 0.0


def test_embed_sends_bearer_when_api_key_env_set(monkeypatch):
    monkeypatch.setenv("MY_EMB_KEY", "secret-xyz")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0]]))

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    p = OpenAIEmbeddingProvider(_cfg(api_key_env="MY_EMB_KEY"))
    p.embed(["a"])
    assert captured["headers"]["authorization"] == "Bearer secret-xyz"


def test_missing_api_key_env_raises():
    with pytest.raises(ValueError, match="unset or empty"):
        OpenAIEmbeddingProvider(_cfg(api_key_env="DEFINITELY_NOT_SET_12345"))


def test_response_sorted_by_index(monkeypatch):
    # API returns rows out of order; provider must reorder by `index`.
    payload = {"data": [
        {"index": 1, "embedding": [9.0, 9.0, 9.0]},
        {"index": 0, "embedding": [1.0, 1.0, 1.0]},
    ]}
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(payload),
    )
    out = OpenAIEmbeddingProvider(_cfg()).embed(["first", "second"])
    assert out[0].tolist() == [1.0, 1.0, 1.0]
    assert out[1].tolist() == [9.0, 9.0, 9.0]


def test_dim_mismatch_raises(monkeypatch):
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(_embeddings_payload([[1.0, 2.0]])),  # dim 2
    )
    with pytest.raises(ValueError, match="dim"):
        OpenAIEmbeddingProvider(_cfg()).embed(["a"])  # cfg says dim=3


def test_empty_input_returns_zero_rows():
    out = OpenAIEmbeddingProvider(_cfg()).embed([])
    assert out.shape == (0, 3) and out.dtype == np.float32


def test_batching_splits_requests(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        batch = json.loads(req.data.decode())["input"]
        calls.append(len(batch))
        return _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0]] * len(batch)))

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    p = OpenAIEmbeddingProvider(_cfg(batch_size=2))
    out = p.embed(["a", "b", "c", "d", "e"])
    assert out.shape == (5, 3)
    assert calls == [2, 2, 1]  # 5 inputs at batch_size=2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_provider.py -v`
Expected: import error — provider module doesn't exist.

- [ ] **Step 3: Write the provider (no retry yet)**

Create `python/src/chunkshop/embedders/openai_provider.py`:

```python
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import numpy as np

from chunkshop.config import OpenAIEmbedder as Cfg


class OpenAIEmbeddingProvider:
    """Embedder backed by a remote OpenAI-compatible /v1/embeddings endpoint.

    Network-bound, opt-in alternative to FastembedProvider. Targets OpenAI,
    Azure OpenAI, Voyage, Mistral, Together, or local servers (TEI / vLLM /
    Ollama) via base_url + model (+ optional api_key_env). Stdlib HTTP only —
    no extra dependency.
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.dim = cfg.dim
        self.embed_seconds: float = 0.0
        self._url = cfg.base_url.rstrip("/") + "/embeddings"
        self._headers = {"Content-Type": "application/json"}
        if cfg.api_key_env is not None:
            key = os.environ.get(cfg.api_key_env)
            if not key:
                raise ValueError(
                    f"embedder.api_key_env={cfg.api_key_env!r} but that "
                    f"environment variable is unset or empty"
                )
            self._headers["Authorization"] = f"Bearer {key}"

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        t0 = time.perf_counter()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.cfg.batch_size):
            vectors.extend(self._embed_batch(texts[start : start + self.cfg.batch_size]))
        arr = np.asarray(vectors, dtype=np.float32)
        self.embed_seconds += time.perf_counter() - t0
        if arr.ndim != 2 or arr.shape[1] != self.dim:
            got = arr.shape[1] if arr.ndim == 2 else "?"
            raise ValueError(
                f"embedder model {self.cfg.model!r} produced dim {got}, "
                f"config says dim={self.dim}"
            )
        return arr

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        body = json.dumps(
            {"model": self.cfg.model, "input": batch, "encoding_format": "float"}
        ).encode("utf-8")
        payload = self._post(body)
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError(
                f"embeddings endpoint returned no data for {len(batch)} inputs"
            )
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in ordered]

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(
            self._url, data=body, headers=self._headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


__all__ = ["OpenAIEmbeddingProvider"]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_provider.py -v`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/embedders/openai_provider.py python/tests/chunkshop/test_openai_embedder_provider.py
git commit -m "feat(embedders): OpenAIEmbeddingProvider core embed() (mocked-HTTP unit tests)"
```

---

### Task 3: Retry + error handling

**Files:**
- Modify: `python/src/chunkshop/embedders/openai_provider.py` (replace `_post`)
- Test: `python/tests/chunkshop/test_openai_embedder_provider.py` (append)

- [ ] **Step 1: Write the failing retry/error tests**

Append to `test_openai_embedder_provider.py`:

```python
import urllib.error


def _http_error(code):
    return urllib.error.HTTPError(
        url="https://api.test/v1/embeddings", code=code, msg="x", hdrs=None,
        fp=io.BytesIO(b'{"error":"boom"}'),
    )


def test_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.time.sleep", lambda *_: None
    )
    seq = [_http_error(503), _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0]]))]

    def fake_urlopen(req, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    out = OpenAIEmbeddingProvider(_cfg(max_retries=2)).embed(["a"])
    assert out.shape == (1, 3) and not seq  # both queue items consumed


def test_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.time.sleep", lambda *_: None
    )

    def always_503(req, timeout=None):
        raise _http_error(503)

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", always_503
    )
    with pytest.raises(RuntimeError, match="HTTP 503"):
        OpenAIEmbeddingProvider(_cfg(max_retries=1)).embed(["a"])


def test_non_retryable_4xx_raises_immediately(monkeypatch):
    calls = {"n": 0}

    def bad_request(req, timeout=None):
        calls["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", bad_request
    )
    with pytest.raises(RuntimeError, match="HTTP 400"):
        OpenAIEmbeddingProvider(_cfg(max_retries=3)).embed(["a"])
    assert calls["n"] == 1  # no retries on 400
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_provider.py -k "retr or 4xx or 503" -v`
Expected: FAIL — current `_post` doesn't retry; 503 propagates as raw `HTTPError`.

- [ ] **Step 3: Replace `_post` with `_post_with_retry`**

In `openai_provider.py`, add the module-level constant after the imports:

```python
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
```

Replace the `_post` method AND update `_embed_batch` to call `_post_with_retry`:

In `_embed_batch`, change `payload = self._post(body)` to `payload = self._post_with_retry(body)`.

Replace the `_post` method with:

```python
    def _post_with_retry(self, body: bytes) -> dict:
        for attempt in range(self.cfg.max_retries + 1):
            try:
                req = urllib.request.Request(
                    self._url, data=body, headers=self._headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                retryable = exc.code in _RETRYABLE_STATUS
                if retryable and attempt < self.cfg.max_retries:
                    time.sleep(min(0.5 * (2 ** attempt), 8.0))
                    continue
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:500]
                except Exception:  # pragma: no cover — defensive
                    pass
                raise RuntimeError(
                    f"embeddings request to {self._url} failed: "
                    f"HTTP {exc.code} {detail}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < self.cfg.max_retries:
                    time.sleep(min(0.5 * (2 ** attempt), 8.0))
                    continue
                raise RuntimeError(
                    f"embeddings request to {self._url} failed after "
                    f"{self.cfg.max_retries + 1} attempts: {exc}"
                ) from exc
        raise RuntimeError(  # pragma: no cover — loop always returns/raises
            f"embeddings request to {self._url} exhausted retries"
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_provider.py -v`
Expected: all (10) PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/embedders/openai_provider.py python/tests/chunkshop/test_openai_embedder_provider.py
git commit -m "feat(embedders): retry on 429/5xx with backoff; clear errors (openai embedder)"
```

---

### Task 4: Loader branch + env-gated live test

**Files:**
- Modify: `python/src/chunkshop/embedders/__init__.py`
- Test: `python/tests/chunkshop/test_openai_embedder_live.py` (create)
- Test: append a loader test to `python/tests/chunkshop/test_openai_embedder_provider.py`

- [ ] **Step 1: Write the failing loader test**

Append to `test_openai_embedder_provider.py`:

```python
def test_load_embedder_dispatches_to_openai_provider():
    from chunkshop.embedders import load_embedder
    p = load_embedder(_cfg())
    assert type(p).__name__ == "OpenAIEmbeddingProvider"
    assert p.dim == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_provider.py::test_load_embedder_dispatches_to_openai_provider -v`
Expected: FAIL — `load_embedder` raises `unknown embedder type: OpenAIEmbedder`.

- [ ] **Step 3: Add the loader branch**

In `python/src/chunkshop/embedders/__init__.py`, update the import line and add a branch. Replace:

```python
from chunkshop.config import EmbedderConfig, FastembedEmbedder as FastCfg
```

with:

```python
from chunkshop.config import (
    EmbedderConfig,
    FastembedEmbedder as FastCfg,
    OpenAIEmbedder as OpenAICfg,
)
```

Then inside `load_embedder`, after the `FastCfg` branch's `return FastembedProvider(cfg)` and before the `raise ValueError`, add:

```python
    if isinstance(cfg, OpenAICfg):
        # Lazy import: keep `import chunkshop.embedders` light.
        from chunkshop.embedders.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(cfg)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_provider.py::test_load_embedder_dispatches_to_openai_provider -v`
Expected: PASS.

- [ ] **Step 5: Add the env-gated live test**

Create `python/tests/chunkshop/test_openai_embedder_live.py`:

```python
"""Live smoke test against a real OpenAI-compatible endpoint. Skips unless
CHUNKSHOP_TEST_OPENAI_BASE_URL is set. Point it at a local Ollama/TEI:

    CHUNKSHOP_TEST_OPENAI_BASE_URL=http://localhost:11434/v1 \\
    CHUNKSHOP_TEST_OPENAI_MODEL=nomic-embed-text \\
    CHUNKSHOP_TEST_OPENAI_DIM=768 \\
      uv run --no-sync pytest tests/chunkshop/test_openai_embedder_live.py -v
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from chunkshop.config import OpenAIEmbedder
from chunkshop.embedders.openai_provider import OpenAIEmbeddingProvider

_BASE = os.environ.get("CHUNKSHOP_TEST_OPENAI_BASE_URL")


@pytest.mark.skipif(not _BASE, reason="set CHUNKSHOP_TEST_OPENAI_BASE_URL to run")
def test_live_embed_shape() -> None:
    dim = int(os.environ["CHUNKSHOP_TEST_OPENAI_DIM"])
    cfg = OpenAIEmbedder(
        type="openai",
        model=os.environ["CHUNKSHOP_TEST_OPENAI_MODEL"],
        dim=dim,
        base_url=_BASE,
        api_key_env=os.environ.get("CHUNKSHOP_TEST_OPENAI_KEY_ENV"),
    )
    out = OpenAIEmbeddingProvider(cfg).embed(["hello world", "second string"])
    assert out.shape == (2, dim) and out.dtype == np.float32
```

- [ ] **Step 6: Run (skips without env) + commit**

Run: `uv run --no-sync pytest tests/chunkshop/test_openai_embedder_live.py -v`
Expected: 1 skipped.

```bash
git add python/src/chunkshop/embedders/__init__.py tests/chunkshop/test_openai_embedder_provider.py tests/chunkshop/test_openai_embedder_live.py
git commit -m "feat(embedders): wire openai embedder into load_embedder + env-gated live test"
```

---

### Task 5: Docs + sample YAML

**Files:**
- Create: `python/../docs/reference/embedder-openai.md`
- Create: `docs/samples/sample-openai-embedder.yaml`
- Modify: `docs/embedders.md` (add a section), `docs/AGENT_REFERENCE.md` (note the 2nd embedder type), `docs/reference/README.md` (index row)

- [ ] **Step 1: Write the reference doc**

Create `docs/reference/embedder-openai.md`:

```markdown
# Embedder reference — `type: openai` (remote, OpenAI-compatible)

Opt-in embedder that calls a remote OpenAI-compatible `/v1/embeddings`
endpoint instead of running a model locally. `fastembed` remains the default;
choose this per cell with `embedder.type: openai`.

## Config

| Field | Default | Description |
|-------|---------|-------------|
| `type` | — | `openai` |
| `model` | — | Model name sent in the request (e.g. `text-embedding-3-small`, `voyage-3`, `nomic-embed-text`). |
| `dim` | — | Embedding width. Must match what the model returns — the sink's vector column is fixed at this. A mismatch fails the first batch with a clear error. |
| `base_url` | `https://api.openai.com/v1` | Endpoint root; `/embeddings` is appended. Repoint for other providers / local servers. |
| `api_key_env` | `null` | NAME of the env var holding the bearer token. Omit for keyless local servers. The key is never written in YAML. |
| `batch_size` | `64` | Inputs per request. |
| `timeout` | `60` | Per-request timeout (seconds). |
| `max_retries` | `3` | Retries on connection errors / HTTP 429 / 5xx, exponential backoff. Non-retryable 4xx fail immediately. |

## Provider matrix

| Provider | `base_url` | `model` (example) | `api_key_env` |
|----------|-----------|-------------------|---------------|
| OpenAI | `https://api.openai.com/v1` | `text-embedding-3-small` (dim 1536) | `OPENAI_API_KEY` |
| Voyage (Anthropic's pick) | `https://api.voyageai.com/v1` | `voyage-3` (dim 1024) | `VOYAGE_API_KEY` |
| Mistral | `https://api.mistral.ai/v1` | `mistral-embed` (dim 1024) | `MISTRAL_API_KEY` |
| Together | `https://api.together.xyz/v1` | `BAAI/bge-large-en-v1.5` (dim 1024) | `TOGETHER_API_KEY` |
| Ollama (local) | `http://localhost:11434/v1` | `nomic-embed-text` (dim 768) | *(omit)* |
| TEI / vLLM (local) | `http://localhost:8080/v1` | server-dependent | *(omit or a local token)* |

> **Anthropic** does not offer an embeddings API; its recommended embedding
> provider is **Voyage** (row above). **Cohere** uses a different (`/v1/embed`)
> API shape and is not supported by this embedder.

> **Azure OpenAI** uses an `api-key` header and a deployment in the path rather
> than the standard `Authorization: Bearer` + `{base_url}/embeddings`; it is a
> known limitation of v1.

## Cost & secrets

Keyed providers bill per call. The key is a secret — supply it via
`api_key_env` (an environment variable), never inline in YAML.
```

- [ ] **Step 2: Write the sample YAML**

Create `docs/samples/sample-openai-embedder.yaml`:

```yaml
# Remote embedder example. NOT the default — fastembed is. This shows the
# opt-in `type: openai` embedder against a keyless local server; the commented
# block shows a keyed cloud provider (key supplied via api_key_env, never here).
cell_name: openai_embedder_example
source:
  type: inline
  documents:
    - id: doc-1
      content: "chunkshop can embed against a remote OpenAI-compatible endpoint."
chunker:
  type: sentence_aware
  max_chars: 2000
embedder:
  type: openai
  model: nomic-embed-text          # a model your endpoint serves
  dim: 768                         # MUST match the model's output width
  base_url: http://localhost:11434/v1   # local Ollama; keyless
  # --- keyed cloud provider instead (OpenAI / Voyage / Mistral / Together): ---
  # model: text-embedding-3-small
  # dim: 1536
  # base_url: https://api.openai.com/v1
  # api_key_env: OPENAI_API_KEY    # env var NAME — export the key, don't write it here
  batch_size: 64
  timeout: 60
  max_retries: 3
target:
  type: postgres
  mode: overwrite
  table: openai_embedder_demo
  dsn_env: CHUNKSHOP_DSN
```

- [ ] **Step 3: Add sections to the index docs**

In `docs/embedders.md`, add a short subsection (near the embedder discussion) noting the second embedder type with a link to `reference/embedder-openai.md` and the one-line summary: "`type: openai` — opt-in remote embedder for any OpenAI-compatible `/v1/embeddings` endpoint (OpenAI, Voyage, Mistral, Together, local TEI/vLLM/Ollama)."

In `docs/AGENT_REFERENCE.md`, in the embedder section, add: "Two embedder types: `fastembed` (default, local ONNX) and `openai` (opt-in, remote OpenAI-compatible `/v1/embeddings`; `base_url` + `model` + optional `api_key_env`). See `docs/reference/embedder-openai.md`."

In `docs/reference/README.md`, add an index row near the other references:
`| [`embedder-openai`](embedder-openai.md) | `type: openai` remote embedder (OpenAI-compatible /v1/embeddings; OpenAI/Voyage/Mistral/local). |`

- [ ] **Step 4: Lint the sample YAML parses + commit**

Run: `cd python && uv run --no-sync python -c "import yaml; yaml.safe_load(open('../docs/samples/sample-openai-embedder.yaml')); print('yaml ok')"`
Expected: `yaml ok`.

```bash
git add docs/reference/embedder-openai.md docs/samples/sample-openai-embedder.yaml docs/embedders.md docs/AGENT_REFERENCE.md docs/reference/README.md
git commit -m "docs(embedders): reference + sample + index for the openai remote embedder"
```

---

### Task 6: Changelog + version bump to 0.8.1 + finish

**Files:**
- Modify: `CHANGELOG.md`, `python/pyproject.toml`, `rust/Cargo.toml`, `rust/Cargo.lock`

- [ ] **Step 1: Changelog**

Add under `## Unreleased` (create `### Added` if absent):

```markdown
- **Opt-in remote embedder (`type: openai`).** A second embedder type that calls any OpenAI-compatible `/v1/embeddings` endpoint instead of running locally — covers OpenAI, Azure, Voyage (Anthropic's recommended provider), Mistral, Together, and local servers (TEI / vLLM / Ollama) via `base_url` + `model` + optional `api_key_env` (bearer token from an env var; keyless for local). `fastembed` remains the default. stdlib HTTP only — no new runtime dependency. Retries on 429/5xx with backoff; validates the returned dim. See `docs/reference/embedder-openai.md`.
```

- [ ] **Step 2: Bump versions (Python + Rust must match the tag)**

Edit `python/pyproject.toml` `[project].version` → `0.8.1`.
Edit `rust/Cargo.toml` `[workspace.package].version` → `0.8.1`.

- [ ] **Step 3: Regenerate the Rust lockfile (the release gate)**

Run: `(cd rust && cargo update --workspace)`
This rewrites `rust/Cargo.lock` to 0.8.1. CI builds with `cargo --locked`, so a stale lock would block the crates.io publish (see `docs/RELEASING.md`).

- [ ] **Step 4: Full suite + finish**

Run: `cd python && uv run --no-sync pytest -q` (after `bash ../scripts/dev-setup.sh`).
Expected: PASS (new tests green; the live test skips).

Run the Rust pre-flight that mirrors CI:
`(cd rust && cargo publish --dry-run --locked -p chunkshop-rs)` → ends with "aborting upload due to dry run".

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md python/pyproject.toml rust/Cargo.toml rust/Cargo.lock
git commit -m "chore(release): v0.8.1 — opt-in openai remote embedder"
```

Then finish via `superpowers:finishing-a-development-branch` → PR → (on merge) tag `v0.8.1`.

---

## Self-Review

**Spec coverage:** config model (T1), provider embed/auth/dim/empty/batch (T2), retry/errors (T3), loader + live test (T4), docs + sample (T5), changelog + 0.8.1 bump incl. Cargo.lock (T6). All spec sections covered. ✅

**Placeholder scan:** every code step shows complete code; commands include expected output. The only prose-instruction steps (T5 Step 3) are doc edits where exact wording is given. ✅

**Type consistency:** `OpenAIEmbedder` (config) ↔ `OpenAIEmbeddingProvider` (provider) ↔ `OpenAICfg` alias (loader); fields `model`/`dim`/`base_url`/`api_key_env`/`batch_size`/`timeout`/`max_retries` identical across tasks; `_post_with_retry` (T3) replaces `_post` (T2) and `_embed_batch` is updated to call it; `_RETRYABLE_STATUS` introduced in T3. ✅

**Release discipline:** version bumped in BOTH pyproject + Cargo.toml AND Cargo.lock regenerated (the v0.8.0 lesson), with the `--locked` dry-run pre-flight. ✅
