"""
engine2_live_parity.py — LIVE tripwire: Engine 2's baseline must equal what Engine 1's real
pipeline produces from the same data.

Runs `model_f_pipeline.generate_schedule` (Engine 1's actual code path) with its two Oracle
WRITE calls stubbed out — nothing is written — and compares every output row (machine,
start, end, quantity, REMARK) with `engine2.runner.simulate`. Run it after ANY change to
Engine 1's simulator or pipeline; it fails if Engine 2's mirrored call has drifted.

Run: backend/venv/Scripts/python.exe backend/testing/engine2_live_parity.py   (read-only)
"""

import sys
import warnings
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
warnings.filterwarnings("ignore")

import model_f_pipeline as mfp
from engine2 import metrics
from engine2.runner import simulate
from engine2.snapshot import load_config_dict, load_snapshot
from shift_clock import timepoint_to_datetime as to_dt

captured: dict = {}
mfp.delete_schedule_output = lambda: 0  # NO Oracle writes — both write calls are stubbed
mfp.write_schedule_output = lambda rows: captured.setdefault("rows", rows) and len(rows)


def main() -> int:
    run_date = date.today()
    mfp.generate_schedule(load_config_dict(), run_date)  # Engine 1's real code path
    engine1 = captured["rows"]
    engine2 = simulate(load_snapshot(run_date))

    k1 = {
        (r["PRODUCTION_ORDER"], r["OPERATION_NO"], r["LINE_NO"]): (
            r["WORK_CENTER"], r["START_TIMESTAMP"], r["END_TIMESTAMP"], r["BALANCE_QTY"], r["REMARK"])
        for r in engine1
    }
    k2 = {
        (r.production_order, r.operation_no, r.line_no): (
            r.machine, to_dt(r.start) if r.start else None, to_dt(r.end) if r.end else None, r.balance_qty, r.remark)
        for r in engine2
    }
    differing = [k for k in k1 if k in k2 and k1[k] != k2[k]]
    c1 = {o: (d.date() if hasattr(d, "date") else d) for o, d in
          ((r["PRODUCTION_ORDER"], r["ORDER_COMPLETION_DATE"]) for r in engine1) if d is not None}
    ok = set(k1) == set(k2) and not differing and c1 == metrics.completion_by_order(engine2)
    print(f"Engine 1 rows: {len(engine1)}  Engine 2 rows: {len(engine2)}  differing rows: {len(differing)}")
    print("PARITY:", "EXACT" if ok else "DIFFERS — Engine 2's runner has drifted from model_f_pipeline")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
