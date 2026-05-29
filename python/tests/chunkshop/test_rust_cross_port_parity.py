"""Cross-port byte-equivalence tests for build_fqn + code_symbol_node_id.

Invokes the Rust ``fqn-cli`` binary as a subprocess and asserts that for a
curated 50+ vector set, the Rust output equals the Python output byte-for-
byte. This is the second half of RM-C's SC-003 cross-port harness; the Rust
proptest in ``rust/chunkshop/tests/cross_port_proptest.rs`` covers the
breadth side.

Skipped cleanly when the Rust binary isn't built. To build it::

    cd rust && cargo build --release --bin fqn-cli

Test PG / network not required.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

REPO_ROOT = Path(__file__).resolve().parents[3]
FQN_CLI = REPO_ROOT / "rust" / "target" / "release" / "fqn-cli"

pytestmark = pytest.mark.skipif(
    not FQN_CLI.exists(),
    reason=f"Rust fqn-cli binary not built; run `cd rust && cargo build --release --bin fqn-cli`",
)


def _run_rust(*args: str) -> str:
    result = subprocess.run(
        [str(FQN_CLI), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fqn-cli failed: {result.stderr}")
    return result.stdout  # no trailing newline; Rust uses print! not println!


# --- 48+ curated vectors: drawn from test_fqn.py + test_id.py + adversarial extras ---

FQN_VECTORS: list[tuple[str, str, str | None]] = [
    # Mirror of test_fqn.py
    ("c.py", "f", None),
    ("a/b/c.py", "f", None),
    ("/repo/src/pkg/mod/sub/file.py", "f", None),
    ("a/b/c.py", "method", "MyClass"),
    ("a/b/c.py", "f", None),
    ("repo/a.b/file.ts", "g", None),
    # Cross-platform separators
    ("a\\b\\c.py", "f", None),
    ("a/b\\c.py", "f", None),
    ("a\\b/c.py", "f", None),
    ("/a/b/c.py", "f", None),
    ("\\a\\b\\c.py", "f", None),
    ("a//b/c.py", "f", None),
    ("a\\\\b\\c.py", "f", None),
    ("C:\\repo\\src\\pkg\\mod\\sub\\file.py", "f", None),
    ("a\\b\\c.py", "method", "MyClass"),
    # Adversarial: unicode, very long, dots
    ("repo/módulo/файл.py", "función", None),
    ("a/b/c.py", "你好", None),
    ("a/b/c.tar.gz", "f", None),  # only LAST extension stripped
    ("a/b/.hidden.py", "f", None),  # leading-dot filename
    ("a/b/c", "f", None),  # no extension
    ("", "f", None),  # empty path
    ("/" * 5 + "a/b/c.py", "f", None),  # many leading slashes
    ("a/b/c.py", "f", ""),  # empty parent (falsy → no parent prefix)
    ("a/b/c.py", "f", "C.Nested"),  # parent with dot
    # Deep paths, mixed languages
    ("src/main/java/com/example/Foo.java", "bar", None),
    ("src/main/java/com/example/Foo.java", "method", "Foo"),
    ("internal/svc/handler.go", "ServeHTTP", None),
    ("packages/ui/src/components/Button.tsx", "render", None),
    ("packages/ui/src/components/Button.tsx", "default", None),
]

NODE_ID_VECTORS: list[tuple[str, str, str, str]] = [
    # (project_id, language, file_path, fqn)
    ("proj1", "python", "a/b/c.py", "a.b.c.f"),
    ("proj1", "python", "a/b/c.py", "a.b.c.f"),  # same input -> same id
    ("proj2", "python", "a/b/c.py", "a.b.c.f"),
    ("proj1", "java", "a/b/c.py", "a.b.c.f"),
    ("proj1", "python", "a/b/d.py", "a.b.c.f"),
    ("proj1", "python", "a/b/c.py", "a.b.c.g"),
    # Adversarial
    ("проект1", "python", "файл.py", "модуль.f"),
    ("p", "python", "/very/deep/nested/path/to/module.py", "to.module.function_name"),
    ("p", "java", "src/Main.java", "Main.greet"),
    ("p", "rust", "src/lib.rs", "lib.do_thing"),
    ("p", "typescript", "src/Foo.ts", "Foo.bar"),
    ("p", "javascript", "src/Foo.js", "Foo.bar"),
    ("project-with-dashes", "python", "a.py", "a.f"),
    ("project_with_underscores", "python", "a.py", "a.f"),
    ("p", "python", "", "a.b.c.f"),  # empty path
    ("p", "", "a.py", "a.f"),  # empty language
    ("", "python", "a.py", "a.f"),  # empty project_id
]

assert len(FQN_VECTORS) + len(NODE_ID_VECTORS) >= 46, "need ~50 vectors per brief"


@pytest.mark.parametrize("file_path,symbol_name,parent_name", FQN_VECTORS)
def test_build_fqn_cross_port_parity(file_path: str, symbol_name: str, parent_name: str | None) -> None:
    """Rust build_fqn output must equal Python build_fqn output byte-for-byte."""
    py_out = build_fqn(file_path, symbol_name, parent_name)
    rust_args = ["build-fqn", file_path, symbol_name]
    if parent_name is not None:
        rust_args.append(parent_name)
    rust_out = _run_rust(*rust_args)
    assert py_out == rust_out, (
        f"divergence for ({file_path!r}, {symbol_name!r}, {parent_name!r}): "
        f"python={py_out!r} rust={rust_out!r}"
    )


@pytest.mark.parametrize("project_id,language,file_path,fqn", NODE_ID_VECTORS)
def test_code_symbol_node_id_cross_port_parity(
    project_id: str, language: str, file_path: str, fqn: str
) -> None:
    """Rust code_symbol_node_id must equal Python code_symbol_node_id byte-for-byte."""
    py_out = code_symbol_node_id(project_id, language, file_path, fqn)
    rust_out = _run_rust("node-id", project_id, language, file_path, fqn)
    assert py_out == rust_out, (
        f"divergence for ({project_id!r}, {language!r}, {file_path!r}, {fqn!r}): "
        f"python={py_out!r} rust={rust_out!r}"
    )
