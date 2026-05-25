"""code_summary extractor — per-chunk natural-language summaries (SP-D).

Generates a 1-3 sentence summary for every code chunk and stamps it as
``metadata.summary``. For the first chunk of each file (heuristic: chunk
metadata ``start_line == 1`` or ``symbol_type == "module"``), it also stamps a
file-level rollup as ``metadata.file_summary``.

Backends:
  - ``lede``               (default) chunkshop's extractive lede shim.
  - ``callable``           BYO ``summarize(text, **kwargs) -> str``.
  - ``first_n_sentences``  zero-dep regex sentence split fallback.

All backend imports are LAZY — ``load_extractor`` never triggers lede / vendor
SDK imports at config-load time. If ``backend="lede"`` is requested but the
lede extra isn't installed, the extractor transparently falls back to
``first_n_sentences`` and emits one ``RuntimeWarning`` per process.

Why a kwarg for chunk metadata
------------------------------
chunkshop's ``Extractor`` protocol is ``extract(text) -> ExtractResult`` and
``runner.py`` only feeds text. To enable the file-level rollup heuristic
without touching the runner or the base Protocol, ``extract`` accepts an
*optional* second kwarg ``chunk_metadata: dict | None = None``. Code that
wants file_summary populated must pass that kwarg explicitly. From the
default runner code path the kwarg is absent → only ``summary`` is stamped.
This is the v1 simplification noted in the SP-D plan; a future runner pass
can wire chunk metadata through if a real cross-symbol rollup is wanted.
"""
from __future__ import annotations

import re
import warnings
from importlib import import_module
from typing import Callable, Optional

from chunkshop.config import CodeSummaryExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


# Regex sentence splitter used by the fallback backend. Splits after a
# terminal punctuation followed by whitespace — good enough for code-comment
# and docstring prose.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _first_n_sentences(text: str, max_length: int) -> str:
    """Return the first N sentences of ``text`` whose joined length fits in
    ``max_length`` characters. Sentences are atomic — once the next sentence
    would push the joined length over budget, stop. The returned string is
    therefore ``<= max_length``."""
    if not text or not text.strip():
        return ""
    parts = _SENTENCE_SPLIT.split(text.strip())
    out: list[str] = []
    total = 0
    for sent in parts:
        sent = sent.strip()
        if not sent:
            continue
        # +1 accounts for the joining space (only counted after the first).
        added = len(sent) + (1 if out else 0)
        if total + added > max_length:
            break
        out.append(sent)
        total += added
    return " ".join(out)


def _parse_callable_path(path: str) -> Callable[..., str]:
    """Resolve a ``module.path:function`` string to its callable.

    Raises ``ValueError`` with a clear hint on any failure (bad format,
    missing module, missing attribute, non-callable).
    """
    if not path or ":" not in path:
        raise ValueError(
            f"code_summary: callable_path={path!r} must be of the form "
            "'module.path:function' (e.g. 'myapp.summarizers.openai:summarize')."
        )
    module_path, _, func_name = path.partition(":")
    try:
        mod = import_module(module_path)
    except ImportError as exc:
        raise ValueError(
            f"code_summary: cannot import module {module_path!r} from "
            f"callable_path={path!r}: {exc}"
        ) from exc
    fn = getattr(mod, func_name, None)
    if fn is None:
        raise ValueError(
            f"code_summary: module {module_path!r} has no attribute "
            f"{func_name!r} (callable_path={path!r})."
        )
    if not callable(fn):
        raise ValueError(
            f"code_summary: {path!r} resolves to a non-callable object."
        )
    return fn


class CodeSummaryExtractor:
    """Generates per-chunk natural-language summaries for code chunks.

    See module docstring for backend selection and the optional
    ``chunk_metadata`` kwarg semantics.
    """

    # Process-wide flag so the lede-missing fallback warning is emitted once.
    # Tests may reset this to re-exercise the warning path.
    _lede_fallback_warned: bool = False

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        # Cached resolved callable (only populated for backend="callable",
        # only after the first extract). None means "not resolved yet".
        self._callable: Optional[Callable[..., str]] = None
        # Effective backend after fallback resolution. Set on first extract.
        self._effective_backend: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def extract(
        self, text: str, chunk_metadata: Optional[dict] = None
    ) -> ExtractResult:
        if not text or not text.strip():
            return ExtractResult(tags=[], metadata={"summary": ""})

        summary = self._summarize(text)
        metadata: dict = {"summary": summary}

        if self.cfg.file_summary and self._is_first_chunk_of_file(chunk_metadata):
            # v1 simplification: the file_summary equals the summary of the
            # first-chunk content. A true cross-symbol rollup would require a
            # finalize() pass after all chunks are seen — out of scope for v1.
            # See module docstring for the rationale.
            metadata["file_summary"] = summary

        return ExtractResult(tags=[], metadata=metadata)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _is_first_chunk_of_file(chunk_metadata: Optional[dict]) -> bool:
        if not chunk_metadata:
            return False
        if chunk_metadata.get("start_line") == 1:
            return True
        if chunk_metadata.get("symbol_type") == "module":
            return True
        return False

    def _summarize(self, text: str) -> str:
        backend = self._effective_backend or self.cfg.backend

        if backend == "first_n_sentences":
            return _first_n_sentences(text, self.cfg.max_length)

        if backend == "callable":
            if self._callable is None:
                # Lazy import + cache.
                if not self.cfg.callable_path:
                    raise ValueError(
                        "code_summary: backend='callable' requires "
                        "callable_path to be set (e.g. "
                        "'myapp.summarizers.openai:summarize')."
                    )
                self._callable = _parse_callable_path(self.cfg.callable_path)
            return self._callable(text, max_length=self.cfg.max_length)

        if backend == "lede":
            # Lazy import — keeps load_extractor cheap when lede isn't used.
            try:
                from chunkshop.summarizers.lede import summarize as _lede_summarize
                return _lede_summarize(text, max_length=self.cfg.max_length)
            except ImportError:
                # Fall through to first_n_sentences for the rest of the
                # process; warn once.
                if not type(self)._lede_fallback_warned:
                    warnings.warn(
                        "code_summary: backend='lede' requested but `lede` "
                        "is not installed; falling back to "
                        "'first_n_sentences'. Install with: "
                        "pip install 'chunkshop[lede]'.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    type(self)._lede_fallback_warned = True
                self._effective_backend = "first_n_sentences"
                return _first_n_sentences(text, self.cfg.max_length)

        # Should be unreachable thanks to the pydantic Literal — but be
        # explicit for safety.
        raise ValueError(f"code_summary: unknown backend {backend!r}")
