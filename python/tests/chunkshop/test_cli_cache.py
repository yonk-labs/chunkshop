"""`chunkshop cache` — the CLI surface for the derivation cache.

The command was promised by `ross.py`/`ross.rs` docstrings, SPEC.md and the
roadmap long before it existed. These tests pin the contract those references
make: a resolvable root, a cheap preview, and a purge that confirms before it
deletes.

Two properties get disproportionate attention because they are the ones that
would hurt in the field:

  * **NN-6** — a text-only install must be able to find and clear its cache.
    The cache module is pure stdlib, so none of this may require a media or
    parser extra.
  * **Purge only removes cache entries.** The root is user-configurable and
    may point somewhere shared; deleting a neighbouring file would be a data
    loss bug, not a tidiness one.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from chunkshop.cli import cli
from chunkshop.sources.derivers.cache import DerivationCache
from chunkshop.sources.parsers.result import ParseResult


def _seed(root: Path, n: int) -> DerivationCache:
    """Write ``n`` real entries through the library's own put() path."""
    cache = DerivationCache(root=root)
    for i in range(n):
        cache.put(f"content{i}", "deriv-hash", ParseResult(text=f"t{i}", metadata={"i": i}))
    return cache


def _cell_yaml(tmp_path: Path, cache_root: Path | None, enabled: bool = True) -> Path:
    root_line = f"    root: {cache_root}\n" if cache_root is not None else ""
    p = tmp_path / "cell.yaml"
    p.write_text(
        "cell_name: cache_test\n"
        "source:\n"
        "  type: files\n"
        f"  glob: {tmp_path}/corpus/*\n"
        "  derive:\n"
        '    ".png":\n'
        "      rung: image_meta\n"
        "  cache:\n"
        f"    enabled: {str(enabled).lower()}\n"
        f"{root_line}"
        "chunker:\n"
        "  type: hierarchy\n"
        "embedder:\n"
        "  type: fastembed\n"
        "  model_name: Xenova/bge-small-en-v1.5-int8\n"
        "  dim: 384\n"
        "target:\n"
        "  type: sqlite\n"
        "  dsn_env: SQLITE_PATH\n"
        "  database: ignored\n"
        "  table: chunks\n"
        "  mode: overwrite\n"
        "  source_tag: cache_test\n"
    )
    return p


# --- path -----------------------------------------------------------------


def test_path_respects_chunkshop_cache_dir(tmp_path):
    res = CliRunner().invoke(
        cli, ["cache", "path"], env={"CHUNKSHOP_CACHE_DIR": str(tmp_path / "explicit")}
    )
    assert res.exit_code == 0, res.output
    assert res.output.strip() == str(tmp_path / "explicit")


def test_path_falls_back_to_xdg_cache_home(tmp_path):
    """CHUNKSHOP_CACHE_DIR > XDG_CACHE_HOME > ~/.cache, then chunkshop/derived."""
    res = CliRunner().invoke(
        cli, ["cache", "path"],
        env={"CHUNKSHOP_CACHE_DIR": None, "XDG_CACHE_HOME": str(tmp_path / "xdg")},
    )
    assert res.exit_code == 0, res.output
    assert res.output.strip() == str(tmp_path / "xdg" / "chunkshop" / "derived")


def test_path_emits_the_bare_path_and_nothing_else(tmp_path):
    """`cache path` must compose: `du -sh $(chunkshop cache path)`.

    The provenance note is echoed to stderr, so stdout is exactly one line:
    the path. Asserted on the last line of combined output too, which holds
    whether or not the installed click separates the streams for CliRunner.
    """
    cfg = _cell_yaml(tmp_path, tmp_path / "cellroot")
    res = CliRunner().invoke(cli, ["cache", "path", "--config", str(cfg)])
    assert res.exit_code == 0, res.output
    assert res.output.strip().splitlines()[-1] == str(tmp_path / "cellroot")


def test_path_uses_cells_custom_cache_root(tmp_path):
    """A user debugging one cell gets that cell's root, not the default."""
    cfg = _cell_yaml(tmp_path, tmp_path / "cellroot")
    res = CliRunner().invoke(
        cli, ["cache", "path", "--config", str(cfg)],
        env={"CHUNKSHOP_CACHE_DIR": str(tmp_path / "default-not-used")},
    )
    assert res.exit_code == 0, res.output
    assert str(tmp_path / "cellroot") in res.output
    assert "default-not-used" not in res.output


def test_path_for_cell_without_explicit_root_uses_default(tmp_path):
    cfg = _cell_yaml(tmp_path, None)
    res = CliRunner().invoke(
        cli, ["cache", "path", "--config", str(cfg)],
        env={"CHUNKSHOP_CACHE_DIR": str(tmp_path / "thedefault")},
    )
    assert res.exit_code == 0, res.output
    assert str(tmp_path / "thedefault") in res.output


def test_path_says_so_when_the_cell_has_caching_disabled(tmp_path):
    """`enabled: false` still resolves a root — but silently printing it
    would imply the cell writes there."""
    cfg = _cell_yaml(tmp_path, tmp_path / "cellroot", enabled=False)
    res = CliRunner().invoke(cli, ["cache", "path", "--config", str(cfg)])
    assert res.exit_code == 0, res.output
    assert "DISABLED" in res.output


def test_path_for_source_type_with_no_derivation_cache(tmp_path):
    """Only `files` carries a derive block. Report the default root and say why."""
    p = tmp_path / "http.yaml"
    p.write_text(
        "cell_name: http_cell\n"
        "source:\n  type: http\n  urls:\n    - https://example.com\n"
        "chunker:\n  type: hierarchy\n"
        "embedder:\n  type: fastembed\n  model_name: Xenova/bge-small-en-v1.5-int8\n  dim: 384\n"
        "target:\n  type: sqlite\n  dsn_env: SQLITE_PATH\n  database: ignored\n"
        "  table: chunks\n  mode: overwrite\n  source_tag: http_cell\n"
    )
    res = CliRunner().invoke(
        cli, ["cache", "path", "--config", str(p)],
        env={"CHUNKSHOP_CACHE_DIR": str(tmp_path / "thedefault")},
    )
    assert res.exit_code == 0, res.output
    assert "no derivation cache" in res.output
    assert str(tmp_path / "thedefault") in res.output


def test_path_reports_an_unloadable_config_as_a_clean_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a cell\n")
    res = CliRunner().invoke(cli, ["cache", "path", "--config", str(bad)])
    assert res.exit_code != 0
    assert "could not load" in res.output


# --- info -----------------------------------------------------------------


def test_info_counts_entries_and_size(tmp_path):
    root = tmp_path / "c"
    _seed(root, 4)
    res = CliRunner().invoke(cli, ["cache", "info"], env={"CHUNKSHOP_CACHE_DIR": str(root)})
    assert res.exit_code == 0, res.output
    assert "entries: 4" in res.output
    assert str(root) in res.output
    assert "size:" in res.output


def test_info_on_a_root_that_does_not_exist_is_not_an_error(tmp_path):
    res = CliRunner().invoke(
        cli, ["cache", "info"], env={"CHUNKSHOP_CACHE_DIR": str(tmp_path / "never-made")}
    )
    assert res.exit_code == 0, res.output
    assert "entries: 0" in res.output
    assert "does not exist" in res.output


def test_info_on_an_empty_but_existing_root(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    res = CliRunner().invoke(cli, ["cache", "info"], env={"CHUNKSHOP_CACHE_DIR": str(root)})
    assert res.exit_code == 0, res.output
    assert "entries: 0" in res.output
    assert "does not exist" not in res.output


def test_info_ignores_unrelated_files_in_the_root(tmp_path):
    root = tmp_path / "shared"
    _seed(root, 2)
    (root / "notes.txt").write_text("not a cache entry")
    (root / "sub").mkdir()
    (root / "sub" / "data.bin").write_bytes(b"\x00" * 4096)
    res = CliRunner().invoke(cli, ["cache", "info"], env={"CHUNKSHOP_CACHE_DIR": str(root)})
    assert res.exit_code == 0, res.output
    assert "entries: 2" in res.output


def test_info_count_matches_what_purge_removes(tmp_path):
    """`info` is a preview of `purge`, so the two walks must agree exactly."""
    root = tmp_path / "c"
    cache = _seed(root, 5)
    (root / "stray.txt").write_text("x")
    count, _size = cache.stats()
    assert count == 5
    assert cache.purge() == count


# --- purge ----------------------------------------------------------------


def test_purge_prompts_and_declining_deletes_nothing(tmp_path):
    root = tmp_path / "c"
    _seed(root, 3)
    res = CliRunner().invoke(
        cli, ["cache", "purge"], env={"CHUNKSHOP_CACHE_DIR": str(root)}, input="n\n"
    )
    assert res.exit_code != 0, res.output
    assert "Delete 3 cache entries?" in res.output
    assert "Aborted" in res.output
    assert len(list(root.rglob("*.json"))) == 3


def test_purge_prints_the_root_before_asking(tmp_path):
    """Never a surprise which directory is about to be emptied."""
    root = tmp_path / "c"
    _seed(root, 1)
    res = CliRunner().invoke(
        cli, ["cache", "purge"], env={"CHUNKSHOP_CACHE_DIR": str(root)}, input="n\n"
    )
    before_prompt = res.output.split("Delete 1 cache entries?")[0]
    assert str(root) in before_prompt


def test_purge_confirmed_removes_entries(tmp_path):
    root = tmp_path / "c"
    _seed(root, 3)
    res = CliRunner().invoke(
        cli, ["cache", "purge"], env={"CHUNKSHOP_CACHE_DIR": str(root)}, input="y\n"
    )
    assert res.exit_code == 0, res.output
    assert "purged 3 entries" in res.output
    assert list(root.rglob("*.json")) == []


def test_purge_yes_bypasses_the_prompt(tmp_path):
    root = tmp_path / "c"
    _seed(root, 2)
    # No input supplied at all: if the prompt were reached this would fail.
    res = CliRunner().invoke(
        cli, ["cache", "purge", "--yes"], env={"CHUNKSHOP_CACHE_DIR": str(root)}
    )
    assert res.exit_code == 0, res.output
    assert "Delete" not in res.output
    assert "purged 2 entries" in res.output
    assert list(root.rglob("*.json")) == []


def test_purge_with_no_cache_present_reports_zero_and_exits_zero(tmp_path):
    """Scripting `cache purge` before a re-ingest must not fail on run one."""
    res = CliRunner().invoke(
        cli, ["cache", "purge"], env={"CHUNKSHOP_CACHE_DIR": str(tmp_path / "never-made")}
    )
    assert res.exit_code == 0, res.output
    assert "nothing to purge" in res.output
    assert "0 entries" in res.output


def test_purge_of_an_empty_root_does_not_prompt(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    res = CliRunner().invoke(cli, ["cache", "purge"], env={"CHUNKSHOP_CACHE_DIR": str(root)})
    assert res.exit_code == 0, res.output
    assert "Delete" not in res.output


def test_purge_leaves_non_cache_files_alone(tmp_path):
    """The root is user-configurable and may be shared. Deleting a
    neighbouring file would be data loss."""
    root = tmp_path / "shared"
    _seed(root, 2)
    keep = root / "notes.txt"
    keep.write_text("keep me")
    (root / "sub").mkdir()
    keep2 = root / "sub" / "data.bin"
    keep2.write_bytes(b"\x00" * 16)

    res = CliRunner().invoke(
        cli, ["cache", "purge", "--yes"], env={"CHUNKSHOP_CACHE_DIR": str(root)}
    )
    assert res.exit_code == 0, res.output
    assert "purged 2 entries" in res.output
    assert keep.read_text() == "keep me"
    assert keep2.exists()


def test_purge_acts_on_the_cells_root_not_the_default(tmp_path):
    cell_root = tmp_path / "cellroot"
    default_root = tmp_path / "defaultroot"
    _seed(cell_root, 3)
    _seed(default_root, 7)
    cfg = _cell_yaml(tmp_path, cell_root)

    res = CliRunner().invoke(
        cli, ["cache", "purge", "--config", str(cfg), "--yes"],
        env={"CHUNKSHOP_CACHE_DIR": str(default_root)},
    )
    assert res.exit_code == 0, res.output
    assert "purged 3 entries" in res.output
    assert list(cell_root.rglob("*.json")) == []
    assert len(list(default_root.rglob("*.json"))) == 7, "default root must be untouched"


# --- the real ingest path -------------------------------------------------


def test_cli_acts_on_the_cache_a_real_derive_run_writes(tmp_path):
    """End-to-end: entries written by FilesSource's own derive path are the
    ones `info` counts and `purge` removes. A CLI that resolved a different
    root than ingest writes to would be useless and pass every unit test."""
    pytest.importorskip("PIL")
    from PIL import Image

    from chunkshop.config import load_config
    from chunkshop.sources import load_source

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("alpha-one.png", "beta-two.png"):
        Image.new("RGB", (8, 8), (1, 2, 3)).save(corpus / name)

    cache_root = tmp_path / "cellroot"
    cfg_path = _cell_yaml(tmp_path, cache_root)
    docs = list(load_source(load_config(cfg_path).source).iter_documents())
    assert len(docs) == 2
    assert len(list(cache_root.rglob("*.json"))) == 2, "derive run must populate the cache"

    res = CliRunner().invoke(cli, ["cache", "info", "--config", str(cfg_path)])
    assert res.exit_code == 0, res.output
    assert "entries: 2" in res.output

    res = CliRunner().invoke(cli, ["cache", "purge", "--config", str(cfg_path), "--yes"])
    assert res.exit_code == 0, res.output
    assert "purged 2 entries" in res.output
    assert list(cache_root.rglob("*.json")) == []


# --- NN-6 -----------------------------------------------------------------


def test_cache_commands_need_no_media_or_parser_extra(tmp_path):
    """NN-6: a text-only install must be able to find and clear its cache.

    Asserted rather than assumed — every media/parser-gated package is made
    unimportable in a fresh interpreter before the commands run, so the test
    holds on this fully-extra'd dev venv exactly as it would on a bare one.
    """
    code = textwrap.dedent(f'''
        import sys
        BLOCKED = {{"pypdf","docx","pptx","openpyxl","bs4","lxml","html5lib",
                    "pytesseract","av","pydub","librosa","soundfile","cv2",
                    "moviepy","whisper","torch","transformers"}}
        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ImportError("NN-6: %s is extra-gated" % name)
                return None
        sys.meta_path.insert(0, Blocker())

        from click.testing import CliRunner
        from chunkshop.cli import cli
        env = {{"CHUNKSHOP_CACHE_DIR": {str(tmp_path / "nn6")!r}}}
        r = CliRunner()
        for args in (["cache","path"],["cache","info"],["cache","purge"]):
            res = r.invoke(cli, args, env=env)
            assert res.exit_code == 0, (args, res.output, res.exception)
    ''')
    subprocess.run([sys.executable, "-c", code], check=True)


def test_cache_module_itself_imports_nothing_media(tmp_path):
    """The narrower, sharper claim: importing the cache module pulls in no
    imaging, vision or parser library. This is what makes the CLI surface
    safe on a text-only install in the first place."""
    code = textwrap.dedent('''
        import sys
        import chunkshop.sources.derivers.cache  # noqa: F401
        loaded = {m.split(".")[0] for m in sys.modules}
        forbidden = {"PIL","pypdf","docx","pptx","openpyxl","av","cv2",
                     "pytesseract","torch","transformers"} & loaded
        assert not forbidden, "cache module imported %s" % sorted(forbidden)
    ''')
    subprocess.run([sys.executable, "-c", code], check=True)
