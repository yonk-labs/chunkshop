# PR-006 — Replace Python lib `print()` with module-level `logging`

**Priority:** P2
**Effort:** S (~1 hour)
**Dependencies:** none
**GAP-IDs:** GAP-004

## Problem

Six `print()` calls in non-CLI Python source code emit to stdout when chunkshop is used as a library, mixing chunkshop's progress / debug output with the host application's output.

**Evidence:**
- `src/chunkshop/runner.py:36` — per-line stdout of cell output.
- `src/chunkshop/orchestrator.py:68, 81, 96, 135` — orchestration progress lines.
- `src/chunkshop/extractors/spacy_entities.py:26` — spaCy model-download notice.

The `orchestrator.py` ones are arguable — `chunkshop orchestrate` IS a CLI feature — but the `runner.py` and `spacy_entities.py` `print()` calls fire from library mode too.

## Solution

Convert to `logging` with module-level loggers. Configure the CLI entry points to wire a stdout handler at INFO level so the CLI user-visible behavior is unchanged.

### Pattern per file

```python
# Top of each module:
import logging

logger = logging.getLogger(__name__)

# Replace:
print(f"[orchestrator] started {cp.name} pid={h.proc.pid}", flush=True)

# With:
logger.info("started %s pid=%s", cp.name, h.proc.pid)
```

### CLI handler wire-up

In `python/src/chunkshop/cli.py`, near the top of each subcommand:

```python
import logging
import sys

def _setup_cli_logging():
    """Configure stdout logging so CLI subcommands show progress to the user."""
    root = logging.getLogger("chunkshop")
    if not root.handlers:  # idempotent — only set up once per process
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
```

Call `_setup_cli_logging()` at the top of each `@cli.command()` function.

### Files to update

- [ ] `src/chunkshop/runner.py` — 1 print
- [ ] `src/chunkshop/orchestrator.py` — 4 prints
- [ ] `src/chunkshop/extractors/spacy_entities.py` — 1 print
- [ ] `src/chunkshop/cli.py` — add `_setup_cli_logging` helper, call from each subcommand

## Acceptance Criteria

- [ ] `chunkshop ingest`, `chunkshop bakeoff`, `chunkshop orchestrate` produce visually identical stdout output to the previous version (the prefix `[chunkshop.runner]` etc. is a tiny cosmetic delta — acceptable).
- [ ] Importing `chunkshop` from a host app and calling `run_cell(...)` produces zero stdout output unless the host app has configured `logging`.
- [ ] `grep -rn '^\s*print(' python/src/chunkshop/` returns zero hits outside `cli.py`.

## Risk if Skipped

Library mode users get unwanted log noise. CLI users see identical behavior either way. Low-impact, moderate-friction for embedders.
