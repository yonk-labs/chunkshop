# PR-003 — Branded lazy-import errors for backend drivers

**Priority:** P1
**Effort:** S (~1 hour)
**Dependencies:** none (combines with PR-002 for defense in depth)
**GAP-IDs:** GAP-016

## Problem

When a user has chunkshop installed without the backend extras (the GAP-016 footgun PR-002 fixes by README), they currently see a generic `ModuleNotFoundError: No module named 'pymysql'`. They have to read the chunkshop source to figure out which `pip install 'chunkshop[X]'` extra to add.

A small wrapping import gives them a chunkshop-branded error that names the fix.

## Solution

Wrap the driver-library imports in `python/src/chunkshop/backends/{mariadb,sqlite,clickhouse}.py` with a try/except that re-raises with a branded message.

### Pattern

```python
# python/src/chunkshop/backends/mariadb.py
try:
    import pymysql  # noqa: F401  -- imported for side-effects + downstream use
except ImportError as e:
    raise ImportError(
        "chunkshop's MariaDB backend requires the 'mariadb' extra. "
        "Install with: pip install 'chunkshop[mariadb]' "
        "(or 'chunkshop[all-backends]' for all 4 backends)."
    ) from e
```

Repeat for `clickhouse_connect` in `clickhouse.py` and `sqlite_vec` in `sqlite.py`.

### Notes on placement

The wrapper goes at module top-level — before any code that calls into the driver. Right after `import os` / `import pathlib`, before any chunkshop-specific imports that might transitively try to use the driver.

## Acceptance Criteria

- [ ] In a fresh venv WITHOUT `chunkshop[mariadb]` extras installed, running `chunkshop ingest --config docs/samples/sample-mariadb.yaml` produces the branded error, not the raw `ModuleNotFoundError`.
- [ ] Same for `chunkshop[sqlite]` and `chunkshop[clickhouse]`.
- [ ] Test added: monkeypatch `sys.modules` to remove `pymysql` and assert the branded error message.
- [ ] PR-002 changes (README install command) already landed (or land in same PR).

## Risk if Skipped

The error message is the user's only signal at first-failure. Generic `ModuleNotFoundError` makes them think chunkshop doesn't support their backend. Branded error makes them feel taken care of.

## Notes

- Don't bury the import in a function — `from chunkshop.backends.mariadb import MariadbBackend` must trigger the check, otherwise the user discovers the gap later.
- The Rust side doesn't have this problem — its 4 backend dependencies are non-optional in `Cargo.toml`. Only Python's optional-extras model creates the pattern.
