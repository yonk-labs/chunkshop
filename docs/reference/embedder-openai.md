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
> known limitation of this embedder.

## Behavior

- Inputs are sent in `batch_size` chunks; response `data` is reordered by its
  `index` field before stacking, so vector order always matches input order.
- The returned width is checked against `dim`; a mismatch raises immediately
  rather than writing a wrong-width vector to the table.
- Empty input returns a `(0, dim)` array (no request made).
- `Authorization: Bearer <key>` is sent only when `api_key_env` is set; a
  configured-but-unset env var fails fast at construction.

## Cost & secrets

Keyed providers bill per call. The key is a secret — supply it via
`api_key_env` (an environment variable), never inline in YAML.
