"""Process management, virtualenv auto-detection, and stderr ring-buffering.

Provides:
- find_virtualenv_python: locates .venv/venv/.conda Python interpreter
- ProcessManager: thread-safe lifecycle management, SIGTERM->SIGKILL escalation,
  stderr ring-buffering (max 200 lines), and zombie state detection.
"""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Global thread-safe state
_lock = threading.Lock()
_processes: dict[int, subprocess.Popen[str]] = {}
_logs: dict[int, collections.deque[str]] = {}
_proc_metadata: dict[int, dict[str, Any]] = {}


def find_virtualenv_python(target_path: Path | str) -> Path:
    """Walks up the directory tree starting from target_path.resolve().parent.

    Checks for .venv/bin/python, venv/bin/python, or .conda/bin/python.
    Returns the path to the virtualenv interpreter if executable,
    otherwise returns Path(sys.executable).
    """
    path = Path(target_path).resolve()
    current = path.parent if path.is_file() or path.suffix else path

    candidate_subpaths = [
        Path(".venv/bin/python"),
        Path("venv/bin/python"),
        Path(".conda/bin/python"),
    ]

    for parent in [current, *current.parents]:
        for candidate in candidate_subpaths:
            interpreter = parent / candidate
            if interpreter.is_file() and os.access(interpreter, os.X_OK):
                return interpreter

    return Path(sys.executable)


def _consume_stream(stream: Any, log_deque: collections.deque[str]) -> None:
    """Daemon thread worker consuming lines from process stderr."""
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            log_deque.append(line.rstrip("\r\n"))
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def launch(target_script: str, args: list[str] | None = None, cwd: str | None = None) -> int:
    """Launches a script/application using auto-detected Python runtime.

    Spawns process with stdout=PIPE, stderr=PIPE, runs a background reader thread
    for stderr into a ring buffer (max 200 lines), and registers PID in _processes.
    """
    args = args or []
    target_path = Path(target_script)
    if target_path.is_absolute() and target_path.exists():
        script_path = target_path
    elif cwd and (Path(cwd) / target_path).exists():
        script_path = (Path(cwd) / target_path).resolve()
    elif target_path.exists():
        script_path = target_path.resolve()
    else:
        # Fallback to repo/project root
        repo_root = Path(__file__).resolve().parents[2]
        candidate = (repo_root / target_path).resolve()
        if candidate.exists():
            script_path = candidate
        else:
            script_path = target_path.resolve()

    if script_path.suffix == ".py" or not os.access(script_path, os.X_OK):
        python_bin = find_virtualenv_python(script_path)
        cmd = [str(python_bin), str(script_path), *args]
    else:
        cmd = [str(script_path), *args]

    work_dir = cwd or str(script_path.parent)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["GTK_MODULES"] = "gail:atk-bridge"
    env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=work_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    pid = proc.pid
    log_deque: collections.deque[str] = collections.deque(maxlen=200)

    with _lock:
        _processes[pid] = proc
        _logs[pid] = log_deque
        _proc_metadata[pid] = {
            "target_script": target_script,
            "args": list(args),
            "cwd": cwd,
            "cmd": cmd,
            "env": env,
        }

    # Start stderr consumer thread
    thread = threading.Thread(
        target=_consume_stream,
        args=(proc.stderr, log_deque),
        daemon=True,
        name=f"stderr-reader-{pid}",
    )
    thread.start()

    return pid


def terminate(pid: int) -> bool:
    """Safely terminates an application process owned by this session.

    Sends SIGTERM, waits up to 2 seconds via poll(), escalates to SIGKILL if still running.
    Cleans up registry entries.
    Raises PermissionError if PID is not owned by this session.
    """
    with _lock:
        if pid not in _processes:
            raise PermissionError(f"PID {pid} not owned by this session.")
        proc = _processes[pid]

    # Send SIGTERM
    try:
        proc.terminate()
    except ProcessLookupError:
        pass

    # Wait up to 2.0 seconds
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.05)

    # Escalate to SIGKILL if still running
    if proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass

    with _lock:
        _processes.pop(pid, None)
        # Note: retain logs temporarily so client can inspect crash info if needed

    return True


def restart(pid: int) -> int:
    """Hot-restarts a managed application while preserving its command arguments."""
    with _lock:
        if pid not in _processes:
            raise PermissionError(f"PID {pid} not owned by this session.")
        meta = _proc_metadata.get(pid)
        if not meta:
            raise RuntimeError(f"Metadata for PID {pid} not found.")

    target_script = meta["target_script"]
    args = meta["args"]
    cwd = meta["cwd"]

    terminate(pid)
    new_pid = launch(target_script, args, cwd)
    return new_pid


def get_logs(pid: int, lines: int = 50) -> str:
    """Retrieves the most recent lines from _logs[pid] joined as a single string."""
    with _lock:
        if pid not in _logs:
            return ""
        entries = list(_logs[pid])

    count = max(0, lines)
    recent = entries[-count:] if count > 0 else []
    return "\n".join(recent)


def is_responsive(pid: int) -> bool:
    """Checks if a managed process is active, responding, or in a zombie/crashed state.

    Checks if proc.poll() is None and inspects /proc/<pid>/status for zombie state ('Z').
    """
    with _lock:
        proc = _processes.get(pid)

    if proc is None:
        return False

    if proc.poll() is not None:
        return False

    status_file = Path(f"/proc/{pid}/status")
    if status_file.is_file():
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("State:"):
                        state_code = line.split()[1] if len(line.split()) > 1 else ""
                        if "Z" in state_code:  # Zombie
                            return False
                        break
        except (OSError, IndexError):
            pass

    return True


def get_recent_traceback(pid: int) -> str | None:
    """Scans the ring buffer for standard Python traceback patterns."""
    with _lock:
        if pid not in _logs:
            return None
        entries = list(_logs[pid])

    full_text = "\n".join(entries)
    if "Traceback (most recent call last):" in full_text:
        idx = full_text.rfind("Traceback (most recent call last):")
        return full_text[idx:].strip()
    return None


def check_process_health_and_enrich(pid: int | None, result_text: str) -> str:
    """Checks process health after an action and attaches traceback if detected.

    - If the process terminated/crashed, raises RuntimeError with the formatted traceback.
    - If the process is alive but emitted a traceback to stderr, appends an alert block.
    """
    if not pid:
        return result_text

    # Only check if PID is managed
    with _lock:
        is_managed = pid in _processes

    if not is_managed:
        return result_text

    alive = is_responsive(pid)
    tb = get_recent_traceback(pid)
    if not tb:
        for _ in range(5):
            time.sleep(0.04)
            tb = get_recent_traceback(pid)
            if tb:
                break

    if not alive:
        if tb:
            raise RuntimeError(f"Application process (PID {pid}) crashed!\n\nTraceback:\n{tb}")
        raise RuntimeError(f"Application process (PID {pid}) unexpectedly terminated or exited.")

    if tb:
        return (
            f"{result_text}\n\n"
            f"> [!WARNING]\n"
            f"> Application emitted a Python exception to stderr:\n"
            f"> ```python\n"
            f"> {tb}\n"
            f"> ```"
        )

    return result_text
