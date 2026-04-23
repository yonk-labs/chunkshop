"""Origin-agnostic summarizer shims.

Each sub-module exposes ``summarize(text: str, **kwargs) -> str`` so a user YAML
can reference them uniformly via ``module: chunkshop.summarizers.<name>``. Adapts
underlying libraries (skimr, sumy, ...) whose native APIs may return structured
objects or require multi-step setup.

Opt-in pip extras (``[skimr]``, ``[sumy]``) install the underlying libraries;
these shim modules import them lazily inside ``summarize`` so chunkshop core
never loads a summarizer unless the user's YAML asks for one.
"""
