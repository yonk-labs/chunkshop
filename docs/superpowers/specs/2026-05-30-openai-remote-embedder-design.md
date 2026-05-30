# Remote Embedder (`type: openai`) — Design Spec

**Date:** 2026-05-30
**Branch / worktree:** `feat/openai-embedder` (`../chunkshop-remote-embedder`, off `main` `ccb9410`)
**Status:** approved-by-dialogue (user picked OpenAI-compatible, optional auth, single config-driven embedder, no Cohere)

## Context

chunkshop's only embedder today is `fastembed` (local ONNX/CPU). `EmbedderConfig`
is a pydantic discriminated union (`config.py:778`), and `load_embedder`
(`embedders/__init__.py`) dispatches on it. The `Embedder` protocol is tiny:
`dim: int` + `embed(texts: list[str]) -> np.ndarray`. `FastembedProvider` also
exposes `embed_seconds: float` (cumulative time in `embed()`), which `run_cell`
and the bakeoff read to attribute the embedder's share of ingest wall time.

This adds a second, **opt-in** embedder that calls a remote OpenAI-compatible
`/v1/embeddings` endpoint instead of running locally. It is not the default —
you select it per cell with `embedder.type: openai`. `fastembed` is unchanged.

## Goal / Non-goals

**Goal:** A `type: openai` embedder that POSTs to any OpenAI-compatible
`/v1/embeddings` endpoint, with optional bearer-token auth, so a cell can embed
against OpenAI, Azure OpenAI, Voyage (Anthropic's recommended embedding
provider), Mistral, Together, or a local server (TEI / vLLM / Ollama) by
configuration alone.

**Non-goals**
- Replacing or changing `fastembed` (stays the default).
- Cohere or other non-OpenAI-shaped APIs (different request/response; out of scope).
- A literal `anthropic` type — **Anthropic has no embeddings API**; its ecosystem
  answer is Voyage, reached via `base_url: https://api.voyageai.com/v1`.
- A fully-configurable arbitrary-JSON embedder, streaming, token counting.
- A Rust port (Python path only).
- Storing API keys in YAML (env-var reference only).

## Design

### Config model (`OpenAIEmbedder`, added to the `EmbedderConfig` union)

`_Base` (`extra="forbid"`), so typos raise at config load.

```python
class OpenAIEmbedder(_Base):
    type: Literal["openai"]
    model: str                                   # request "model" field
    dim: int                                     # sink's vector column needs it upfront
    base_url: str = "https://api.openai.com/v1"  # trailing /embeddings appended
    api_key_env: Optional[str] = None            # env var name; None => no auth header
    batch_size: int = 64
    timeout: float = 60.0
    max_retries: int = 3
```

`EmbedderConfig` becomes `Union[FastembedEmbedder, OpenAIEmbedder]` discriminated
on `type`. `base_url` is validated to be http(s); `dim`, `batch_size`,
`max_retries` must be positive.

### Provider (`embedders/openai_provider.py`)

`OpenAIEmbeddingProvider` satisfies `Embedder`:
- `self.dim = cfg.dim`; `self.embed_seconds = 0.0` (timing parity with fastembed).
- `embed(texts)`:
  - empty input → `np.empty((0, dim), np.float32)`.
  - split `texts` into `batch_size` chunks; for each, POST
    `{"model": cfg.model, "input": batch, "encoding_format": "float"}` to
    `{base_url}/embeddings`.
  - parse `response["data"]`, **sort by `index`**, take `embedding` from each.
  - stack all batches → `float32 (N, dim)`; validate `arr.shape[1] == dim`
    (raise `ValueError` on mismatch, same guard as fastembed); accumulate
    `embed_seconds`.

**HTTP** uses stdlib `urllib.request` + `json` — **no new runtime dependency**.
Headers: `Content-Type: application/json`; if `api_key_env` is set, read
`os.environ[api_key_env]` and add `Authorization: Bearer <key>` (raise a clear
error if the env var is unset/empty so a missing key fails fast, not as a 401).

**Auth is optional:** `api_key_env=None` sends no auth header (local servers).

### Loader

`load_embedder` gains a branch: `isinstance(cfg, OpenAIEmbedder)` →
`OpenAIEmbeddingProvider(cfg)`. (Lazy-import the provider inside the branch so
importing the package stays light, matching the fastembed seam.)

### Error handling

A small `_post_with_retry(url, body, headers, timeout, max_retries)` helper:
- Retries on `urllib.error.URLError` (connection) and HTTP **429 / 5xx** with
  exponential backoff (`0.5 * 2**attempt`, capped), up to `max_retries`.
- Non-retryable **4xx** (400 bad model, 401/403 auth) raise immediately with the
  response body in the message.
- Persistent failure after retries raises with the last error.
- An empty or malformed `data` array raises a clear `ValueError`.

## Components & boundaries

- `config.py` — `OpenAIEmbedder` model + union update (+ validators). ~25 lines.
- `embedders/openai_provider.py` — new provider + retry helper. ~110 lines, one file.
- `embedders/__init__.py` — one dispatch branch.
- No change to `runner`, `sink`, `base.Embedder`, or `FastembedProvider`.

Backoff timing uses a constant base (no `Math.random`/jitter needed at this
scale); a fixed exponential schedule keeps the retry test deterministic.

## Testing

- **Unit (mock `urllib.request.urlopen`)**: request shape (url ends `/embeddings`,
  body has `model`/`input`/`encoding_format`); auth header present when
  `api_key_env` set + env present, absent when `api_key_env=None`; missing env
  var → clear error; response parsing with **out-of-order `index`** yields
  correctly-ordered rows; `dim` mismatch raises; empty input → `(0, dim)`;
  batching (texts > batch_size → multiple POSTs, concatenated); retry on a 503
  then success; failure after `max_retries`; non-retryable 400 raises immediately.
- **Config**: pydantic accepts `type: openai`; `extra="forbid"` rejects a typo'd
  key; missing required `model`/`dim` raises; bad `base_url` rejected.
- **Live, env-gated** (`CHUNKSHOP_TEST_OPENAI_BASE_URL`, optional
  `CHUNKSHOP_TEST_OPENAI_KEY_ENV` + `..._MODEL` + `..._DIM`): skips in CI; a dev
  can point it at a local Ollama/TEI to embed a couple strings and assert shape.

No DB needed for any unit test. The provider is network-bound, so the runner's
OMP-thread setup is irrelevant to it.

## Docs (shipped with the feature)

- New `docs/reference/embedder-openai.md` (config table + behavior + errors).
- New cookbook section / table mapping providers → `base_url` / `model` /
  `api_key_env` (OpenAI, Azure, Voyage, Mistral, Together, Ollama, TEI, vLLM).
- `docs/embedders.md` + `docs/AGENT_REFERENCE.md`: note the second embedder type.
- A sample YAML (`docs/samples/sample-openai-embedder.yaml`) — keyless local +
  keyed cloud, with the key as `api_key_env`, never literal.
- `CHANGELOG.md` `### Added`.

## Risks / open questions

- **Provider drift:** "OpenAI-compatible" isn't a hard spec; Azure puts the
  deployment in the path + uses `api-key` header not Bearer. v1 targets the
  standard `Authorization: Bearer` + `{base_url}/embeddings` shape; Azure's
  header quirk is documented as a known limitation (follow-up if needed).
- **dim must be known up front** (sink vector column). If a user sets the wrong
  `dim`, the explicit mismatch check fails the first batch with a clear message
  rather than corrupting the table.
- **Cost/secrets:** keyed providers cost money per call and the key is a secret —
  hence env-var-only, and the sample YAML uses a placeholder env name.
