#!/usr/bin/env python3
"""
Model D Phase 1 — Vocabulary audit between MCH_WIP and MCH_ITEMWISE_FIXTURE_LOCATOR
(and MCH_MACHINE_PRIORITY where relevant), across every join dimension:
SIZE_INCH, CLASS, DESIGN, TASK, WORK_CENTER.

Finds:
  - Exact mismatches (value in one table, absent from the other)
  - Case-only mismatches (same value, different case — trivially normalizable)
  - Values present in WIP but with NO analogue at all in fl_master (real gaps)

Read-only. No changes made.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import read_wip_orders, read_routing_master, read_fixture_locator_master

wip = read_wip_orders()
routing = read_routing_master()
fl = read_fixture_locator_master()

wip["balance_qty"] = wip["QUANTITY_ORDERED"] - wip["QUANTITY_COMPLETED"] - wip["QUANTITY_REJECTED"].fillna(0)
active = wip[(wip["CYCLE_TIME"] > 0) & (wip["balance_qty"] > 0)].copy()

print("=" * 90)
print("MODEL D PHASE 1 — VOCABULARY AUDIT (WIP vs MCH_ITEMWISE_FIXTURE_LOCATOR vs ROUTING)")
print("=" * 90)


def audit_column(col_name, wip_series, fl_series, routing_series=None):
    print(f"\n{'-'*90}")
    print(f"COLUMN: {col_name}")
    print(f"{'-'*90}")

    wip_vals = set(wip_series.dropna().astype(str).unique())
    fl_vals = set(fl_series.dropna().astype(str).unique())

    print(f"  WIP values:       {sorted(wip_vals)}")
    print(f"  fl_master values: {sorted(fl_vals)}")
    if routing_series is not None:
        routing_vals = set(routing_series.dropna().astype(str).unique())
        print(f"  routing values:   {sorted(routing_vals)}")

    # Case-insensitive matching to separate "case-only" from "genuinely missing"
    fl_lower_map = {}
    for v in fl_vals:
        fl_lower_map.setdefault(v.lower(), []).append(v)

    exact_match = wip_vals & fl_vals
    case_only_diff = set()
    genuinely_missing = set()

    for v in wip_vals - fl_vals:
        if v.lower() in fl_lower_map:
            case_only_diff.add((v, tuple(fl_lower_map[v.lower()])))
        else:
            genuinely_missing.add(v)

    print(f"\n  [OK] Exact matches (WIP value found as-is in fl_master): {sorted(exact_match)}")

    if case_only_diff:
        print(f"\n  [CASE MISMATCH] WIP value vs fl_master value (same ignoring case):")
        for wip_v, fl_vs in sorted(case_only_diff):
            print(f"    WIP='{wip_v}'  <->  fl_master={list(fl_vs)}")
    else:
        print(f"\n  [OK] No case-only mismatches.")

    if genuinely_missing:
        print(f"\n  [GENUINE GAP] WIP value has NO analogue (exact or case-insensitive) in fl_master:")
        for v in sorted(genuinely_missing):
            affected = active[active[col_name] == v]
            n_orders = affected["PRODUCTION_ORDER"].nunique()
            n_rows = len(affected)
            print(f"    '{v}': {n_orders} distinct orders, {n_rows} active/balance>0 rows affected")
    else:
        print(f"\n  [OK] No genuine gaps — every WIP value has a case-insensitive match in fl_master.")

    return exact_match, case_only_diff, genuinely_missing


# ── Each join dimension ────────────────────────────────────────────────────────
audit_column("DESIGN", active["DESIGN"], fl["DESIGN"], routing["DESIGN"])
audit_column("CLASS", active["CLASS"], fl["CLASS"], routing["CLASS"])
audit_column("SIZE_INCH", active["SIZE_INCH"], fl["SIZE_INCH"])
audit_column("TASK", active["TASK"], fl["TASK"], routing["TASK"])
audit_column("WORK_CENTER", active["WORK_CENTER"], fl["WORK_CENTER"], routing["WORK_CENTER"])

# ── Overall exposure: how many orders are touched by ANY vocabulary problem ────
print(f"\n{'='*90}")
print("OVERALL EXPOSURE")
print(f"{'='*90}")
print(f"Total distinct active PRODUCTION_ORDERs (CT>0, balance>0): {active['PRODUCTION_ORDER'].nunique()}")
print(f"Total active rows: {len(active)}")

for col in ["DESIGN", "CLASS"]:
    print(f"\nBy {col} (active rows):")
    print(active[col].value_counts().to_string())

print(f"\nDistinct orders with CLASS='PN10': {active[active['CLASS']=='PN10']['PRODUCTION_ORDER'].nunique()}")
print(f"Distinct orders with DESIGN='DFS': {active[active['DESIGN']=='DFS']['PRODUCTION_ORDER'].nunique()}")

print(f"\n{'='*90}")
print("AUDIT COMPLETE")
print(f"{'='*90}")
