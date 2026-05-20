"""Issue #7: `chunkshop.sources` must not eager-import optional-backend
modules — consumers that only need one backend should be able to
``pip install chunkshop`` (no extras) and ``from chunkshop.sources import
load_source`` without dragging in pymysql / sqlite-vec / clickhouse-connect
/ boto3.

Verified out-of-process so the import state is genuinely fresh (the test
runner already has every backend loaded via `[all-backends]`)."""
import subprocess
import sys


_OPTIONAL = [
    "chunkshop.sources.mariadb_table",
    "chunkshop.sources.sqlite_table",
    "chunkshop.sources.clickhouse_table",
    "chunkshop.sources.s3",
]


def test_importing_sources_does_not_eager_load_optional_backends():
    code = (
        "import sys, chunkshop.sources;"
        f"opt = {_OPTIONAL!r};"
        "loaded = [m for m in opt if m in sys.modules];"
        "assert not loaded, f'eager-loaded optional backends: {loaded}'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert proc.returncode == 0, (
        f"sources eager-loads optional backends.\nstderr:\n{proc.stderr}"
    )
