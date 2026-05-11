"""Top-level pytest configuration.

Currently provides one feature: strict mode that fails the run if any test
was skipped. Enable by setting `CHUNKSHOP_STRICT_TESTS=1` in CI environments
where all DSNs and extras are expected to be present.

Skipped tests in chunkshop are almost always DSN-conditional (the test runs
against a real DB and skips when the env var is unset). In CI, a misconfigured
runner that doesn't set a DSN env var should fail the build instead of silently
losing test coverage.

Local developers leave the env var unset → skips are tolerated as today.
"""
from __future__ import annotations

import os
import sys


def pytest_sessionfinish(session, exitstatus):
    if not os.environ.get("CHUNKSHOP_STRICT_TESTS"):
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is None:
        return
    skipped = tr.stats.get("skipped", [])
    if not skipped:
        return
    tr.write_sep(
        "=",
        f"CHUNKSHOP_STRICT_TESTS=1: {len(skipped)} test(s) skipped",
        red=True,
    )
    for r in skipped:
        tr.write_line(f"  SKIP: {getattr(r, 'nodeid', r)}")
    # pytest's exit code is computed by main.py from the worst seen outcome;
    # by sessionfinish time the value is locked. Force the process exit.
    sys.exit(1)
