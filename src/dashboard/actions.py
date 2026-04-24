"""Subprocess-based job runner. Only one job runs at a time.

We launch `scripts/run_daily.py` or `scripts/run_analysis.py` in a child process,
tee stdout+stderr to a log file, and track status in a small JSON state file so
the dashboard can show "in progress / completed / failed" across page loads.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


JOB_TYPES = {"daily", "dry_run", "analysis"}
STATE_FILENAME = "current_job.json"


@dataclass
class JobState:
    kind: str
    pid: int
    log_path: str
    started_at: str
    ended_at: str | None = None
    returncode: int | None = None
    args: list[str] | None = None


def _state_path(logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / STATE_FILENAME


def load_state(logs_dir: Path) -> JobState | None:
    p = _state_path(logs_dir)
    if not p.exists():
        return None
    try:
        return JobState(**json.loads(p.read_text()))
    except (json.JSONDecodeError, TypeError):
        return None


def save_state(logs_dir: Path, state: JobState | None) -> None:
    p = _state_path(logs_dir)
    if state is None:
        if p.exists():
            p.unlink()
        return
    p.write_text(json.dumps(asdict(state), indent=2))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def refresh_state(logs_dir: Path) -> JobState | None:
    """Mark a finished job as finished by reading its return code if possible."""
    state = load_state(logs_dir)
    if state is None:
        return None
    if state.ended_at:
        return state
    if not _pid_alive(state.pid):
        state.ended_at = datetime.now(timezone.utc).isoformat()
        # No reliable way to recover returncode of an orphaned child; mark -1.
        state.returncode = -1
        save_state(logs_dir, state)
    return state


def is_running(logs_dir: Path) -> bool:
    state = refresh_state(logs_dir)
    return state is not None and state.ended_at is None


def launch(
    kind: str,
    repo_root: Path,
    logs_dir: Path,
    *,
    extra_args: list[str] | None = None,
) -> JobState:
    if kind not in JOB_TYPES:
        raise ValueError(f"Unknown job kind: {kind}")
    if is_running(logs_dir):
        raise RuntimeError("Another job is already running.")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = logs_dir / f"web-{kind}-{ts}.log"

    if kind == "daily":
        argv = [sys.executable, str(repo_root / "scripts" / "run_daily.py")]
    elif kind == "dry_run":
        argv = [sys.executable, str(repo_root / "scripts" / "run_daily.py"), "--dry-run"]
    elif kind == "analysis":
        argv = [sys.executable, str(repo_root / "scripts" / "run_analysis.py")]
    else:
        raise ValueError(kind)
    if extra_args:
        argv.extend(extra_args)

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "wb", buffering=0)
    proc = subprocess.Popen(
        argv,
        cwd=str(repo_root),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=os.environ.copy(),
    )
    state = JobState(
        kind=kind,
        pid=proc.pid,
        log_path=str(log_path),
        started_at=datetime.now(timezone.utc).isoformat(),
        args=argv,
    )
    save_state(logs_dir, state)
    return state


def cancel(logs_dir: Path) -> bool:
    state = load_state(logs_dir)
    if state is None or state.ended_at is not None:
        return False
    try:
        os.killpg(os.getpgid(state.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    # Give it a moment to die, then re-check.
    time.sleep(0.5)
    refresh_state(logs_dir)
    return True


def tail_log(log_path: Path, max_bytes: int = 32 * 1024) -> str:
    if not log_path.exists():
        return ""
    size = log_path.stat().st_size
    start = max(0, size - max_bytes)
    with open(log_path, "rb") as f:
        f.seek(start)
        return f.read().decode(errors="replace")


def clear_state(logs_dir: Path) -> None:
    save_state(logs_dir, None)
