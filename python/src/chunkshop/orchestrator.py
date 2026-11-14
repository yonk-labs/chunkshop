"""Parallel orchestration: spawn N cells as subprocesses with checkpoint polling.

Each cell runs as a subprocess invocation of `python -m chunkshop.cli ingest
--config X`. Subprocess isolation matters because (1) fastembed / ONNX Runtime
has process-global state that doesn't play nicely with thread sharing, and
(2) a silent crash in one cell must not take down siblings or the orchestrator.
"""
from __future__ import annotations
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Seconds to wait after SIGTERM before SIGKILLing a cell's process group.
_TERM_GRACE_SECONDS = 10.0
_OUTPUT_TAIL_BYTES = 4096


@dataclass
class CellHandle:
    config_path: Path
    proc: subprocess.Popen
    started_at: float
    done_at: Optional[float] = None
    returncode: Optional[int] = None
    out: Optional[object] = None  # temp file receiving combined stdout/stderr


@dataclass
class OrchestrationResult:
    total: int
    succeeded: int
    failed: int
    cells: list[dict] = field(default_factory=list)


def _cell_argv(config_path: Path) -> list[str]:
    return [sys.executable, "-m", "chunkshop.cli", "ingest", "--config", str(config_path)]


def _spawn_cell(config_path: Path) -> CellHandle:
    env = os.environ.copy()
    cmd = _cell_argv(config_path)
    # A temp file, not a pipe: nobody drains a pipe while we poll, so a chatty
    # child would block once the ~64 KiB pipe buffer filled.
    out = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,  # lets us signal the whole process group
        )
    except BaseException:
        out.close()
        raise
    return CellHandle(config_path=config_path, proc=proc, started_at=time.time(), out=out)


def _output_tail(h: CellHandle) -> str:
    try:
        h.out.seek(0, os.SEEK_END)
        size = h.out.tell()
        h.out.seek(max(0, size - _OUTPUT_TAIL_BYTES))
        return h.out.read(_OUTPUT_TAIL_BYTES).decode("utf-8", errors="replace")
    except (OSError, ValueError, AttributeError):
        return ""


def _killpg(h: CellHandle, sig: int) -> None:
    try:
        os.killpg(os.getpgid(h.proc.pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_all(handles: list[CellHandle]) -> None:
    """SIGTERM each cell's process group, wait up to the grace period, SIGKILL
    any survivor, and reap every child."""
    for h in handles:
        _killpg(h, signal.SIGTERM)
    deadline = time.monotonic() + _TERM_GRACE_SECONDS
    for h in handles:
        try:
            h.proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for h in handles:
        # Still alive after the grace period: SIGKILL its whole process group.
        if h.proc.poll() is None:
            _killpg(h, signal.SIGKILL)
            h.proc.wait()


def orchestrate(
    configs: list[Path],
    concurrency: int = 4,
    checkpoint_seconds: Optional[list[int]] = None,
    overall_timeout_seconds: int = 2 * 60 * 60,  # 2h default
) -> OrchestrationResult:
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    checkpoints = sorted(checkpoint_seconds or [60, 120, 300, 600])
    pending = list(configs)
    running: list[CellHandle] = []
    done: list[CellHandle] = []
    timed_out: list[CellHandle] = []
    started = time.time()
    next_checkpoint_idx = 0

    try:
        while pending or running:
            # Fill pool
            while pending and len(running) < concurrency:
                cp = pending.pop(0)
                h = _spawn_cell(cp)
                running.append(h)
                logger.info("started %s pid=%s", cp.name, h.proc.pid)

            # Poll for completions
            still_running: list[CellHandle] = []
            for h in running:
                rc = h.proc.poll()
                if rc is None:
                    still_running.append(h)
                else:
                    h.returncode = rc
                    h.done_at = time.time()
                    status = "OK" if rc == 0 else f"FAIL(rc={rc})"
                    wall = h.done_at - h.started_at
                    logger.info("finished %s %s wall=%.1fs", h.config_path.name, status, wall)
                    done.append(h)
            running = still_running

            # Checkpoint report
            elapsed = time.time() - started
            while next_checkpoint_idx < len(checkpoints) and elapsed >= checkpoints[next_checkpoint_idx]:
                _checkpoint_report(running, done, elapsed)
                next_checkpoint_idx += 1

            # Overall timeout
            if elapsed > overall_timeout_seconds:
                logger.warning(
                    "OVERALL TIMEOUT at %.0fs, killing %d workers", elapsed, len(running)
                )
                _terminate_all(running)
                timed_out = list(running)
                for h in running:
                    h.returncode = -1
                    h.done_at = time.time()
                    done.append(h)
                break

            if running or pending:
                time.sleep(0.25)

        succeeded = sum(1 for h in done if h.returncode == 0)
        cell_rows = []
        for h in done:
            row = {
                "config": str(h.config_path),
                "rc": h.returncode,
                "wall_seconds": round((h.done_at or time.time()) - h.started_at, 2),
                "status": (
                    "ok" if h.returncode == 0
                    else "timeout" if h in timed_out else "failed"
                ),
            }
            if h.returncode != 0:
                row["output_tail"] = _output_tail(h)
            cell_rows.append(row)
        # Cells never started (overall timeout hit first) count as failed.
        for cp in pending:
            cell_rows.append(
                {"config": str(cp), "rc": None, "wall_seconds": 0.0, "status": "not_started"}
            )
        return OrchestrationResult(
            total=len(configs), succeeded=succeeded, failed=len(configs) - succeeded, cells=cell_rows
        )
    except BaseException:
        # KeyboardInterrupt etc.: cells run in their own sessions, so Ctrl-C
        # never reaches them. Stop them before propagating.
        _terminate_all(running)
        raise
    finally:
        for h in done + running:
            if h.out is not None:
                h.out.close()


def _checkpoint_report(running: list[CellHandle], done: list[CellHandle], elapsed: float) -> None:
    rows = []
    now = time.time()
    for h in running:
        rows.append(f"  RUN  {h.config_path.name}  t={now - h.started_at:.0f}s")
    for h in done:
        status = "OK" if h.returncode == 0 else f"FAIL({h.returncode})"
        rows.append(f"  DONE {h.config_path.name}  {status}")
    body = "\n".join(rows) if rows else "  (nothing to report)"
    logger.info(
        "checkpoint t=%.0fs (%d running, %d done)\n%s", elapsed, len(running), len(done), body
    )
