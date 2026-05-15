# PR-008 — `chunkshop validate <yaml>` dry-run command

**Priority:** P2
**Effort:** M (~half day, two CLI surfaces to wire)
**Dependencies:** none
**GAP-IDs:** GAP-012

## Problem

The only way to validate a chunkshop YAML config today is to run `chunkshop ingest`, which opens DB connections and creates tables before any error surfaces. A user iterating on config tweaks pays the connection-cost penalty on every iteration, even when they only want to check that the YAML is syntactically right.

## Solution

Add a `validate` subcommand to both CLIs that loads + parses + runs pydantic/serde validation, exiting 0/1 without touching DBs.

### Python side (`python/src/chunkshop/cli.py`)

```python
@cli.command()
@click.option("--config", required=True, type=click.Path(exists=True, path_type=Path))
def validate(config: Path):
    """Validate a YAML config without running it. Exits 0 if valid, non-zero otherwise."""
    try:
        from chunkshop.config import load_config
        cfg = load_config(config)
        click.echo(f"[validate] OK — cell {cfg.cell_name!r}")
        click.echo(f"  source:   {cfg.source.type}")
        click.echo(f"  chunker:  {cfg.chunker.type}")
        click.echo(f"  embedder: {cfg.embedder.model_name} (dim={cfg.embedder.dim})")
        click.echo(f"  target:   {cfg.target.type} → {cfg.target.database_name}.{cfg.target.table}")
    except Exception as e:
        click.echo(f"[validate] FAIL: {e}", err=True)
        sys.exit(1)
```

### Rust side (`rust/chunkshop/src/main.rs`)

```rust
#[derive(clap::Subcommand)]
enum Cmd {
    Ingest { ... },
    Bakeoff { ... },
    /// Validate a YAML config without running it. Exits 0 if valid.
    Validate {
        #[arg(long)]
        config: PathBuf,
    },
}

// In the dispatch:
Cmd::Validate { config } => {
    match chunkshop::config::load_config(&config) {
        Ok(cfg) => {
            println!("[validate] OK — cell {:?}", cfg.cell_name);
            println!("  source:   {:?}", cfg.source.type_str());
            println!("  chunker:  {:?}", cfg.chunker.type_str());
            // ... etc
            std::process::exit(0);
        }
        Err(e) => {
            eprintln!("[validate] FAIL: {e:#}");
            std::process::exit(1);
        }
    }
}
```

(`type_str()` helpers may need adding if not present; or just `format!("{:?}", ...)`.)

## Acceptance Criteria

- [ ] `chunkshop validate --config docs/samples/sample.yaml` returns exit 0 and prints a summary.
- [ ] `chunkshop validate --config /tmp/typo.yaml` (e.g., misspelled `chunker.type`) returns non-zero with pydantic's error.
- [ ] `chunkshop-rs validate --config docs/samples/sample.yaml` same behavior.
- [ ] No DB connection attempted during `validate` (verified by running with no DSN env vars set — should still succeed for syntactically valid configs that don't reference DSN env vars; though config validation may try `os.environ[...]` lookups in some validators — check).
- [ ] Help text shipped: `chunkshop validate --help`.

## Risk if Skipped

Users iterate on YAML tweaks slowly. Frustration; not a correctness gap.

## Notes

The Python `load_config` already does the full pydantic validation; we're just exposing it as a CLI entry point. Same for Rust's `serde + custom validate_*` calls in `config.rs`.
