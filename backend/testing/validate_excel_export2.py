#!/usr/bin/env python3
"""Validate the Excel export (MCH_WIP INNER JOIN MCH_SCHEDULE_OUTPUT)."""

import pandas as pd

PATH = r"C:\Users\E1558411\OneDrive - Emerson\Desktop\Machine Loading Project\MCH_SCH_OUTPUT_ENGINE1.xlsx"

df = pd.read_excel(PATH, sheet_name="Export Worksheet")
print(f"Total rows: {len(df)}")
print(f"Columns: {list(df.columns)}\n")

# balance_qty per WIP row
df["BALANCE_QTY"] = df["QUANTITY_ORDERED"] - df["QUANTITY_COMPLETED"] - df["QUANTITY_REJECTED"].fillna(0)

print("=" * 80)
print("[1] Duplicate rows (exact scheduling-key duplicates)")
print("=" * 80)
dup_key = ["PRODUCTION_ORDER", "OPERATION", "WORK_CENTER_1", "SHIFT", "SCHEDULED_DATE", "Planned_Quantity"]
dups = df[df.duplicated(subset=dup_key, keep=False)]
print(f"Duplicate rows on {dup_key}: {len(dups)}")
if len(dups):
    print(dups[dup_key].head(10))

print("\n" + "=" * 80)
print("[2] Over-scheduling check: sum(Planned_Quantity) per (order, operation) vs BALANCE_QTY")
print("=" * 80)
grp = df.groupby(["PRODUCTION_ORDER", "OPERATION"]).agg(
    total_planned=("Planned_Quantity", "sum"),
    balance_qty=("BALANCE_QTY", "first"),
).reset_index()
over = grp[grp["total_planned"] > grp["balance_qty"]]
print(f"Tasks over-scheduled: {len(over)}")
if len(over):
    print(over.head(15).to_string(index=False))

under = grp[grp["total_planned"] < grp["balance_qty"]]
print(f"\nTasks under-scheduled (partial placement): {len(under)}")
print(f"  (expected for capacity-bound tasks; not necessarily an error)")

exact = grp[grp["total_planned"] == grp["balance_qty"]]
print(f"Tasks exactly matched: {len(exact)}")

print("\n" + "=" * 80)
print("[3] Batch consolidation check: does each (BATCH_KEY, TASK) use ONE machine?")
print("=" * 80)
batch_grp = df.groupby(["BATCH_KEY", "TASK"])["WORK_CENTER_1"].nunique().reset_index(name="num_machines")
multi_order_batches = df.groupby(["BATCH_KEY", "TASK"])["PRODUCTION_ORDER"].nunique().reset_index(name="num_orders")
merged = batch_grp.merge(multi_order_batches, on=["BATCH_KEY", "TASK"])
multi_order = merged[merged["num_orders"] > 1]
split = multi_order[multi_order["num_machines"] > 1]
print(f"Batch groups with >1 order: {len(multi_order)}")
print(f"Consolidated onto ONE machine: {len(multi_order) - len(split)}")
print(f"Still split across multiple machines: {len(split)}")
if len(split):
    print(split.head(10).to_string(index=False))

print("\n" + "=" * 80)
print("[4] IS_SAFETY_STOCK consistency: does it match CDD being NULL?")
print("=" * 80)
mismatch = df[(df["CDD"].isna()) != (df["IS_SAFETY_STOCK"] == "Y")]
print(f"Mismatched rows: {len(mismatch)}")
if len(mismatch):
    print(mismatch[["PRODUCTION_ORDER", "OPERATION", "CDD", "IS_SAFETY_STOCK"]].head(10))

print("\n" + "=" * 80)
print("[5] Precedence check: within an order, is SCHEDULED_DATE non-decreasing with OPERATION?")
print("=" * 80)
violations = []
for order, g in df.groupby("PRODUCTION_ORDER"):
    ops = g.groupby("OPERATION")["SCHEDULED_DATE"].max().sort_index()
    prev_op, prev_date = None, None
    for op, date in ops.items():
        if prev_date is not None and date < prev_date:
            violations.append((order, prev_op, prev_date, op, date))
        prev_op, prev_date = op, date
print(f"Precedence violations: {len(violations)}")
if violations:
    for v in violations[:10]:
        print(f"  {v[0]}: Op{v[1]} ({v[2]}) -> Op{v[3]} ({v[4]}) — LATER OP SCHEDULED EARLIER")

print("\n" + "=" * 80)
print("[6] RUN_ID check — is this a single, coherent run?")
print("=" * 80)
print(f"Distinct RUN_IDs: {df['RUN_ID'].nunique()}")
print(df["RUN_ID"].value_counts())

print("\n" + "=" * 80)
print("[7] Capacity sanity: total minutes per (WORK_CENTER_1, SHIFT, SCHEDULED_DATE)")
print("=" * 80)
df["row_minutes"] = df["Planned_Quantity"] * df["CYCLE_TIME"]
cap_check = df.groupby(["WORK_CENTER_1", "SHIFT", "SCHEDULED_DATE"])["row_minutes"].sum().reset_index()
print(f"Distinct (machine, shift, date) slots used: {len(cap_check)}")
print(f"Max minutes in a single slot (excl. setup): {cap_check['row_minutes'].max():.1f}")
print(f"  (compare against AVAILABLE_MINS ~ 350-450 typical per shift; large values need setup-aware check)")

print("\n" + "=" * 80)
print("VALIDATION COMPLETE")
print("=" * 80)
