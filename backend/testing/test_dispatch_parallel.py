"""
test_dispatch_parallel.py — parallel branch evaluation harness for the §E.10
full speculative check.

Deliberately uses real module-level functions (never closures/lambdas) as
branches, proving the exact shape the eventual "append vs. defer" simulation
continuations must take to survive Windows' spawn-based multiprocessing.

Run: backend/venv/Scripts/python.exe backend/testing/test_dispatch_parallel.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatch_parallel import Branch, evaluate_branches_in_parallel


# Must be module-level (picklable by reference) — this is the real-world
# shape a "run the rest of the schedule from this forked state" continuation
# will need in the final orchestrator.
def _double(x: int) -> int:
    return x * 2


def _explode() -> None:
    raise RuntimeError("branch failed")


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


if __name__ == "__main__":
    test_runs_branches_in_separate_processes()
    test_exception_in_one_branch_propagates()
    test_empty_branch_list_returns_empty_dict()
    print("\n[OK] Parallel branch evaluation behaves per CLAUDE.md §E.10's speculative-check requirement.")
