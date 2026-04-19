"""Parallel orchestration: spawn N cells as subprocesses with checkpoint polling.

Each cell runs as a subprocess invocation of `python -m chunkshop.cli ingest
--config X`. Subprocess isolation matters because (1) fastembed / ONNX Runtime
has process-global state that doesn't play nicely with thread sharing, and
(2) a silent crash in one cell must not take down siblings or the orchestrator.
"""
from __future__ import annotations
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CellHandle:
    config_path: Path
    proc: subprocess.Popen
    started_at: float
    done_at: Optional[float] = None
    returncode: Optional[int] = None


@dataclass
class OrchestrationResult:
    total: int
    succeeded: int
    failed: int
    cells: list[dict] = field(default_factory=list)


def _spawn_cell(config_path: Path) -> CellHandle:
    env = os.environ.copy()
    cmd = [sys.executable, "-m", "chunkshop.cli", "ingest", "--config", str(config_path)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,  # lets us SIGKILL the process group on timeout
    )
    return CellHandle(config_path=config_path, proc=proc, started_at=time.time())


def orchestrate(
    configs: list[Path],
    concurrency: int = 4,
    checkpoint_seconds: Optional[list[int]] = None,
    overall_timeout_seconds: int = 2 * 60 * 60,  # 2h default
) -> OrchestrationResult:
    checkpoints = sorted(checkpoint_seconds or [60, 120, 300, 600])
    pending = list(configs)
    running: list[CellHandle] = []
    done: list[CellHandle] = []
    started = time.time()
    next_checkpoint_idx = 0

    while pending or running:
        # Fill pool
        while pending and len(running) < concurrency:
            cp = pending.pop(0)
            h = _spawn_cell(cp)
            running.append(h)
            print(f"[orchestrator] started {cp.name} pid={h.proc.pid}", flush=True)

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
                print(
                    f"[orchestrator] finished {h.config_path.name} {status} wall={wall:.1f}s",
                    flush=True,
                )
                done.append(h)
        running = still_running

        # Checkpoint report
        elapsed = time.time() - started
        while next_checkpoint_idx < len(checkpoints) and elapsed >= checkpoints[next_checkpoint_idx]:
            _checkpoint_report(running, done, elapsed)
            next_checkpoint_idx += 1

        # Overall timeout
        if elapsed > overall_timeout_seconds:
            print(
                f"[orchestrator] OVERALL TIMEOUT at {elapsed:.0f}s, killing {len(running)} workers",
                flush=True,
            )
            for h in running:
                try:
                    os.killpg(os.getpgid(h.proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                h.returncode = -1
                h.done_at = time.time()
                done.append(h)
            break

        if running or pending:
            time.sleep(0.25)

    succeeded = sum(1 for h in done if h.returncode == 0)
    failed = len(done) - succeeded
    cell_rows = [
        {
            "config": str(h.config_path),
            "rc": h.returncode,
            "wall_seconds": round((h.done_at or time.time()) - h.started_at, 2),
        }
        for h in done
    ]
    return OrchestrationResult(total=len(configs), succeeded=succeeded, failed=failed, cells=cell_rows)


def _checkpoint_report(running: list[CellHandle], done: list[CellHandle], elapsed: float) -> None:
    rows = []
    now = time.time()
    for h in running:
        rows.append(f"  RUN  {h.config_path.name}  t={now - h.started_at:.0f}s")
    for h in done:
        status = "OK" if h.returncode == 0 else f"FAIL({h.returncode})"
        rows.append(f"  DONE {h.config_path.name}  {status}")
    body = "\n".join(rows) if rows else "  (nothing to report)"
    print(
        f"[orchestrator] checkpoint t={elapsed:.0f}s ({len(running)} running, {len(done)} done)\n{body}",
        flush=True,
    )
