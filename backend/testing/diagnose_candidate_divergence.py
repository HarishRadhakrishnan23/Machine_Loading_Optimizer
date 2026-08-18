#!/usr/bin/env python3
"""
Diagnose WHY batch groups still split despite low utilization (3-4%).
Hypothesis: MOC affects routing_master's WORK_CENTER capability even though
our batch key (SIZE~CLASS~DESIGN) excludes MOC — so two orders in the "same"
batch can have genuinely DIFFERENT candidate machine sets, forcing a split
with zero capacity problem involved. Also captures override log lines from
a fresh greedy run so we see the ACTUAL reason recorded at decision time.
"""

import sys
import logging
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import read_wip_orders, read_routing_master, read_machine_master, read_machine_daily
from preprocess import filter_wip_orders, compute_horizon, build_scheduler_input
from engine1_scheduler import Engine1Scheduler
from models import Config
import json
from datetime import date

with open("config.json") as f:
    config = Config(**json.load(f))

routing_df = read_routing_master()
wip_df = read_wip_orders()
machine_master_df = read_machine_master()
machine_daily_df = read_machine_daily()

filtered_wip = filter_wip_orders(wip_df, routing_df)
horizon_dates = compute_horizon(filtered_wip, machine_master_df, config, date.today())
scheduler_input = build_scheduler_input(
    wip_df, machine_master_df, machine_daily_df, routing_df,
    horizon_dates=horizon_dates, config=config, today=date.today(),
)

# Capture logger output to a string buffer AND console (debug level, so we see
# "Batch override" lines) without waiting for the full CP-SAT solve — greedy only.
logger = logging.getLogger("diagnose_greedy")
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())

engine = Engine1Scheduler(scheduler_input, logger=logger)

# ── Part A: candidate-set divergence check, for EVERY multi-order batch group ──
print("=" * 80)
print("PART A: Candidate machine set divergence within batch groups")
print("=" * 80)

divergent_groups = []
for group_key, pids in engine.tasks_in_batch_group.items():
    if len(pids) <= 1:
        continue
    candidate_sets = [frozenset(engine.candidates[pid]) for pid in pids]
    unique_sets = set(candidate_sets)
    if len(unique_sets) > 1:
        divergent_groups.append((group_key, pids, unique_sets))

print(f"Total multi-order batch groups: {sum(1 for p in engine.tasks_in_batch_group.values() if len(p) > 1)}")
print(f"Groups with DIVERGENT candidate sets across members: {len(divergent_groups)}")

if divergent_groups:
    print("\nSample divergent groups (first 10):")
    for group_key, pids, unique_sets in divergent_groups[:10]:
        print(f"\n  Group {group_key} ({len(pids)} tasks):")
        for pid in pids[:6]:
            print(f"    {pid[0]} Op{pid[1]}: candidates={sorted(engine.candidates[pid])}")
        common = set.intersection(*[set(s) for s in unique_sets])
        print(f"    -> Common machines across ALL members: {sorted(common) if common else '(NONE)'}")

# ── Part B: run the actual batch-aware greedy pass, watch for override lines ──
print("\n" + "=" * 80)
print("PART B: Running greedy_schedule() live — watch for 'Batch override' lines above")
print("=" * 80)
assign = engine.greedy_schedule()

# ── Part C: cross-check which split groups in the RESULT correspond to
#    divergent-candidate groups vs. groups where all members DID share full
#    candidate overlap (meaning override happened despite no set divergence —
#    a real logic issue worth digging into further) ──
print("\n" + "=" * 80)
print("PART C: For groups that ended up split in the ACTUAL assignment, was it")
print("        due to candidate divergence, or something else?")
print("=" * 80)

group_machines_used = defaultdict(set)
for (pid, m, k), qty in assign.items():
    if qty > 0:
        group_key = engine.batch_group_of.get(pid)
        if group_key:
            group_machines_used[group_key].add(m)

divergent_group_keys = {g for g, _, _ in divergent_groups}
split_due_to_divergence = 0
split_unexplained = 0
for group_key, machines_used in group_machines_used.items():
    pids = engine.tasks_in_batch_group.get(group_key, [])
    if len(pids) <= 1 or len(machines_used) <= 1:
        continue
    if group_key in divergent_group_keys:
        split_due_to_divergence += 1
    else:
        split_unexplained += 1
        if split_unexplained <= 5:
            print(f"\n  UNEXPLAINED split: {group_key}")
            print(f"    Machines used: {sorted(machines_used)}")
            for pid in pids[:6]:
                print(f"    {pid[0]} Op{pid[1]}: candidates={sorted(engine.candidates[pid])}")

print(f"\nSplit groups explained by candidate divergence: {split_due_to_divergence}")
print(f"Split groups UNEXPLAINED by divergence (needs further digging): {split_unexplained}")

print("\n" + "=" * 80)
print("DIAGNOSIS COMPLETE")
print("=" * 80)
