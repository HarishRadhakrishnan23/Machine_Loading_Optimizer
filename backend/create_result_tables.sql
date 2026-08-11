-- create_result_tables.sql
--
-- DDL to create the two result tables owned by the TOV Machine Loading Optimizer.
--
-- These tables are written by:
--   - MCH_SCHEDULE_OUTPUT: Engine 1 (scheduling optimizer)
--   - MCH_SIM_RESULTS: Engine 2 (priority simulation / impact analyzer)
--
-- The 4 ERP views (MCH_WIP, MCH_MACHINE_AVAILABILITY, MCH_MACHINE_AVAILABILITY_BY_DATE,
-- MCH_MACHINE_PRIORITY) are read-only and already exist in Oracle (created by ERP team).
--
-- Run this script in Oracle SQL Developer:
--   1. Connect to your database (e.g., Production_ERP)
--   2. Copy-paste the DDL below and execute
--   3. Verify: SELECT * FROM MCH_SCHEDULE_OUTPUT; SELECT * FROM MCH_SIM_RESULTS;

-- ═══════════════════════════════════════════════════════════════════════════
-- MCH_SCHEDULE_OUTPUT — Engine 1 Scheduling Results
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE MCH_SCHEDULE_OUTPUT (
    RUN_ID            VARCHAR2(36)  NOT NULL,   -- one id per /schedule/generate run (e.g., UUID)
    PRODUCTION_ORDER  VARCHAR2(9)   NOT NULL,   -- order identifier
    OPERATION_NO      NUMBER        NOT NULL,   -- ascending operation sequence number (10, 20, 30, 35, ...)
    TASK              VARCHAR2(51),             -- task/operation code (VB02, VB03, R002, ...); display only, nullable
    WORK_CENTER       VARCHAR2(49)  NOT NULL,   -- assigned machine identifier (e.g., M1, M2, M3)
    SHIFT             VARCHAR2(10)  NOT NULL,   -- shift: first | second | third
    SCHEDULED_DATE    DATE          NOT NULL,   -- calendar date the batch is scheduled
    BALANCE_QTY       NUMBER        NOT NULL,   -- number of pieces scheduled in THIS slot (may overflow across multiple rows)
    START_OFFSET_MIN  NUMBER        NOT NULL,   -- display: start time within shift, in minutes (0 to WORKING_MINS)
    END_OFFSET_MIN    NUMBER        NOT NULL,   -- display: end time within shift, in minutes
    GENERATED_AT      TIMESTAMP     NOT NULL,   -- timestamp when the schedule was generated

    CONSTRAINT PK_MCH_SCHEDULE_OUTPUT
        PRIMARY KEY (RUN_ID, PRODUCTION_ORDER, OPERATION_NO, WORK_CENTER, SHIFT, SCHEDULED_DATE)
);

-- Create index on (PRODUCTION_ORDER) for quick order lookup
CREATE INDEX IX_MCH_SCHEDULE_OUTPUT_ORDER ON MCH_SCHEDULE_OUTPUT(PRODUCTION_ORDER);

-- Create index on SCHEDULED_DATE for range queries
CREATE INDEX IX_MCH_SCHEDULE_OUTPUT_DATE ON MCH_SCHEDULE_OUTPUT(SCHEDULED_DATE);


-- ═══════════════════════════════════════════════════════════════════════════
-- MCH_SIM_RESULTS — Engine 2 Simulation Results
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE MCH_SIM_RESULTS (
    SIM_ID               VARCHAR2(36)  NOT NULL,  -- one id per /priority/simulate run (e.g., UUID)
    ELEVATED_ORDER       VARCHAR2(200) NOT NULL,  -- comma-joined list of elevated PRODUCTION_ORDER(s)
    PRODUCTION_ORDER     VARCHAR2(9)   NOT NULL,  -- the impacted order (Oracle reserves word ORDER, so column is PRODUCTION_ORDER)
    OLD_COMPLETION_DATE  DATE,                    -- completion date from the baseline schedule
    NEW_COMPLETION_DATE  DATE,                    -- completion date after elevation
    SLIP_DAYS            NUMBER,                   -- new_completion - old_completion, in calendar days (positive = delay)
    RISK_FLAG            VARCHAR2(10)  NOT NULL,   -- SAFE | AT_RISK | BREACH (see engine2_recommender.py risk_classifier)
    CREATED_AT           TIMESTAMP     NOT NULL,   -- timestamp when simulation was run

    CONSTRAINT PK_MCH_SIM_RESULTS
        PRIMARY KEY (SIM_ID, PRODUCTION_ORDER)
);

-- Create index on PRODUCTION_ORDER for impact lookup
CREATE INDEX IX_MCH_SIM_RESULTS_ORDER ON MCH_SIM_RESULTS(PRODUCTION_ORDER);

-- Create index on RISK_FLAG for risk dashboard queries
CREATE INDEX IX_MCH_SIM_RESULTS_RISK ON MCH_SIM_RESULTS(RISK_FLAG);

-- Create index on CREATED_AT for time-range queries (latest simulations first)
CREATE INDEX IX_MCH_SIM_RESULTS_CREATED ON MCH_SIM_RESULTS(CREATED_AT DESC);


-- ═══════════════════════════════════════════════════════════════════════════
-- Verification Queries
-- ═══════════════════════════════════════════════════════════════════════════

-- Run these to verify the tables were created successfully:
--
-- SELECT COUNT(*) AS schedule_output_rows FROM MCH_SCHEDULE_OUTPUT;
-- SELECT COUNT(*) AS sim_results_rows FROM MCH_SIM_RESULTS;
--
-- DESC MCH_SCHEDULE_OUTPUT;    -- show column definitions
-- DESC MCH_SIM_RESULTS;
