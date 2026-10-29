-- Fix for: "ORA-00001: unique constraint (...PK_MCH_SCHEDULE_OUTPUT_ARCHIVE) violated" on "Freeze schedule".
--
-- Cause: the archive is meant to be APPEND-ONLY history (one RUN_ID's worth of rows per freeze), but its primary key
-- was (PRODUCTION_ORDER, OPERATION_NO, LINE_NO) — without RUN_ID. So only the FIRST frozen run could ever be stored:
-- any later run contains the same (order, operation, line) keys and collides with it.
--
-- Fix: make RUN_ID part of the key. Safe on existing data (every existing row is already unique within its own run,
-- so it is trivially unique with RUN_ID added). Nothing is deleted or changed; the application code needs no change.
-- Apply once, manually, as the table owner (same way the other app-owned DDL was applied).

ALTER TABLE MCH_SCHEDULE_OUTPUT_ARCHIVE DROP CONSTRAINT PK_MCH_SCHEDULE_OUTPUT_ARCHIVE;

ALTER TABLE MCH_SCHEDULE_OUTPUT_ARCHIVE
    ADD CONSTRAINT PK_MCH_SCHEDULE_OUTPUT_ARCHIVE
    PRIMARY KEY (RUN_ID, PRODUCTION_ORDER, OPERATION_NO, LINE_NO);

-- Verify (should list RUN_ID, PRODUCTION_ORDER, OPERATION_NO, LINE_NO in this order):
SELECT cc.position, cc.column_name
FROM user_cons_columns cc
JOIN user_constraints c ON c.constraint_name = cc.constraint_name
WHERE c.table_name = 'MCH_SCHEDULE_OUTPUT_ARCHIVE' AND c.constraint_type = 'P'
ORDER BY cc.position;
