"""
dispatch_parallel.py — CPU-bound branch evaluation for Engine 1 Model E's
safety-stock full speculative check (CLAUDE.md §E.10).

Deciding whether to append a heavy-operation's safety-stock portion requires
forking the simulation state and running TWO full continuations of "the rest
of the schedule" — append vs. defer — then comparing whether either one
causes any committed order to breach its own CDD. Both continuations are pure
CPU-bound Python computation (no I/O), so real parallelism requires separate
PROCESSES, not threads: CPython's GIL serializes threads for CPU-bound work,
so a ThreadPoolExecutor here would add concurrency's complexity without
buying any actual speedup.

Windows constraint (this project's dev machine is Windows 11 Enterprise, and
its production target is Windows Server): `multiprocessing`'s default start
method there is "spawn", which pickles the target callable and its arguments
to hand them to a fresh worker process. Closures, lambdas, and bound methods
are NOT picklable under the standard `pickle` module — so every branch MUST
be a plain module-level function plus a tuple of picklable arguments. This
module enforces that shape via the `Branch` dataclass rather than accepting
an arbitrary callable, so a caller can't accidentally pass a closure that
works in a same-process unit test and then breaks the moment it actually runs
in parallel.

Practical corollary for whoever writes the final orchestrator: the "run the
rest of the schedule from this forked state" function passed in as `fn` must
itself be a top-level function, and everything it needs (forked machine-free
map, forked DevicePool via `DevicePool.clone()`, the remaining order queue,
etc.) must travel through `args` as plain, picklable data — no live Oracle
connections, no open file handles, no closures captured over outer-scope
state.

Persistent pool (CLAUDE.md "What's left" step 4): profiled on the live backlog,
97% of a full Engine 1 run (62s of 64s, 48 admission checks) was spent inside
these calls, and an EMPTY branch cost the same ~1.3s as a real one — i.e. the
cost was spawning two Windows worker processes and re-importing the dispatch
modules in them on every call, not the continuation compute itself. The pool
is therefore created once, lazily, and reused for the life of the process
(across checks AND across schedule runs); workers import the dispatch modules
once. `shutdown_pool()` is registered with atexit.
"""

from __future__ import annotations

import atexit
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Branch:
    """One speculative branch: a top-level function plus its (picklable) arguments."""

    name: str
    fn: Callable[..., Any]
    args: tuple = ()


_pool: Optional[ProcessPoolExecutor] = None
_pool_workers: Optional[int] = None
_pool_lock = threading.Lock()


def _exit_when_parent_dies() -> None:
    """Pool-worker initializer. A persistent worker has no reason to outlive its server: when the
    parent is force-killed (uvicorn --reload and service restarts do exactly that on Windows) idle
    workers would otherwise sit orphaned forever, holding memory and file locks — confirmed live,
    where four of them survived a killed test server. This thread blocks until the parent process
    exits, however it exits, then ends the worker. Module-level so it pickles under spawn."""
    import multiprocessing
    import os

    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def _watch() -> None:
        parent.join()
        os._exit(0)

    threading.Thread(target=_watch, name="exit-with-parent", daemon=True).start()


def _get_pool(max_workers: Optional[int]) -> ProcessPoolExecutor:
    """The shared pool, created on first use. An explicit `max_workers` that
    differs from the live pool's replaces it; `None` always reuses whatever exists."""
    global _pool, _pool_workers
    with _pool_lock:
        if _pool is not None and max_workers is not None and max_workers != _pool_workers:
            _pool.shutdown(wait=True)
            _pool = None
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=max_workers, initializer=_exit_when_parent_dies)
            _pool_workers = max_workers
        return _pool


def _discard_pool(pool: ProcessPoolExecutor) -> None:
    """Drop a broken pool so the next call builds a fresh one."""
    global _pool, _pool_workers
    with _pool_lock:
        if _pool is pool:
            _pool = None
            _pool_workers = None
    pool.shutdown(wait=False, cancel_futures=True)


def shutdown_pool() -> None:
    """Stop the shared pool (idempotent). Also runs automatically at interpreter exit."""
    global _pool, _pool_workers
    with _pool_lock:
        pool, _pool, _pool_workers = _pool, None, None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)


atexit.register(shutdown_pool)


# Upper bound on how long one set of branches may take. A normal admission check is
# well under a second; this exists so a wedged worker surfaces as an error instead of
# hanging the request (and the server thread) forever.
BRANCH_TIMEOUT_SECONDS = 600


def evaluate_branches_in_parallel(
    branches: list[Branch], max_workers: Optional[int] = None, timeout: Optional[float] = None
) -> dict[str, Any]:
    """
    Run every branch in its own worker process and return {branch.name: result}.

    Uses the module's persistent pool (see module docstring) — worker
    processes survive between calls, so only the first call pays process
    startup. `max_workers=None` reuses the live pool (or defaults to
    os.cpu_count() for a new one); a single admission decision submits just 2
    branches, so only 2 workers are ever actually spawned.

    If a branch raises, the exception is re-raised here (surfaced by
    `Future.result()`) rather than silently swallowed — a crashed speculative
    branch must fail the whole decision loudly, never be treated as "safe by
    default." If the POOL itself dies (a worker killed, e.g. out of memory),
    it is rebuilt and the branches re-run once — safe because branches are
    pure functions of their arguments; a second pool failure is raised. If the
    branches do not finish within `timeout` seconds (default BRANCH_TIMEOUT_SECONDS) the
    pool is discarded and TimeoutError is raised — never an unbounded wait.
    """
    if not branches:
        return {}

    for attempt in (1, 2):
        pool = _get_pool(max_workers)
        futures = {}
        try:
            futures = {branch.name: pool.submit(branch.fn, *branch.args) for branch in branches}
            deadline = time.monotonic() + (BRANCH_TIMEOUT_SECONDS if timeout is None else timeout)
            return {name: future.result(timeout=max(0.0, deadline - time.monotonic())) for name, future in futures.items()}
        except FuturesTimeoutError:
            _discard_pool(pool)  # a wedged worker must not poison the next caller
            raise TimeoutError(f"Speculative branches did not finish within the time limit ({branches[0].name}…)")
        except BrokenProcessPool:
            _discard_pool(pool)
            if attempt == 2:
                raise
        except BaseException:
            for future in futures.values():
                future.cancel()
            raise
    raise AssertionError("unreachable")
