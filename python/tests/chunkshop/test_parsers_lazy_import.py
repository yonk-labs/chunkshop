"""Lean-default guarantee: `import chunkshop.sources.parsers` must not pull
any optional parser library at module load time. Backing libs are imported
inside each parser's `parse()` method.
"""
import importlib


def test_registry_imports_without_extras():
    import chunkshop.sources.parsers as P

    importlib.reload(P)  # must not raise even if pypdf/docx/etc absent
    assert "pdf" in P.DEFAULT_PARSERS
    assert "docx" in P.DEFAULT_PARSERS
    assert "html" in P.DEFAULT_PARSERS


def test_parser_modules_have_no_top_level_backing_lib_import():
    """Static check: each parser file must only import its backing lib inside
    `parse()`, not at module top level — that's what makes the lean default
    work. We do this with a textual scan so the test runs even if the libs
    are installed in this env (in which case a runtime import wouldn't catch
    the regression).
    """
    from pathlib import Path

    parsers_dir = (
        Path(__file__).parents[2] / "src" / "chunkshop" / "sources" / "parsers"
    )
    forbidden = {
        "pdf.py": ("import pypdf", "from pypdf"),
        "docx.py": ("import docx", "from docx"),
        "pptx.py": ("import pptx", "from pptx"),
        "xlsx.py": ("import openpyxl", "from openpyxl"),
        "html.py": ("import bs4", "from bs4"),
    }
    for fname, needles in forbidden.items():
        src = (parsers_dir / fname).read_text(encoding="utf-8").splitlines()
        # Module-top-level imports happen before the first `class ` line.
        top_level: list[str] = []
        for line in src:
            stripped = line.strip()
            if stripped.startswith("class "):
                break
            top_level.append(stripped)
        joined = "\n".join(top_level)
        for needle in needles:
            assert needle not in joined, (
                f"{fname} imports the backing lib at module top-level "
                f"({needle!r}); this breaks the lean-default install."
            )
