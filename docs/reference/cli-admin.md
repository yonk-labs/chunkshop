# CLI reference — `init`, `validate`, `prefetch`

The operational/setup commands that bracket an ingest. For the data commands
see [`cli-search.md`](cli-search.md), [`cli-fact-search.md`](cli-fact-search.md),
and [`cli-impact-of.md`](cli-impact-of.md); for `ingest` / `orchestrate` /
`bakeoff` see [`../tutorial.md`](../tutorial.md) and
[`../getting-started.md`](../getting-started.md).

A typical first run is `init` → `validate` → `prefetch` → `ingest`.

---

## `chunkshop init`

Interactive scaffold for a new cell YAML. Prompts for the basics (cell name,
source, table) and writes a ready-to-edit config.

| Option | Default | Description |
|--------|---------|-------------|
| `--out PATH` | `cell.yaml` | Output path for the generated cell YAML. |
| `--force` | off | Overwrite the output file if it already exists. |

```bash
chunkshop init --out my-cell.yaml
# then: chunkshop validate -c my-cell.yaml && chunkshop ingest -c my-cell.yaml
```

---

## `chunkshop validate`

Validate a YAML/JSON config **without running it**. Exits `0` if the config
parses and satisfies the pydantic schema (`extra="forbid"`, discriminated
unions, identifier-safety regexes), non-zero with the validation error
otherwise. Use it in CI or pre-commit to catch a typo'd key before an ingest
spends time embedding.

| Option | Default | Description |
|--------|---------|-------------|
| `--config, -c PATH` | *(required)* | Path to the YAML/JSON cell config. |

```bash
chunkshop validate --config my-cell.yaml
```

---

## `chunkshop prefetch`

Download the embedder model named in a config **ahead of time** so the first
`ingest` never blocks on a model download mid-run (e.g. in an air-gapped or
CI environment, or before a timed benchmark). The model is cached under
`~/.cache/fastembed/`; subsequent ingests reuse it. Shipped in 0.4.3 as part of
the "batteries-included" library-embedding ergonomics.

| Option | Default | Description |
|--------|---------|-------------|
| `--config, -c PATH` | *(required)* | Path to the cell config whose embedder model is fetched. |

```bash
chunkshop prefetch --config my-cell.yaml   # warms the fastembed cache
```

Because the model ID lives in the config, `prefetch` and `ingest` always agree
on what to download — there's no separate model flag to keep in sync.
