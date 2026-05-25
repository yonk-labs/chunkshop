"""Per-language extractors.

Each ``langs/<name>.py`` exposes ``parse(*, source: bytes, file_path: str,
project_id: str = 'default') -> ParseResult``. The :mod:`tree_sitter_wrapper`
imports them lazily.
"""
