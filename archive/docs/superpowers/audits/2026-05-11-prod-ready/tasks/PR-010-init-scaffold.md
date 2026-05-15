# PR-010 — `chunkshop init` scaffolding command

**Priority:** P3
**Effort:** M (~half day)
**Dependencies:** none
**GAP-IDs:** GAP-013

## Problem

New users authoring their first chunkshop YAML have to copy from `docs/samples/` and edit by hand. An interactive `init` command would lower the bar.

## Solution

Add `chunkshop init` subcommand that prompts for:
- Backend (postgres / mariadb / sqlite / clickhouse)
- Corpus path (glob)
- Embedder model (default: `Xenova/bge-small-en-v1.5-int8`)
- Chunker (default: `hierarchy`)
- Output cell YAML path

Emits a `cell.yaml` ready to `ingest`.

### Sketch

```python
@cli.command()
@click.option("--out", default="cell.yaml", type=click.Path(path_type=Path))
def init(out: Path):
    """Interactive scaffold for a new chunkshop cell YAML."""
    cell_name = click.prompt("Cell name", default="my_cell")
    backend = click.prompt("Backend", type=click.Choice(["postgres","mariadb","sqlite","clickhouse"]), default="postgres")
    corpus = click.prompt("Corpus path (glob)", default="./docs/*.md")
    model = click.prompt("Embedder model", default="Xenova/bge-small-en-v1.5-int8")
    # ... emit YAML using a per-backend template ...
```

## Acceptance Criteria

- [ ] `chunkshop init` prompts and emits a working `cell.yaml`.
- [ ] The emitted YAML passes `chunkshop validate` (depends on PR-008).
- [ ] `chunkshop ingest --config cell.yaml` works against a configured DSN.

## Risk if Skipped

None — users have sample YAMLs to copy. Nice-to-have only.
