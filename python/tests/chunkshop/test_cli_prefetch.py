"""`chunkshop prefetch` — resolves the embedder model from a config and warms
the fastembed cache, so first ingest()/store() never blocks (0.4.3)."""
import pytest
from click.testing import CliRunner

from chunkshop.cli import cli


@pytest.fixture(autouse=True)
def _no_cli_logging(monkeypatch):
    # Invoking a CLI command runs _setup_cli_logging(), which sets the
    # "chunkshop" logger propagate=False process-wide — that leaks into
    # later caplog-based warning tests. These tests don't exercise logging,
    # so stub it to keep the side effect contained.
    monkeypatch.setattr("chunkshop.cli._setup_cli_logging", lambda **_kw: None)


def test_prefetch_resolves_model_from_config_and_fetches(tmp_path, monkeypatch):
    cfgfile = tmp_path / "cell.yaml"
    cfgfile.write_text("ignored — load_config is mocked")

    class _Emb:
        model_name = "Xenova/bge-small-en-v1.5-int8"

    class _Rt:
        log_format = "text"

    class _Cfg:
        embedder = _Emb()
        runtime = _Rt()

    monkeypatch.setattr("chunkshop.cli.load_config", lambda _p: _Cfg())

    captured = {}

    def _fake_load_embedder(emb):
        captured["emb"] = emb  # network download stubbed out
        return object()

    monkeypatch.setattr("chunkshop.embedders.load_embedder", _fake_load_embedder)

    res = CliRunner().invoke(cli, ["prefetch", "--config", str(cfgfile)])

    assert res.exit_code == 0, res.output
    assert "Xenova/bge-small-en-v1.5-int8" in res.output
    assert "cached and ready" in res.output
    assert captured["emb"] is _Cfg.embedder


def test_prefetch_requires_config():
    res = CliRunner().invoke(cli, ["prefetch"])
    assert res.exit_code != 0
    assert "--config" in res.output
