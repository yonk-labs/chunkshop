"""Tree-sitter lazy import + regex fallback verification.

Two properties are tested here, both load-bearing:

1. Plain ``import chunkshop.codeparse`` does NOT trigger import of
   ``tree_sitter_python`` (or any tree-sitter package). The heavy native
   wheels stay dormant until ``parse_file()`` runs.
2. When the tree-sitter package is unavailable (we simulate the absence
   with ``monkeypatch.setitem(sys.modules, ..., None)``), ``parse_file``
   still returns a non-empty ParseResult thanks to the regex extractor.
"""
from __future__ import annotations

import sys
from pathlib import Path


def test_codeparse_import_is_lazy() -> None:
    """Top-level import must not pull in tree-sitter packages.

    Runs in a subprocess so prior tests in the same pytest session that
    already imported ``tree_sitter_python`` don't pollute ``sys.modules``.
    Subprocess gets a clean interpreter — anything missing afterwards is
    a real lazy-import violation.
    """
    import subprocess
    import sys as _sys
    import textwrap

    script = textwrap.dedent(
        """
        import sys
        import chunkshop.codeparse  # noqa: F401

        offenders = [
            m for m in sys.modules
            if m == "tree_sitter"
            or m.startswith("tree_sitter_")
            or m.startswith("chunkshop.codeparse.langs.")
            and m != "chunkshop.codeparse.langs"
            and m != "chunkshop.codeparse.langs.regex_fallback"
        ]
        if offenders:
            print("EAGER:" + ",".join(offenders))
            sys.exit(1)
        print("OK")
        """
    )
    proc = subprocess.run(
        [_sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"lazy import violated: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "OK" in proc.stdout


def test_parse_falls_back_to_regex_when_tree_sitter_missing(
    monkeypatch, fixtures_dir: Path
) -> None:
    """Simulate uninstalled tree_sitter_python; parse_file still returns symbols.

    Setting ``sys.modules[name] = None`` is the Python idiom for "fail
    this import" — any subsequent ``import tree_sitter_python`` raises
    ImportError. The wrapper's except clause must catch it and run the
    regex extractor.
    """
    # Force fresh imports by clearing cached modules.
    for mod in list(sys.modules):
        if (
            mod == "tree_sitter_python"
            or mod.startswith("chunkshop.codeparse.langs.python")
        ):
            sys.modules.pop(mod, None)

    monkeypatch.setitem(sys.modules, "tree_sitter_python", None)

    # Re-import the wrapper to pick up the monkeypatched state.
    from chunkshop.codeparse import parse_file

    result = parse_file(
        fixtures_dir / "python" / "sample.py", language="python"
    )
    # Regex fallback emits at least the class + free function + 2 methods.
    assert len(result.symbols) >= 3
    assert any(s.name == "helper" for s in result.symbols)
    assert any(s.name == "Calculator" for s in result.symbols)
    # The wrapper logs "falling back to regex" and the result's parser
    # tag reflects that.
    assert result.parser == "regex"
