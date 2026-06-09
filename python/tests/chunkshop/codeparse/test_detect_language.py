"""Unit tests for language resolution helpers (chunkshop#69).

These back the ``symbol_aware`` chunker's layered language resolution:
- :func:`detect_language` — extension -> tag (path-based).
- :func:`normalize_language_tag` — coerce a caller hint (tag or extension alias).
- :func:`detect_language_from_content` — conservative content heuristic, the
  last resort when no path or hint is available.

The heuristic is deliberately conservative: it returns a language only on a
clear signal and ``None`` otherwise, so a wrong guess can never do worse than
the unknown-language fallback it replaces.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chunkshop.codeparse.langs.regex_fallback import (
    KNOWN_LANGUAGES,
    detect_language,
    detect_language_from_content,
    normalize_language_tag,
)


def test_known_languages_complete():
    assert KNOWN_LANGUAGES == {
        "python", "java", "go", "typescript", "javascript",
        "rust", "c", "cpp", "csharp", "ruby",
    }


@pytest.mark.parametrize(
    "name,expected",
    [
        ("a.py", "python"),
        ("a.ts", "typescript"),
        ("a.tsx", "typescript"),
        ("a.jsx", "javascript"),
        ("a.unknown", None),
        ("noext", None),
    ],
)
def test_detect_language_by_extension(name, expected):
    assert detect_language(Path(name)) == expected


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("typescript", "typescript"),
        ("TypeScript", "typescript"),  # case-insensitive
        ("ts", "typescript"),          # extension alias
        (".tsx", "typescript"),
        ("py", "python"),
        ("python", "python"),
        ("klingon", None),             # junk -> ignored
        ("", None),
        ("  go  ", "go"),              # trimmed
    ],
)
def test_normalize_language_tag(hint, expected):
    assert normalize_language_tag(hint) == expected


# --- all ten languages, driven by the real codeparse fixtures -------------
# Every language codeparse parses must be recoverable from content alone when
# no path/hint is present (chunkshop#69). The fixtures are the same ones the
# tree-sitter parse tests use, so drift between "can parse" and "can detect"
# surfaces here.
_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "codeparse"


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("python/sample.py", "python"),
        ("python/realistic.py", "python"),
        ("java/Sample.java", "java"),
        ("java/Lambdas.java", "java"),
        ("go/sample.go", "go"),
        ("go/closures.go", "go"),
        ("typescript/sample.ts", "typescript"),
        ("typescript/realistic.ts", "typescript"),
        ("javascript/sample.js", "javascript"),
        ("rust/sample.rs", "rust"),
        ("c/sample.c", "c"),
        ("cpp/sample.cpp", "cpp"),
        ("csharp/Sample.cs", "csharp"),
        ("ruby/sample.rb", "ruby"),
    ],
)
def test_content_detection_over_real_fixtures(rel, expected):
    src = (_FIXTURES / rel).read_text()
    assert detect_language_from_content(src) == expected


# --- disambiguation: lookalike language pairs -----------------------------


def test_content_ruby_not_python_despite_shared_def():
    # Ruby and Python both use ``def``; ``end`` / ``puts`` pin it to Ruby.
    src = "def add(a, b)\n  puts a + b\nend\n"
    assert detect_language_from_content(src) == "ruby"


def test_content_c_not_cpp_without_cpp_markers():
    src = "#include <stdio.h>\n\nint main(void) {\n    printf(\"hi\");\n    return 0;\n}\n"
    assert detect_language_from_content(src) == "c"


def test_content_cpp_not_c_with_std_namespace():
    src = "#include <iostream>\nusing namespace std;\nint main() { std::cout << 1; }\n"
    assert detect_language_from_content(src) == "cpp"


def test_content_go_not_java_package_without_semicolon():
    src = "package main\n\nfunc main() {\n\tx := 1\n\t_ = x\n}\n"
    assert detect_language_from_content(src) == "go"


def test_content_python_def_class():
    src = "import os\n\ndef alpha(x):\n    return x\n\nclass Foo:\n    pass\n"
    assert detect_language_from_content(src) == "python"


def test_content_python_type_hints_not_mistaken_for_typescript():
    # ``: int`` / ``-> str`` are not TS markers (string/number/boolean/...).
    src = "def f(x: int) -> str:\n    return str(x)\n"
    assert detect_language_from_content(src) == "python"


def test_content_typescript_interface():
    src = (
        "export interface User { id: number; name: string; }\n"
        "export function greet(u: User): string { return u.name; }\n"
    )
    assert detect_language_from_content(src) == "typescript"


def test_content_typescript_import_not_mistaken_for_python():
    # JS/TS ``import { x } from 'y'`` must not count as a Python import.
    src = (
        "import { useState } from 'react';\n"
        "const App = () => { const [n] = useState(0); return n; };\n"
    )
    assert detect_language_from_content(src) in {"javascript", "typescript"}


def test_content_plain_javascript():
    src = "export const add = (a, b) => a + b;\nfunction mul(a, b) { return a * b; }\n"
    assert detect_language_from_content(src) == "javascript"


def test_content_python_shebang():
    assert detect_language_from_content("#!/usr/bin/env python3\nx = 1\n") == "python"


def test_content_node_shebang():
    assert detect_language_from_content("#!/usr/bin/env node\nvar x = 1;\n") == "javascript"


@pytest.mark.parametrize("text", ["", "   \n  \n", "Just some prose. No code here at all."])
def test_content_ambiguous_returns_none(text):
    assert detect_language_from_content(text) is None


# --- generated / minified guard (chunkshop#71) ----------------------------
# Path-less detection used to classify generated/minified files as code that
# 0.8.3 skipped; embedding the resulting chunk flood OOM'd consumers. The
# content heuristic now skips obviously machine-emitted files (returns None ->
# fall back), but only on the *content-guess* path — explicit signals bypass it.


def test_content_minified_blob_returns_none():
    # A single very long line of JS-looking code reads as minified -> skip.
    minified = "function a(){return 1;}" * 150  # ~3.4k chars, one line
    assert len(minified.splitlines()) == 1 and len(minified) > 2000
    assert detect_language_from_content(minified) is None


def test_content_generated_marker_returns_none():
    src = (
        "// @generated by some-codegen-tool. DO NOT EDIT.\n"
        "export function f1() { return 1; }\n"
        "export function f2() { return 2; }\n"
    )
    assert detect_language_from_content(src) is None


def test_content_sourcemap_marker_returns_none():
    src = (
        "export function add(a, b) { return a + b; }\n"
        "//# sourceMappingURL=bundle.js.map\n"
    )
    assert detect_language_from_content(src) is None


def test_content_normal_code_with_moderately_long_line_still_detected():
    # A long-ish but sub-threshold line in real code must NOT trip the guard.
    long_line = "x = [" + ", ".join(str(i) for i in range(200)) + "]"
    assert 0 < len(long_line) < 2000
    src = f"import os\n\n{long_line}\n\ndef compute():\n    return sum(x)\n"
    assert detect_language_from_content(src) == "python"
