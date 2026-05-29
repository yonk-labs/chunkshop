"""Deterministic human-readable scope-chain builder.

``build_scope_chain`` is the display-string companion to ``build_fqn``. Both
are derived from the same ``(file_path, symbol_name, parent_name)`` inputs, but
serve different consumers:

    build_fqn("users/svc.py", "get_user", "UserService")
        == "users.svc.UserService.get_user"   # machine join key
    build_scope_chain("users/svc.py", "get_user", "UserService")
        == "svc > UserService > get_user"      # UI / search-result display

``fqn`` stays the graph join key (it keeps up to the last 3 path components so
two files with the same stem don't collide). ``scope_chain`` is optimised for a
human reading a search result, so it uses the file *stem only* — the full path
already lives in ``fqn`` and ``file_path``. The two are intentionally NOT
interchangeable; stamp both.

Path-separator normalisation matches ``build_fqn`` exactly so the same logical
path produces the same scope_chain on Linux, macOS, and Windows. The Rust
RM-C port asserts cross-port equivalence on this field.
"""
from __future__ import annotations

import re
from typing import Optional

_SEP = " > "


def build_scope_chain(
    file_path: str, symbol_name: str, parent_name: Optional[str]
) -> str:
    """Render the enclosing-scope path of *symbol_name* for display.

    Format is ``"<stem> > <parent_name> > <symbol_name>"`` for methods and
    ``"<stem> > <symbol_name>"`` for top-level symbols, where ``<stem>`` is the
    file name with its extension stripped. Examples::

        build_scope_chain("pkg/utils.py", "format", None) == "utils > format"
        build_scope_chain("users/svc.py", "get_user", "UserService")
            == "svc > UserService > get_user"

    Separators (``/`` and ``\\``) are normalised cross-platform before the stem
    is taken, mirroring ``build_fqn`` — the same logical path yields one
    scope_chain regardless of the runtime OS.
    """
    # Normalise separators so the stem is OS-independent. Filter empties to
    # absorb leading slashes and consecutive separators — same semantics as
    # build_fqn.
    normalized = file_path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    last = parts[-1] if parts else normalized
    # Strip the file extension from the stem only.
    stem = re.sub(r"\.[^.]+$", "", last)
    if parent_name:
        return f"{stem}{_SEP}{parent_name}{_SEP}{symbol_name}"
    return f"{stem}{_SEP}{symbol_name}"


__all__ = ["build_scope_chain"]
