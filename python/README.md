# chunkshop (Python)

Reference implementation of the chunkshop ingest tool.

## Install

From source (recommended while alpha):

```bash
cd chunkshop/python
uv sync --extra dev
```

As a path dependency from another project:

```toml
[tool.uv.sources]
chunkshop = { path = "../chunkshop/python", editable = true }
```

## CLI

```bash
# One cell end-to-end:
export AGE_BAKEOFF_PGRG_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
chunkshop ingest --config configs/example-files-to-bge.yaml

# Many cells in parallel (4 at a time, status reports at t=60/120/300/600s):
chunkshop orchestrate --config-dir configs/factorial --concurrency 4

# Smoke test: 1 doc per config:
chunkshop orchestrate --config-dir configs/factorial --smoke
```

See the top-level README for the YAML shape and target table schema.
