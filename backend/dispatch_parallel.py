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
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Branch:
    """One speculative branch: a top-level function plus its (picklable) arguments."""

    name: str
    fn: Callable[..., Any]
    args: tuple = ()


def evaluate_branches_in_parallel(branches: list[Branch], max_workers: Optional[int] = None) -> dict[str, Any]:
    """
    Run every branch in its own worker process and return {branch.name: result}.

    `max_workers=None` lets ProcessPoolExecutor default to os.cpu_count() —
    for a single safety-stock decision this is normally just 2 branches
    ("append" vs. "defer"), so the default is already right; the parameter
    exists for a caller batching several independent decisions' branches
    into one pool to avoid repeated pool-startup overhead.

    If a branch raises, the exception is re-raised here (surfaced by
    `Future.result()`) rather than silently swallowed — a crashed speculative
    branch must fail the whole decision loudly, never be treated as "safe by
    default."
    """
    if not branches:
        return {}

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {branch.name: pool.submit(branch.fn, *branch.args) for branch in branches}
        return {name: future.result() for name, future in futures.items()}
