# examples/sync_loop.py
"""COPY-ME EXAMPLE — not part of the chunkshop library.

A minimal semaphore-bounded sync loop showing how a CONSUMER drives chunkshop's
incremental primitives. Production orchestration (scheduling, retries, durable
cursor persistence, multi-tenant isolation, Redis) belongs in your service /
chunkshop_api — NOT here. This file is the baseline connector test harness and
a starting point to copy into your own code.
"""
# NOTE: deliberately NO `from __future__ import annotations` here. This file is
# loaded via importlib.util.module_from_spec (see tests/.../test_example_sync_loop.py
# and any consumer copying it that way) WITHOUT registering the module in
# sys.modules. Under PEP 563 stringized annotations, @dataclass resolves field
# types via sys.modules[cls.__module__].__dict__, which is None for such a
# module and raises AttributeError. Eager annotations (no future import) avoid
# that. dict[str, TaskResult] etc. work natively on the required Python 3.12+.
import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from chunkshop.sources.base import Document, IncrementalSource


class SourceTaskType(str, Enum):
    SYNC = "sync"
    PRUNE = "prune"


@dataclass
class TaskResult:
    task_type: SourceTaskType
    success: bool
    docs_emitted: int
    deletes_emitted: int
    new_cursor: Optional[dict]
    error: Optional[Exception]
    elapsed_ms: int


async def _run_one(name, source, cursor, on_document, on_delete, sem) -> TaskResult:
    async with sem:
        start = time.time()
        try:
            if isinstance(source, IncrementalSource):
                docs = await asyncio.to_thread(lambda: list(source.iter_changes_since(cursor)))
                for d in docs:
                    on_document(name, d)
                # cursor_from returns a per-doc DELTA; merge each into the running
                # cursor in iteration order (see IncrementalSource.cursor_from).
                new_cursor = dict(cursor)
                for d in docs:
                    new_cursor.update(source.cursor_from(d))
                return TaskResult(SourceTaskType.SYNC, True, len(docs), 0, new_cursor, None,
                                  int((time.time() - start) * 1000))
            else:
                docs = await asyncio.to_thread(lambda: list(source.iter_documents()))
                for d in docs:
                    on_document(name, d)
                return TaskResult(SourceTaskType.SYNC, True, len(docs), 0, cursor, None,
                                  int((time.time() - start) * 1000))
        except Exception as exc:  # isolate per-source failure
            return TaskResult(SourceTaskType.SYNC, False, 0, 0, None, exc,
                              int((time.time() - start) * 1000))


async def run_sync(sources: dict, cursors: dict, on_document: Callable[[str, Document], None],
                   on_delete: Optional[Callable[[str, str], None]] = None,
                   max_concurrent_tasks: int = 5) -> dict[str, TaskResult]:
    sem = asyncio.Semaphore(max_concurrent_tasks)
    on_delete = on_delete or (lambda n, i: None)
    tasks = {name: asyncio.create_task(
                _run_one(name, src, cursors.get(name, {}), on_document, on_delete, sem))
             for name, src in sources.items()}
    results = {}
    for name, task in tasks.items():
        results[name] = await task
    return results
