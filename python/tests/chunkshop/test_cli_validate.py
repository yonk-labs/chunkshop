"""Regression test for chunkshop#10: `chunkshop validate --config bakeoff.yaml`
must not vomit a wall of pydantic `extra_forbidden` errors. validate detects
config shape (ingest cell vs bakeoff) and dispatches to the right schema."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from chunkshop.cli import cli

REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST_SAMPLE = REPO_ROOT / "docs/samples/sample.yaml"
BAKEOFF_SAMPLE = REPO_ROOT / "docs/samples/bakeoff.yaml"


def test_validate_ingest_cell_still_works():
    """Regression: the existing ingest-cell validate path must keep
    working unchanged. `sample.yaml` is the headline sample."""
    assert INGEST_SAMPLE.exists(), "missing fixture"
    runner = CliRunner()
    result = runner.invoke(cli, ["validate", "--config", str(INGEST_SAMPLE)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    # Existing pre-fix output included the resolved source/chunker/etc — keep.
    assert "source:" in result.output


def test_validate_bakeoff_config_is_recognised():
    """The fix: a bakeoff YAML must validate cleanly via the bakeoff schema
    and report it. Pre-fix this printed nine `extra_forbidden` errors."""
    assert BAKEOFF_SAMPLE.exists(), "missing fixture"
    runner = CliRunner()
    result = runner.invoke(cli, ["validate", "--config", str(BAKEOFF_SAMPLE)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    # Should be a positive signal that the bakeoff schema was used —
    # not the ingest schema's "source:"/"target:" summary lines.
    assert "bakeoff" in result.output.lower()


def test_validate_broken_yaml_still_errors():
    """A truly malformed config still fails (exit != 0). Make sure the
    detect-and-dispatch logic doesn't accidentally swallow real errors."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("broken.yaml").write_text("not a real config: {{{ bad yaml")
        result = runner.invoke(cli, ["validate", "--config", "broken.yaml"])
        assert result.exit_code != 0, result.output


def test_validate_neither_shape_errors_helpfully():
    """A YAML that's neither an ingest cell nor a bakeoff config should
    fail clearly, not silently fall through. (Pick whichever schema the
    document is closer to, report its errors.)"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Valid YAML, no fields either schema accepts.
        Path("nonsense.yaml").write_text("cell_name: x\nfoo: bar\nbaz: 1\n")
        result = runner.invoke(cli, ["validate", "--config", "nonsense.yaml"])
        assert result.exit_code != 0, result.output
        # Error should be readable, not a stack trace.
        assert "validate" in result.output.lower() or "fail" in result.output.lower()
