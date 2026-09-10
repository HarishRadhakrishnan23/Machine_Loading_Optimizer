#!/usr/bin/env python3
"""
Model D Phase 1 validation: fixture/locator data load & lookup.

Proves (or disproves) the assumptions Model D's spec (CLAUDE.md D.1, D.11) is built on:

  [1] Fixture/locator lookups resolve for in-scope tasks (CT>0, balance>0, not QAINSP,
      has a routing entry). An op with routing but no fixture/locator match is SKIPPED
      transparently per D.11 — not an error — but we report how many/which, since a
      large count here would silently shrink the schedulable set.

  [2] FIXTURE (and LOCATOR) are IDENTICAL across every WORK_CENTER row for a given
      (SIZE_INCH, CLASS, DESIGN, TASK) — the assumption D.1 states and the user
      confirmed (Q7). If this is violated anywhere, Model D's per-machine duration
      formula (D.5) and the whole-plant pool constraint (D.8) both need rethinking,
      so this MUST be checked before any Model D code is written.

  [3] Every FIXTURE/LOCATOR actually referenced by an in-scope task exists in the
      MCH_FIXTURE_LOCATOR inventory table with DEVICE_TYPE in ('F','L') and a
      positive QUANTITY — otherwise Phase 4's pool constraint has nothing to check
      against for that device.

Read-only against live Oracle data. No schema/code changes made by this script.
"""

import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from db import (
    read_wip_orders,
    read_routing_master,
    read_fixture_locator_master,
    read_fixture_locator_inventory,
)

QA_TOKEN = "QAINSP"

print("=" * 80)
print("MODEL D PHASE 1: FIXTURE/LOCATOR DATA LOAD & LOOKUP VALIDATION")
print("=" * 80)

print("\nFetching data from Oracle...")
wip_df = read_wip_orders()
routing_df = read_routing_master()
fl_master_df = read_fixture_locator_master()
fl_inventory_df = read_fixture_locator_inventory()

print(f"  MCH_WIP: {len(wip_df)} rows")
print(f"  MCH_MACHINE_PRIORITY (routing): {len(routing_df)} rows")
print(f"  MCH_ITEMWISE_FIXTURE_LOCATOR: {len(fl_master_df)} rows")
print(f"  MCH_FIXTURE_LOCATOR (inventory): {len(fl_inventory_df)} rows")

# ── D.11 scope filters (CT>0, balance>0, not QAINSP, has routing entry) ────────
print("\n" + "=" * 80)
print("[SCOPE] Applying D.11 filters (CT>0, balance>0, not QAINSP, routed)")
print("=" * 80)

df = wip_df.copy()
before = len(df)
df = df[df["CYCLE_TIME"] > 0]
print(f"  After CYCLE_TIME > 0: {len(df)} (dropped {before - len(df)})")

before = len(df)
df = df[~df["WORK_CENTER"].astype(str).str.upper().str.contains(QA_TOKEN)]
print(f"  After dropping QAINSP: {len(df)} (dropped {before - len(df)})")

df["balance_qty"] = df["QUANTITY_ORDERED"] - df["QUANTITY_COMPLETED"] - df["QUANTITY_REJECTED"].fillna(0)
before = len(df)
df = df[df["balance_qty"] > 0]
print(f"  After balance_qty > 0: {len(df)} (dropped {before - len(df)})")

routable_tasks = set(routing_df["TASK"].unique())
before = len(df)
df = df[df["TASK"].isin(routable_tasks)]
print(f"  After routing TASK filter: {len(df)} (dropped {before - len(df)})")

print(f"\n  In-scope operations (D.11, pre fixture/locator check): {len(df)}")
print(f"  Distinct (SIZE_INCH, CLASS, DESIGN, TASK) combos: "
      f"{df[['SIZE_INCH','CLASS','DESIGN','TASK']].drop_duplicates().shape[0]}")

# ── [1] Fixture/locator lookup coverage ────────────────────────────────────────
print("\n" + "=" * 80)
print("[1] FIXTURE/LOCATOR LOOKUP COVERAGE")
print("=" * 80)

# Build routing candidate machines per (SIZE_INCH, CLASS, MOC, DESIGN, TASK) — full
# ITEM_CATEGORY match, as routing actually works.
routing_key_cols = ["SIZE_INCH", "CLASS", "MOC", "DESIGN", "TASK"]
routing_candidates = (
    routing_df.groupby(routing_key_cols)["WORK_CENTER"]
    .apply(lambda s: set(s))
    .to_dict()
)

# Build fixture/locator lookup: (SIZE_INCH, CLASS, DESIGN, TASK, WORK_CENTER) -> row
# NOTE: fixture master is MOC-independent per D.1.
fl_lookup = {}
for _, row in fl_master_df.iterrows():
    key = (row["SIZE_INCH"], row["CLASS"], row["DESIGN"], row["TASK"], row["WORK_CENTER"])
    fl_lookup[key] = row

resolved = 0
unresolved = 0
unresolved_samples = []

for _, row in df.iterrows():
    rkey = (row["SIZE_INCH"], row["CLASS"], row["MOC"], row["DESIGN"], row["TASK"])
    candidate_machines = routing_candidates.get(rkey, set())
    if not candidate_machines:
        continue  # not routable at all — already excluded from D.11 scope in theory,
                  # but MOC-specific rkey might miss even though TASK-only filter passed

    has_fl_match = any(
        (row["SIZE_INCH"], row["CLASS"], row["DESIGN"], row["TASK"], m) in fl_lookup
        for m in candidate_machines
    )
    if has_fl_match:
        resolved += 1
    else:
        unresolved += 1
        if len(unresolved_samples) < 15:
            unresolved_samples.append(
                (row["PRODUCTION_ORDER"], row["OPERATION"], row["TASK"],
                 row["SIZE_INCH"], row["CLASS"], row["DESIGN"], row["MOC"],
                 sorted(candidate_machines))
            )

total = resolved + unresolved
pct = (resolved / total * 100) if total else 0.0
print(f"  In-scope rows with >=1 fixture/locator match on a candidate machine: {resolved}/{total} ({pct:.1f}%)")
print(f"  In-scope rows with routing but NO fixture/locator match (skipped per D.11): {unresolved}")

if unresolved_samples:
    print("\n  Sample unresolved (routed but no fixture/locator row on any candidate machine):")
    for order, op, task, size, cls, design, moc, machines in unresolved_samples:
        print(f"    {order} Op{op} TASK={task} {size}~{cls}~{design}~{moc} "
              f"candidates={machines}")

# ── [2] FIXTURE/LOCATOR constancy across WORK_CENTER ───────────────────────────
print("\n" + "=" * 80)
print("[2] FIXTURE/LOCATOR CONSTANCY ACROSS WORK_CENTER (per SIZE~CLASS~DESIGN~TASK)")
print("=" * 80)

group_cols = ["SIZE_INCH", "CLASS", "DESIGN", "TASK"]
violations = []
for key, g in fl_master_df.groupby(group_cols):
    distinct_fixtures = g["FIXTURE"].nunique(dropna=False)
    distinct_locators = g["LOCATOR"].nunique(dropna=False)
    if distinct_fixtures > 1 or distinct_locators > 1:
        violations.append((key, g[["WORK_CENTER", "FIXTURE", "LOCATOR"]].to_dict("records")))

print(f"  Distinct (SIZE~CLASS~DESIGN~TASK) groups checked: "
      f"{fl_master_df.groupby(group_cols).ngroups}")
print(f"  Groups where FIXTURE or LOCATOR varies across WORK_CENTER: {len(violations)}")

if violations:
    print("\n  VIOLATIONS (breaks D.1 assumption — must resolve before Phase 2):")
    for key, rows in violations[:15]:
        print(f"    {key}:")
        for r in rows:
            print(f"      {r}")
else:
    print("  [OK] FIXTURE and LOCATOR are constant across WORK_CENTER for every group.")

# ── [3] Inventory coverage for referenced fixtures/locators ────────────────────
print("\n" + "=" * 80)
print("[3] MCH_FIXTURE_LOCATOR INVENTORY COVERAGE")
print("=" * 80)

bad_device_type = fl_inventory_df[~fl_inventory_df["DEVICE_TYPE"].isin(["F", "L"])]
print(f"  Rows with DEVICE_TYPE not in ('F','L'): {len(bad_device_type)}")
if len(bad_device_type):
    print(bad_device_type[["DEVICE_NAME", "DEVICE_TYPE"]].head(10).to_string(index=False))

bad_qty = fl_inventory_df[fl_inventory_df["QUANTITY"].isna() | (fl_inventory_df["QUANTITY"] <= 0)]
print(f"  Rows with QUANTITY missing or <= 0: {len(bad_qty)}")
if len(bad_qty):
    print(bad_qty[["DEVICE_NAME", "DEVICE_TYPE", "QUANTITY"]].head(10).to_string(index=False))

inventory_fixtures = set(fl_inventory_df[fl_inventory_df["DEVICE_TYPE"] == "F"]["DEVICE_NAME"])
inventory_locators = set(fl_inventory_df[fl_inventory_df["DEVICE_TYPE"] == "L"]["DEVICE_NAME"])

# Which fixtures/locators are actually referenced by IN-SCOPE, RESOLVED tasks?
referenced_fixtures = set()
referenced_locators = set()
for _, row in df.iterrows():
    rkey = (row["SIZE_INCH"], row["CLASS"], row["MOC"], row["DESIGN"], row["TASK"])
    candidate_machines = routing_candidates.get(rkey, set())
    for m in candidate_machines:
        fl_row = fl_lookup.get((row["SIZE_INCH"], row["CLASS"], row["DESIGN"], row["TASK"], m))
        if fl_row is not None:
            if pd.notna(fl_row["FIXTURE"]):
                referenced_fixtures.add(fl_row["FIXTURE"])
            if pd.notna(fl_row["LOCATOR"]):
                referenced_locators.add(fl_row["LOCATOR"])

missing_fixtures = referenced_fixtures - inventory_fixtures
missing_locators = referenced_locators - inventory_locators

print(f"\n  Distinct FIXTUREs referenced by in-scope tasks: {len(referenced_fixtures)}")
print(f"  ...missing from MCH_FIXTURE_LOCATOR inventory: {len(missing_fixtures)}")
if missing_fixtures:
    print(f"    {sorted(missing_fixtures)[:20]}")

print(f"\n  Distinct LOCATORs referenced by in-scope tasks: {len(referenced_locators)}")
print(f"  ...missing from MCH_FIXTURE_LOCATOR inventory: {len(missing_locators)}")
if missing_locators:
    print(f"    {sorted(missing_locators)[:20]}")

# ── Summary ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("PHASE 1 VALIDATION SUMMARY")
print("=" * 80)
gate_1 = pct >= 95.0  # arbitrary high bar; report exact number regardless
gate_2 = len(violations) == 0
gate_3 = len(missing_fixtures) == 0 and len(missing_locators) == 0 and len(bad_device_type) == 0 and len(bad_qty) == 0

print(f"  [1] Lookup coverage:        {pct:.1f}% resolved  ({'PASS' if gate_1 else 'REVIEW'})")
print(f"  [2] Fixture/locator constancy: {'PASS' if gate_2 else 'FAIL'} ({len(violations)} violations)")
print(f"  [3] Inventory completeness: {'PASS' if gate_3 else 'FAIL'} "
      f"({len(missing_fixtures)} missing fixtures, {len(missing_locators)} missing locators, "
      f"{len(bad_device_type)} bad device types, {len(bad_qty)} bad quantities)")

if gate_2 and gate_3:
    print("\n  [READY] Data foundation is sound. Safe to proceed to Phase 2 (Batching).")
else:
    print("\n  [BLOCKED] Fix the data issues above before proceeding to Phase 2.")
print("=" * 80)
