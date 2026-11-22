"""Process management, virtualenv auto-detection, and stderr ring-buffering.

Provides:
- find_virtualenv_python: locates .venv/venv/.conda Python interpreter
- ProcessManager: thread-safe lifecycle management, SIGTERM->SIGKILL escalation,
  stderr ring-buffering (max 200 lines), and zombie state detection.
"""

from __future__ import annotations

import collections
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Global thread-safe state
_lock = threading.RLock()
_processes: dict[int, subprocess.Popen[str]] = {}
_logs: dict[int, collections.deque[str]] = {}
_proc_metadata: dict[int, dict[str, Any]] = {}
_log_line_counts: dict[int, int] = {}
_last_warned_tb: dict[int, tuple[int, str]] = {}


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


def _consume_stream(stream: Any, log_deque: collections.deque[str], pid: int | None = None) -> None:
    """Daemon thread worker consuming lines from process stdout/stderr."""
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            cleaned = line.rstrip("\r\n")
            if pid is not None:
                with _lock:
                    log_deque.append(cleaned)
                    _log_line_counts[pid] = _log_line_counts.get(pid, 0) + 1
            else:
                log_deque.append(cleaned)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


_ELECTRON_CHROMIUM_TOKENS = ("electron", "chromium", "chrome", "code")


def _is_chromium_like(cmd_head: str) -> bool:
    name = os.path.basename(cmd_head).lower()
    return any(tok in name for tok in _ELECTRON_CHROMIUM_TOKENS)


def launch(
    target_script: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Launches a GUI script, native binary, or $PATH command with accessibility env setup.

    Spawns process with stdout=PIPE, stderr=PIPE, runs daemon reader threads into a
    200-line ring buffer, and registers PID in _processes.
    """
    args = list(args or [])
    target_path = Path(target_script)
    resolved_local: Path | None = None
    if target_path.is_absolute() and target_path.exists():
        resolved_local = target_path
    elif cwd and (Path(cwd) / target_path).exists():
        resolved_local = (Path(cwd) / target_path).resolve()
    elif target_path.exists():
        resolved_local = target_path.resolve()
    else:
        repo_root = Path(__file__).resolve().parents[2]
        candidate = (repo_root / target_path).resolve()
        if candidate.exists():
            resolved_local = candidate

    which_bin = None if resolved_local is not None else shutil.which(target_script)

    if resolved_local is not None:
        if resolved_local.suffix == ".py" or not os.access(resolved_local, os.X_OK):
            python_bin = find_virtualenv_python(resolved_local)
            cmd = [str(python_bin), str(resolved_local), *args]
        else:
            cmd = [str(resolved_local), *args]
        work_dir = cwd or str(resolved_local.parent)
    elif which_bin:
        cmd = [which_bin, *args]
        work_dir = cwd or os.getcwd()
    else:
        script_path = target_path.resolve()
        if script_path.suffix == ".py" or not os.access(script_path, os.X_OK):
            python_bin = find_virtualenv_python(script_path)
            cmd = [str(python_bin), str(script_path), *args]
        else:
            cmd = [str(script_path), *args]
        work_dir = cwd or str(script_path.parent)

    if env is not None:
        proc_env = dict(env)
    else:
        from wayland_computer_use_mcp.core.context import get_active_context

        ctx = get_active_context()
        proc_env = ctx.build_subprocess_env() if ctx is not None else os.environ.copy()
    proc_env["PYTHONUNBUFFERED"] = "1"
    proc_env["GTK_MODULES"] = "gail:atk-bridge"
    proc_env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"
    proc_env.setdefault("ACCESSIBILITY_ENABLED", "1")
    proc_env.setdefault("ELECTRON_OZONE_PLATFORM_HINT", "auto")

    if _is_chromium_like(cmd[0]) or _is_chromium_like(target_script):
        if "--force-renderer-accessibility" not in cmd:
            cmd.append("--force-renderer-accessibility")
        if proc_env.get("WAYLAND_DISPLAY") and not any(
            a.startswith("--ozone-platform") for a in cmd
        ):
            cmd.append("--ozone-platform=wayland")

    proc = subprocess.Popen(
        cmd,
        cwd=work_dir,
        env=proc_env,
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
        _log_line_counts[pid] = 0
        _last_warned_tb.pop(pid, None)
        _proc_metadata[pid] = {
            "target_script": target_script,
            "target": target_script,
            "args": list(args),
            "cwd": cwd,
            "cmd": cmd,
            "env": proc_env,
        }

    # Start reader threads for both stdout and stderr so neither pipe can deadlock
    for stream_name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        if stream is not None:
            thread = threading.Thread(
                target=_consume_stream,
                args=(stream, log_deque, pid),
                daemon=True,
                name=f"{stream_name}-reader-{pid}",
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
            if pid in _proc_metadata:
                return True
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
        _last_warned_tb.pop(pid, None)
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


def _get_traceback_with_sig(pid: int) -> tuple[str | None, tuple[int, str] | None]:
    """Returns (traceback_text, (abs_start_line, exception_header)) for deduplication."""
    with _lock:
        if pid not in _logs:
            return None, None
        entries = list(_logs[pid])
        total_lines = max(_log_line_counts.get(pid, 0), len(entries))

    tb_idx = -1
    for i in range(len(entries) - 1, -1, -1):
        if "Traceback (most recent call last):" in entries[i]:
            tb_idx = i
            break
    if tb_idx < 0:
        return None, None

    tb_lines = entries[tb_idx:]
    tb_text = "\n".join(tb_lines).strip()
    abs_start = total_lines - len(entries) + tb_idx
    exc_line = ""
    for line in tb_lines[1:]:
        if line and not line.startswith((" ", "\t")):
            exc_line = line
            break
    return tb_text, (abs_start, exc_line)


def get_recent_traceback(pid: int) -> str | None:
    """Scans the ring buffer for standard Python traceback patterns."""
    tb, _ = _get_traceback_with_sig(pid)
    return tb


def has_unreported_traceback(pid: int) -> bool:
    """Returns True if PID has a stderr traceback not yet surfaced by check_process_health."""
    tb, sig = _get_traceback_with_sig(pid)
    if not tb or sig is None:
        return False
    with _lock:
        return _last_warned_tb.get(pid) != sig


def check_process_health_and_enrich(pid: int | None, result_text: Any) -> Any:
    """Checks process health after an action and attaches traceback if detected.

    - If the process terminated/crashed, raises RuntimeError with the formatted traceback.
    - If the process is alive and emitted a new traceback to stderr, attaches a warning once.
    """
    if not pid:
        return result_text

    with _lock:
        is_managed = pid in _processes

    if not is_managed:
        return result_text

    alive = is_responsive(pid)
    if not alive:
        tb = get_recent_traceback(pid)
        if not tb:
            for _ in range(5):
                time.sleep(0.04)
                tb = get_recent_traceback(pid)
                if tb:
                    break
        if tb:
            raise RuntimeError(f"Application process (PID {pid}) crashed!\n\nTraceback:\n{tb}")
        raise RuntimeError(f"Application process (PID {pid}) unexpectedly terminated or exited.")

    tb, sig = _get_traceback_with_sig(pid)
    if not tb or sig is None:
        return result_text

    # Avoid repeating an already-surfaced traceback on subsequent healthy actions
    with _lock:
        prev_sig = _last_warned_tb.get(pid)
    if prev_sig == sig:
        return result_text

    # If the traceback header just arrived mid-flush, allow one short read settle
    if len(tb.splitlines()) < 3:
        time.sleep(0.04)
        tb, sig = _get_traceback_with_sig(pid)
        if not tb or sig is None:
            return result_text

    with _lock:
        _last_warned_tb[pid] = sig

    warning_msg = (
        f"> [!WARNING]\n> Application emitted a Python exception to stderr:\n```python\n{tb}\n```"
    )
    if isinstance(result_text, dict):
        result_text["warning"] = warning_msg
        return result_text
    return f"{result_text}\n\n{warning_msg}"


def prune_dead_processes() -> list[int]:
    """Scans tracked processes, removes any that have exited, and returns pruned PIDs."""
    pruned: list[int] = []
    with _lock:
        for pid, proc in list(_processes.items()):
            if proc.poll() is not None or not is_responsive(pid):
                _processes.pop(pid, None)
                pruned.append(pid)
    return pruned


def list_active_processes() -> list[dict[str, Any]]:
    """Returns metadata for all currently active managed application processes."""
    prune_dead_processes()
    results: list[dict[str, Any]] = []
    with _lock:
        for pid in list(_processes.keys()):
            meta = _proc_metadata.get(pid, {})
            results.append(
                {
                    "pid": pid,
                    "target": meta.get("target_script", "unknown"),
                    "args": meta.get("args", []),
                    "responsive": is_responsive(pid),
                }
            )
    return results


def terminate_all_processes() -> list[int]:
    """Terminates all currently managed child processes cleanly.

    Invoked during server shutdown or cleanup.
    """
    with _lock:
        pids = list(_processes.keys())

    terminated: list[int] = []
    for pid in pids:
        try:
            terminate(pid)
            terminated.append(pid)
        except Exception:
            pass
    return terminated
