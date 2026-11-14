"""Process-handling tests for the orchestrator (no DB, no model).

Cells are replaced by stand-in `python -c` children via the `_cell_argv` seam.
"""
import os
import sys
import time
from pathlib import Path

import pytest

from chunkshop import orchestrator as orch


@pytest.fixture(autouse=True)
def _reap_sleepers(tmp_path):
    yield
    pf = tmp_path / "pid"
    if pf.exists() and pf.read_text():
        try:
            os.kill(int(pf.read_text()), 9)
        except ProcessLookupError:
            pass


def _use_child(monkeypatch, scripts: dict[str, str]):
    """Map config file name -> python source run as the child."""
    def argv(config_path: Path):
        return [sys.executable, "-c", scripts[Path(config_path).name]]
    monkeypatch.setattr(orch, "_cell_argv", argv)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # zombie counts as dead for our purposes
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _sleeper_script(pidfile: Path) -> str:
    return (
        "import os,time\n"
        f"open({str(pidfile)!r},'w').write(str(os.getpid()))\n"
        "time.sleep(300)\n"
    )


def _read_pid(pidfile: Path) -> int:
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            return int(pidfile.read_text())
        time.sleep(0.05)
    raise AssertionError("child never wrote pid")


def test_a_large_output_does_not_deadlock(monkeypatch, tmp_path):
    _use_child(monkeypatch, {"big.yaml": "print('x' * 200000)"})
    t0 = time.time()
    res = orch.orchestrate([tmp_path / "big.yaml"], concurrency=1, overall_timeout_seconds=15)
    assert time.time() - t0 < 10
    assert (res.total, res.succeeded, res.failed) == (1, 1, 0)


def test_b_failure_reports_output_tail(monkeypatch, tmp_path):
    _use_child(monkeypatch, {
        "bad.yaml": "import sys; print('noise'*5000); print('BOOM reason here', file=sys.stderr); sys.exit(3)"
    })
    res = orch.orchestrate([tmp_path / "bad.yaml"], concurrency=1, overall_timeout_seconds=15)
    assert res.failed == 1 and res.succeeded == 0
    cell = res.cells[0]
    assert cell["rc"] == 3
    assert "BOOM reason here" in cell["output_tail"]
    assert len(cell["output_tail"]) <= 4096


def test_c_timeout_kills_child_and_counts_pending(monkeypatch, tmp_path):
    pidfile = tmp_path / "pid"
    _use_child(monkeypatch, {
        "sleep.yaml": _sleeper_script(pidfile),
        "never.yaml": "print('should not run')",
    })
    monkeypatch.setattr(orch, "_TERM_GRACE_SECONDS", 2, raising=False)
    res = orch.orchestrate(
        [tmp_path / "sleep.yaml", tmp_path / "never.yaml"],
        concurrency=1, overall_timeout_seconds=1,
    )
    pid = _read_pid(pidfile)
    assert not _alive(pid)
    assert res.total == 2
    assert res.failed == 2 and res.succeeded == 0
    assert len(res.cells) == 2
    never = [c for c in res.cells if c["config"].endswith("never.yaml")][0]
    assert never["status"] == "not_started"


def test_d_concurrency_zero_raises(tmp_path):
    with pytest.raises(ValueError, match="concurrency must be >= 1"):
        orch.orchestrate([tmp_path / "x.yaml"], concurrency=0, overall_timeout_seconds=1)
    with pytest.raises(ValueError):
        orch.orchestrate([tmp_path / "x.yaml"], concurrency=-2, overall_timeout_seconds=1)


def test_e_interrupt_kills_children_and_propagates(monkeypatch, tmp_path):
    pidfile = tmp_path / "pid"
    _use_child(monkeypatch, {"sleep.yaml": _sleeper_script(pidfile)})
    monkeypatch.setattr(orch, "_TERM_GRACE_SECONDS", 2, raising=False)
    real_sleep = time.sleep
    state = {"n": 0}

    def fake_sleep(s):
        state["n"] += 1
        if state["n"] == 1:
            # let the child write its pid before we "press Ctrl-C"
            _read_pid(pidfile)
            raise KeyboardInterrupt
        real_sleep(s)

    monkeypatch.setattr(orch.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        orch.orchestrate([tmp_path / "sleep.yaml"], concurrency=1, overall_timeout_seconds=60)
    assert not _alive(_read_pid(pidfile))
