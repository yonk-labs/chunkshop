# `s3` source — chunkshop core S3 reader (ETag cursor)

**Module**: `chunkshop.sources.s3`
**Type**: Source (chunkshop core)
**Ship status**: verified
**Optional extra**: `chunkshop[s3]` (boto3)
**Since**: extended this session — `IncrementalSource` implementation

## Purpose

List a bucket+prefix in S3 and yield one `Document` per object. This is
chunkshop core's S3 reader — narrower than the
`chunkshop-connectors[blob]` connector but ships in core (no plugin
package required). Use this one for chunkshop pipelines that want to
stay on the core install.

The session promoted it to a full `IncrementalSource` with a `{key:
etag}` map cursor that skips unchanged objects on re-sync.

## Config schema

`chunkshop.config.S3Source` (pydantic v2, `extra="forbid"`):

| Field          | Type     | Default | Notes |
|----------------|----------|---------|-------|
| `type`         | `Literal["s3"]` | **Required** | Discriminator. |
| `bucket`       | `str`    | **Required** | Bucket name. |
| `prefix`       | `str`    | `""`    | Key prefix filter. |
| `endpoint_url` | `str?`   | `None`  | S3-compatible endpoint (MinIO / R2 / etc.). |

AWS credentials resolve via the standard boto3 chain — there are no
config fields for access_key/secret_key. Export `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` (or use `~/.aws/credentials` / IAM role) before
running ingest.

## Public API

```python
from chunkshop.sources.s3 import S3Source

class S3Source:
    sync_mode = SyncMode.CURSOR  # exposed as CURSOR; semantics are fingerprint-based

    def __init__(self, cfg: S3SourceCfg) -> None: ...

    # Source
    def iter_documents(self) -> Iterator[Document]: ...

    # IncrementalSource
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterator[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...
```

## Behavior contract

1. **Sync mode is `CURSOR`** but the underlying semantics are FINGERPRINT
   — the cursor is the full `{key: etag}` map. Consumers persist a single
   opaque dict between runs (same shape as `HttpSource`'s `{url:
   {etag, last_modified}}`).
2. **Per-key skip on unchanged ETag.** `iter_changes_since` re-lists the
   bucket and yields only objects whose current ETag differs from the
   cursor entry.
3. **`list_objects_v2` paginated** (handles >1000 keys).
4. **Sequential fetch.** No retries, no parallelism. Fail-fast on first
   error from `get_object`.
5. **UTF-8 decode with `errors="replace"`** on object bodies.
6. **boto3 imported lazily** inside `_client()`. `pip install chunkshop`
   alone does NOT pull boto3 — you need `chunkshop[s3]`.

## Inputs

- Bucket + optional key prefix.
- Optional S3-compatible endpoint URL (MinIO / R2 / GCS interop).
- AWS credentials via the standard boto3 chain.

## Outputs

Each yielded `Document`:

| Field         | Value |
|---------------|-------|
| `id`          | `s3://<bucket>/<key>` |
| `content`     | UTF-8 body (with `errors="replace"`) |
| `title`       | `None` |
| `metadata`    | `{bucket, key, size, etag}` |
| `fingerprint` | the object's ETag |

## Errors

| Exception | When |
|-----------|------|
| `RuntimeError` | `import boto3` failed — install with `pip install chunkshop[s3]`. |
| `botocore.exceptions.ClientError` | Bucket missing, access denied, etc. |

## Example: minimal

```yaml
source:
  type: s3
  bucket: my-corpus
  prefix: docs/
```

## Example: MinIO local

```yaml
source:
  type: s3
  bucket: rag-test
  prefix: 2026/
  endpoint_url: http://localhost:9000
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY set in env
```

## How it integrates with the pipeline

Loaded by `chunkshop.sources.__init__.load_source` from the
`S3Source` config discriminator. Identical role to `HttpSource` /
`PgTableSource` — emits Documents, consumer drives chunking.

This source overlaps with the `chunkshop-connectors[blob]` connector;
the differences:

| | `chunkshop.sources.s3` | `chunkshop_connectors.blob` |
|---|---|---|
| Package | core | plugin |
| Config style | discrete YAML type (`type: s3`) | generic `type: connector, connector: blob` |
| Access key in YAML | No (boto3 chain only) | Yes (optional `access_key`/`secret_key`) |
| Region in YAML | No | Yes |
| Pseudo-dir skipping | No | Yes (skips keys ending `/`) |
| Tier metadata | n/a | `@verified` |

If you're already using `chunkshop-connectors`, prefer `blob`. If you're
sticking to core, use this.

## Tests proving the contract

- `tests/chunkshop/test_s3_source.py`:
  - listing pagination
  - cursor-based skip when ETag matches
  - cursor merge through `merge_cursor`
  - decode-error handling
- Demo: `python/connectors/examples/e2e_s3_mocked.py`.

## See also

- Reference: [`source-blob`](source-blob.md) — plugin-side equivalent
- Reference: [`source-http`](source-http.md), [`source-pg-table`](source-pg-table.md)
- [`docs/cookbook/incremental-sources.md`](../cookbook/incremental-sources.md)
