#!/usr/bin/env python3
"""Regenerate schedule: delete old, run fresh Engine 1."""

import os
import json
import logging
from pathlib import Path
from datetime import datetime, date
from dotenv import load_dotenv

import oracledb
import pandas as pd

from db import read_wip_orders, read_routing_master, read_machine_master, read_machine_daily
from preprocess import filter_wip_orders, compute_horizon, build_scheduler_input
from engine1_scheduler import run_engine1
from models import Config

# Setup
load_dotenv(Path(__file__).parent.parent / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('schedule_regen.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# STEP 1: DELETE OLD SCHEDULE
# ─────────────────────────────────────────────────────────────────────
logger.info("\n" + "=" * 80)
logger.info("STEP 1: DELETING OLD SCHEDULE DATA")
logger.info("=" * 80)

con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()

cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
old_count = cur.fetchone()[0]

if old_count > 0:
    cur.execute("DELETE FROM MCH_SCHEDULE_OUTPUT")
    con.commit()
    logger.info(f"✓ Deleted {old_count} old schedule rows")
else:
    logger.info("✓ MCH_SCHEDULE_OUTPUT already empty")

con.close()

# ─────────────────────────────────────────────────────────────────────
# STEP 2: FETCH FRESH DATA
# ─────────────────────────────────────────────────────────────────────
logger.info("\n" + "=" * 80)
logger.info("STEP 2: FETCHING FRESH DATA FROM ORACLE")
logger.info("=" * 80)

logger.info("Reading MCH_WIP...")
wip_df = read_wip_orders()
logger.info(f"  → {len(wip_df)} rows")

logger.info("Reading MCH_MACHINE_PRIORITY (routing)...")
routing_df = read_routing_master()
logger.info(f"  → {len(routing_df)} rows")

logger.info("Reading MCH_MACHINE_AVAILABILITY (baseline)...")
machine_master_df = read_machine_master()
logger.info(f"  → {len(machine_master_df)} rows")

logger.info("Reading MCH_MACHINE_AVAILABILITY_BY_DATE (daily overrides)...")
machine_daily_df = read_machine_daily()
logger.info(f"  → {len(machine_daily_df)} rows")

# ─────────────────────────────────────────────────────────────────────
# STEP 3: PREPROCESS
# ─────────────────────────────────────────────────────────────────────
logger.info("\n" + "=" * 80)
logger.info("STEP 3: PREPROCESSING")
logger.info("=" * 80)

wip_filtered = filter_wip_orders(wip_df, routing_df)
total_pieces = wip_filtered['balance_qty'].sum()
logger.info(f"✓ After filtering (CT>0, routable, balance>0): {len(wip_filtered)} tasks, {int(total_pieces)} pieces")

# ─────────────────────────────────────────────────────────────────────
# STEP 4: RUN ENGINE 1
# ─────────────────────────────────────────────────────────────────────
logger.info("\n" + "=" * 80)
logger.info("STEP 4: RUNNING ENGINE 1 SCHEDULER")
logger.info("=" * 80)

# Load config
with open('config.json') as f:
    config_dict = json.load(f)
config = Config(**config_dict)

today = date.today()
logger.info(f"Run date: {today}")

# Build scheduler input (compute horizon, add machine slots, etc.)
logger.info("Building scheduler input...")
horizon_dates = compute_horizon(wip_filtered, machine_master_df, config, today)
logger.info(f"  Horizon: {len(horizon_dates)} days ({horizon_dates[0]} → {horizon_dates[-1]})")

scheduler_input = build_scheduler_input(
    wip_df,
    machine_master_df,
    machine_daily_df,
    routing_df,
    horizon_dates=horizon_dates,
    config=config,
    today=today,
)
logger.info(f"  Tasks: {len(scheduler_input.tasks)}, Capacity slots: {len(scheduler_input.capacity)}")

# Run solver
logger.info("Solving CP-SAT model...")
result = run_engine1(scheduler_input, max_time_in_seconds=config.solver_time_limit_seconds)

# ─────────────────────────────────────────────────────────────────────
# STEP 5: VERIFY RESULTS
# ─────────────────────────────────────────────────────────────────────
logger.info("\n" + "=" * 80)
logger.info("STEP 5: VERIFICATION")
logger.info("=" * 80)

con = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)
cur = con.cursor()

# If result is successful, check database
if result.is_success:
    cur.execute("SELECT COUNT(*) FROM MCH_SCHEDULE_OUTPUT")
    new_count = cur.fetchone()[0]

    cur.execute("SELECT SUM(BALANCE_QTY) FROM MCH_SCHEDULE_OUTPUT")
    new_pieces = cur.fetchone()[0] or 0
else:
    logger.warning(f"⚠️  Solver returned {result.status.value} — no schedule written")
    new_count = 0
    new_pieces = 0

logger.info(f"✓ New schedule rows: {new_count}")
logger.info(f"✓ Total pieces scheduled: {int(new_pieces)}")

# Show distribution by machine
cur.execute("""
    SELECT WORK_CENTER, COUNT(*) as rows, SUM(BALANCE_QTY) as pieces
    FROM MCH_SCHEDULE_OUTPUT
    GROUP BY WORK_CENTER
    ORDER BY pieces DESC
""")
logger.info("\nTop 10 machines by pieces:")
for i, (machine, rows, pieces) in enumerate(cur.fetchall()[:10], 1):
    logger.info(f"  {i}. {machine}: {int(pieces)} pieces ({rows} rows)")

con.close()

# ─────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────
logger.info("\n" + "=" * 80)
logger.info("✓✓✓ SCHEDULE REGENERATION COMPLETE ✓✓✓")
logger.info("=" * 80)
logger.info(f"Solver status: {result.status}")
logger.info(f"Old schedule deleted: {old_count} rows")
logger.info(f"New schedule created: {new_count} rows, {int(new_pieces)} pieces")
logger.info(f"Run ID: {result.run_id}")
logger.info("=" * 80 + "\n")
