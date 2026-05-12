# PR-012 — `--strict` test mode that fails on unexpected skips

**Priority:** P3
**Effort:** S (~1 hour)
**Dependencies:** CI access
**GAP-IDs:** GAP-005

## Problem

11 Python tests + some Rust tests skip when DSN env vars are unset. In CI, where all DSNs should be set, a misconfigured runner silently skips DB-touching coverage instead of failing.

## Solution

Add a strict mode that asserts zero skips in the production-test environment.

### Python side

Add a pytest plugin / hook that, when `CHUNKSHOP_STRICT_TESTS=1` is set, fails the run if any test was skipped:

```python
# conftest.py
def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("CHUNKSHOP_STRICT_TESTS"):
        skipped = sum(1 for r in session.testsfailed_reports if r.outcome == "skipped")
        # actually use the terminalreporter API to count skips
        if skipped > 0:
            raise SystemExit(f"strict mode: {skipped} test(s) skipped")
```

(API specifics need verification against the pytest version; the concept is to convert skips → failures under the env-var gate.)

### Rust side

Rust test-skip is via `eprintln!` + `return` so it's harder to assert on at the framework level. Best route: emit a uniform `eprintln!("CHUNKSHOP_SKIP: <test_name>: <reason>")` and have CI count them:

```bash
# in CI test step:
SKIP_COUNT=$(cargo test ... 2>&1 | grep -c "^CHUNKSHOP_SKIP:")
if [ "$SKIP_COUNT" -gt 0 ] && [ "$CHUNKSHOP_STRICT_TESTS" = "1" ]; then
  echo "strict mode: $SKIP_COUNT Rust tests skipped"
  exit 1
fi
```

### CI gate

Set `CHUNKSHOP_STRICT_TESTS=1` in CI jobs where all DSNs are expected. Don't set it locally.

## Acceptance Criteria

- [ ] With `CHUNKSHOP_STRICT_TESTS=1` set and all DSNs available, `pytest` and `cargo test` succeed.
- [ ] With `CHUNKSHOP_STRICT_TESTS=1` set and a DSN unset, the run fails with a count of skipped tests.
- [ ] Without `CHUNKSHOP_STRICT_TESTS`, behavior is unchanged (current skips-are-fine).

## Risk if Skipped

A CI misconfiguration silently halves test coverage and nobody notices. Found by the next major incident.
