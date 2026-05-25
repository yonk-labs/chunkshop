# `blob` connector

**Module**: `chunkshop_connectors.blob`
**Type**: Source (verified-tier connector)
**Ship status**: verified
**Optional extra**: `chunkshop-connectors[blob]` (boto3)
**Since**: 2026-05-25 (commit `4b8cf51`)

## Purpose

Ingest objects from any S3-compatible blob store: AWS S3, Cloudflare R2,
Google Cloud Storage interop endpoint, Oracle Cloud Storage, MinIO, etc.
Auth is access-key only (this tier deliberately excludes OAuth — use
the `gdrive` connector for that). Each object's `ETag` becomes the
chunkshop `Document.fingerprint`, so chunkshop's fingerprint sync path
only re-ingests objects whose ETag changed.

## Config schema

`chunkshop_connectors.blob.ConfigModel` (pydantic v2, `extra="forbid"`):

| Field          | Type      | Default | Notes |
|----------------|-----------|---------|-------|
| `bucket`       | `str`     | **Required**, `min_length=1` | Bucket name. |
| `prefix`       | `str`     | `""`    | Key prefix filter passed to `list_objects_v2`. |
| `endpoint_url` | `str?`    | `None`  | S3-compatible endpoint (R2, GCS interop, MinIO, etc.). |
| `region`       | `str?`    | `None`  | AWS region. |
| `access_key`   | `str?`    | `None`  | Access key. Falls through to boto3's default credential chain when unset. |
| `secret_key`   | `str?`    | `None`  | Secret key. Falls through to boto3's default credential chain when unset. |

When `access_key`/`secret_key` are unset, boto3's standard credential
resolution applies: env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`),
`~/.aws/credentials`, IAM role on EC2/ECS.

## Public API

```python
class BlobConnector:
    sync_mode = SyncMode.FINGERPRINT

    def __init__(self, config: dict[str, Any]) -> None: ...

    # Source
    def iter_documents(self) -> Iterator[Document]: ...
```

Factory: `chunkshop_connectors.blob.factory(config: dict) -> BlobConnector`.

Note: this connector implements `Source` only, NOT `IncrementalSource`.
Change detection is via fingerprint comparison at the consumer level —
chunkshop emits a `Document` for every object on every sync, with
`fingerprint=etag`, and the sink's upsert skips unchanged rows.

## Behavior contract

1. **Sync mode is `FINGERPRINT`.** Every object emits a `Document`; the
   consumer (chunkshop's sink) dedupes via `fingerprint == ETag`.
2. **`list_objects_v2` is paginated.** Buckets with >1000 keys are
   handled.
3. **Pseudo-directory markers are skipped.** Keys ending in `/` are
   filtered out (S3 lacks real directories; some tools fake them with
   zero-byte `key/` objects).
4. **Per-object failure isolation.** A single failed `get_object` is
   logged with `logger.exception` and skipped — the rest of the bucket
   continues.
5. **UTF-8 decode with `errors="replace"`** on object bodies. Binary
   files are not skipped (unlike `gdrive`); they land as garbled text.
   Pre-filter via `prefix` if you need to exclude them.
6. **boto3 is imported lazily** inside `__init__`. Importing
   `chunkshop_connectors.blob` without the `[blob]` extra works (for
   registry inspection); instantiation requires boto3.

## Inputs

- S3-compatible bucket + optional key prefix.
- AWS credentials via the standard boto3 chain or explicit
  `access_key`/`secret_key`.

## Outputs

Each yielded `Document`:

| Field         | Value |
|---------------|-------|
| `id`          | `s3://<bucket>/<key>` |
| `content`     | UTF-8 body (with `errors="replace"`) |
| `title`       | last path segment of the key |
| `metadata`    | `{bucket, key}` |
| `fingerprint` | object's ETag (S3-quoted hex string) |

## Errors

| Exception | When |
|-----------|------|
| `pydantic.ValidationError` | At `factory()` time — extra keys, missing/empty `bucket`. |
| `botocore.exceptions.ClientError` | Bucket missing, access denied, credentials invalid. Raised on `list_objects_v2` (not caught — surfaces to the runner). |
| Per-object `Exception` | Caught + logged + skipped (does NOT propagate). |

## Example: minimal

```yaml
cell_name: my_blob_ingest
source:
  type: connector
  connector: blob
  config:
    bucket: my-docs
chunker: {type: sentence_aware}
embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: blob_kb
  table: chunks
  mode: overwrite
```

## Example: realistic (MinIO with custom endpoint)

```yaml
cell_name: minio_docs
source:
  type: connector
  connector: blob
  config:
    bucket: rag-corpus
    prefix: 2026/markdown/
    endpoint_url: https://minio.internal.example/
    region: us-east-1
    access_key: ${MINIO_ACCESS_KEY}
    secret_key: ${MINIO_SECRET_KEY}
  sync: {mode: fingerprint}
chunker:
  type: hierarchy
  prefix_heading: true
  max_chars: 2000
extractor:
  type: rake_keywords
  top_k: 10
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: minio_kb
  table: chunks
  mode: append
  source_tag: minio_2026
  promote_metadata:
    - {path: key, type: text}
```

## Example: Cloudflare R2

```yaml
source:
  type: connector
  connector: blob
  config:
    bucket: my-r2-bucket
    endpoint_url: https://<account_id>.r2.cloudflarestorage.com
    access_key: ${R2_ACCESS_KEY}
    secret_key: ${R2_SECRET_KEY}
```

## How it integrates with the pipeline

`BlobConnector` is a `Source`. Combined with chunkshop's `target.mode:
append` + `source_tag`, you can use it for incremental-by-fingerprint
ingest: chunks for unchanged objects (same ETag) are no-ops because
upserts on the chunks table check primary key `{doc_id}::{seq_num}`.

For multi-cloud, the experimental `r2`, `gcs`, `oci` connectors are
stubs — use `blob` with the appropriate `endpoint_url` instead.

## Tests proving the contract

- `python/connectors/tests/test_blob_connector.py`:
  - registry + tier marker
  - `ConfigModel` validation (extra-key reject, empty-bucket reject)
  - hermetic listing + fetching via
    `chunkshop_connectors.testing.mocks.blob.FakeS3` monkeypatch
  - per-object failure isolation
  - pseudo-directory marker skipping
- Live demo: `python/connectors/examples/e2e_s3_mocked.py` (uses the
  FakeS3 pattern locally — no AWS credentials needed).

## See also

- [`docs/connectors/_status.md`](../connectors/_status.md) — connector tier table
- Reference: [`source-http`](source-http.md) — for URL-based ingest
- Reference: [`source-s3-core`](source-s3-core.md) — chunkshop core's S3Source (similar shape, less feature-rich)
- [`docs/cookbook/incremental-sources.md`](../cookbook/incremental-sources.md)
