-- Phase 1: Database Schema Update for Batch-Aware Scheduling
-- Add BATCH_KEY and IS_SAFETY_STOCK columns to MCH_SCHEDULE_OUTPUT
-- Drop MCH_SCHEDULE_OUTPUT_ARCHIVE (no longer needed)

-- Step 1: Add BATCH_KEY column (SIZE~CLASS~DESIGN, e.g., "3~300~DFS")
ALTER TABLE MCH_SCHEDULE_OUTPUT
ADD (BATCH_KEY VARCHAR2(100));

-- Step 2: Add IS_SAFETY_STOCK column (Y/N flag for safety stock orders)
ALTER TABLE MCH_SCHEDULE_OUTPUT
ADD (IS_SAFETY_STOCK CHAR(1) DEFAULT 'N');

-- Step 3: Create index on BATCH_KEY for faster UI filtering
CREATE INDEX IDX_MCH_SCHEDULE_BATCH_KEY ON MCH_SCHEDULE_OUTPUT(BATCH_KEY);

-- Step 4: Create index on IS_SAFETY_STOCK for UI flagging
CREATE INDEX IDX_MCH_SCHEDULE_SAFETY_STOCK ON MCH_SCHEDULE_OUTPUT(IS_SAFETY_STOCK);

-- Step 5: Drop archive table (no longer needed - each Engine 1 run deletes previous data)
DROP TABLE MCH_SCHEDULE_OUTPUT_ARCHIVE;

-- Step 6: Verify schema
DESC MCH_SCHEDULE_OUTPUT;

-- Commit
COMMIT;
