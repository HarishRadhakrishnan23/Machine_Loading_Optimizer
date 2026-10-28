"""
test_dispatch_parallel.py — parallel branch evaluation harness for the §E.10
full speculative check.

Deliberately uses real module-level functions (never closures/lambdas) as
branches, proving the exact shape the eventual "append vs. defer" simulation
continuations must take to survive Windows' spawn-based multiprocessing.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_parallel.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from concurrent.futures.process import BrokenProcessPool

import dispatch_parallel
from dispatch_parallel import Branch, evaluate_branches_in_parallel, shutdown_pool


# Must be module-level (picklable by reference) — this is the real-world
# shape a "run the rest of the schedule from this forked state" continuation
# will need in the final orchestrator.
def _double(x: int) -> int:
    return x * 2


def _explode() -> None:
    raise RuntimeError("branch failed")


def _pid() -> int:
    return os.getpid()


def _crash_once(marker: str) -> int:
    """Kills its own worker process the first time it runs (simulates a worker
    dying mid-branch), then behaves normally on the retry."""
    if not os.path.exists(marker):
        open(marker, "w").close()
        os._exit(1)
    return 7


def _sleep(seconds: float) -> float:
    import time

    time.sleep(seconds)
    return seconds


def _crash_always() -> None:
    os._exit(1)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def test_runs_branches_in_separate_processes():
    print("\n=== Two branches, run concurrently, results keyed by name ===")
    branches = [Branch("append", _double, (21,)), Branch("defer", _double, (10,))]
    results = evaluate_branches_in_parallel(branches)
    check("append == 42", results["append"] == 42)
    check("defer == 20", results["defer"] == 20)


def test_exception_in_one_branch_propagates():
    print("\n=== A crashed branch fails loudly, never treated as 'safe by default' ===")
    branches = [Branch("ok", _double, (1,)), Branch("bad", _explode, ())]
    try:
        evaluate_branches_in_parallel(branches)
        raised = False
    except RuntimeError:
        raised = True
    check("RuntimeError propagated from the worker process", raised)


def test_empty_branch_list_returns_empty_dict():
    print("\n=== No branches: no-op ===")
    check("empty in -> empty out", evaluate_branches_in_parallel([]) == {})


def test_pool_is_persistent_across_calls():
    print("\n=== Worker processes survive between calls (no per-call pool startup) ===")
    first = evaluate_branches_in_parallel([Branch("a", _pid), Branch("b", _pid)])
    second = evaluate_branches_in_parallel([Branch("a", _pid), Branch("b", _pid)])
    check("workers are not the test process", os.getpid() not in {*first.values(), *second.values()})
    check("second call reuses the first call's worker processes", set(second.values()) <= set(first.values()) | set(second.values()) and set(second.values()) & set(first.values()))


def test_pool_recovers_from_a_dead_worker():
    print("\n=== A worker dying mid-branch rebuilds the pool and retries once ===")
    marker = os.path.join(tempfile.mkdtemp(), "crashed_once")
    results = evaluate_branches_in_parallel([Branch("x", _crash_once, (marker,))])
    check("retry on a fresh pool returned the real result", results == {"x": 7})
    check("pool still usable afterwards", evaluate_branches_in_parallel([Branch("y", _double, (4,))]) == {"y": 8})


def test_persistent_failure_is_raised_not_swallowed():
    print("\n=== A branch that always kills its worker fails loudly after one retry ===")
    try:
        evaluate_branches_in_parallel([Branch("z", _crash_always)])
        raised = False
    except BrokenProcessPool:
        raised = True
    check("BrokenProcessPool raised on the second failure", raised)
    check("pool recovers for the next caller", evaluate_branches_in_parallel([Branch("y", _double, (1,))]) == {"y": 2})


def test_wedged_branch_times_out_instead_of_hanging():
    print("\n=== A branch that never finishes raises TimeoutError and the pool recovers ===")
    try:
        evaluate_branches_in_parallel([Branch("slow", _sleep, (30,))], timeout=1)
        raised = False
    except TimeoutError:
        raised = True
    check("TimeoutError raised within the limit", raised)
    check("pool recovers for the next caller", evaluate_branches_in_parallel([Branch("y", _double, (5,))]) == {"y": 10})


def _pid_alive(pid: int) -> bool:
    import subprocess

    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def test_workers_exit_when_the_parent_is_hard_killed():
    print("\n=== Workers must not be orphaned when the server process is force-killed ===")
    import subprocess
    import textwrap
    import time

    if os.name != "nt":
        print("  [SKIP] Windows-only check (tasklist)")
        return
    backend = str(Path(__file__).parent.parent)
    script = Path(tempfile.mkdtemp()) / "parent_with_pool.py"
    script.write_text(textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {backend!r})
        from dispatch_parallel import Branch, evaluate_branches_in_parallel
        if __name__ == "__main__":
            r = evaluate_branches_in_parallel([Branch("a", os.getpid), Branch("b", os.getpid)])
            print(" ".join(str(v) for v in r.values()), flush=True)
            time.sleep(120)
    """))
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    worker_pids = [int(x) for x in parent.stdout.readline().split()]
    check("pool spawned 2 workers", len(worker_pids) == 2 and all(_pid_alive(p) for p in worker_pids))
    parent.kill()  # TerminateProcess: no atexit, no cleanup — what a reload / service restart does
    parent.wait()
    deadline = time.time() + 15
    while time.time() < deadline and any(_pid_alive(p) for p in worker_pids):
        time.sleep(0.5)
    still = [p for p in worker_pids if _pid_alive(p)]
    for p in still:  # never leave strays behind if this assertion fails
        subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True)
    check("both workers exited on their own after the parent was killed", not still)


def test_shutdown_is_idempotent_and_pool_restarts():
    print("\n=== shutdown_pool() is safe to repeat; next call builds a new pool ===")
    shutdown_pool()
    shutdown_pool()
    check("pool reference cleared", dispatch_parallel._pool is None)
    check("works again after shutdown", evaluate_branches_in_parallel([Branch("a", _double, (3,))]) == {"a": 6})


if __name__ == "__main__":
    test_runs_branches_in_separate_processes()
    test_exception_in_one_branch_propagates()
    test_empty_branch_list_returns_empty_dict()
    test_pool_is_persistent_across_calls()
    test_pool_recovers_from_a_dead_worker()
    test_persistent_failure_is_raised_not_swallowed()
    test_wedged_branch_times_out_instead_of_hanging()
    test_workers_exit_when_the_parent_is_hard_killed()
    test_shutdown_is_idempotent_and_pool_restarts()
    print("\n[OK] Parallel branch evaluation behaves per CLAUDE.md §E.10's speculative-check requirement.")
