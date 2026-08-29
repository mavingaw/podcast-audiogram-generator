"""Cancelling work that is already under way.

A queue you cannot stop is a nuisance once several renders run at once: a
mistyped clip range ties up a lane for a minute and there is nothing to do but
wait for it. Marking a row `canceled` is not enough either — the FFmpeg process
carries on regardless, finishes, and writes its output.

So cancellation has two halves. A flag the job checks between steps, and a
handle on the child process so a render already inside FFmpeg dies promptly
rather than at the next checkpoint that may never come.
"""

from __future__ import annotations

import subprocess
import threading


class JobCancelled(RuntimeError):
    """Raised inside a worker when its job has been cancelled."""


_lock = threading.Lock()
_requested: set[str] = set()
_processes: dict[str, subprocess.Popen] = {}


def request(job_id: str) -> None:
    """Ask a job to stop, and kill its child process if it has one."""
    with _lock:
        _requested.add(job_id)
        process = _processes.get(job_id)
    if process and process.poll() is None:
        # Terminate first so FFmpeg can close its output file; kill if it
        # ignores that. A half-written MP4 in the scratch directory is
        # discarded either way.
        try:
            process.terminate()
        except OSError:
            pass


def is_requested(job_id: str) -> bool:
    with _lock:
        return job_id in _requested


def raise_if_cancelled(job_id: str) -> None:
    if is_requested(job_id):
        raise JobCancelled("Cancelled")


def register(job_id: str, process: subprocess.Popen) -> None:
    """Track a child process so a cancel can reach it.

    A cancel that arrived while the process was starting still applies, so the
    flag is re-checked here rather than assumed to be clear.
    """
    with _lock:
        _processes[job_id] = process
        already = job_id in _requested
    if already and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass


def release(job_id: str) -> None:
    with _lock:
        _processes.pop(job_id, None)


def clear(job_id: str) -> None:
    """Forget a job entirely, once it has finished one way or another."""
    with _lock:
        _requested.discard(job_id)
        _processes.pop(job_id, None)


def run(job_id: str | None, args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run`, but interruptible by :func:`request`.

    Used for every long child process a job spawns, so cancelling reaches the
    encoder rather than waiting for it.
    """
    if job_id is None:
        return subprocess.run(args, **kwargs)

    raise_if_cancelled(job_id)
    timeout = kwargs.pop("timeout", None)
    capture = kwargs.pop("capture_output", False)
    if capture:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)

    process = subprocess.Popen(args, **kwargs)
    register(job_id, process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    finally:
        release(job_id)

    if is_requested(job_id):
        raise JobCancelled("Cancelled")
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
