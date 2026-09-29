-- Model E: MCH_SCHEDULE_OUTPUT rebuild.
-- Replaces the Model C/D shape (slot-based PK, shift-offset display columns)
-- with Model E's one-row-per-(order,operation[,split]) continuous-time shape.
-- Table is truncated and rewritten every /schedule/generate run (no history
-- retained), so a drop + recreate is safe.

DROP TABLE MCH_SCHEDULE_OUTPUT CASCADE CONSTRAINTS PURGE;

CREATE TABLE MCH_SCHEDULE_OUTPUT (
    RUN_ID                  VARCHAR2(36)   NOT NULL,   -- audit only; not part of PK (table holds one run at a time)
    PRODUCTION_ORDER        VARCHAR2(9)    NOT NULL,
    OPERATION_NO            NUMBER         NOT NULL,   -- = MCH_WIP.OPERATION
    LINE_NO                 NUMBER         DEFAULT 1 NOT NULL,  -- increments only on a machine-handoff split
    TASK                    VARCHAR2(51),
    WORK_CENTER             VARCHAR2(49),               -- NULL on unscheduled/REMARK rows
    SHIFT                   VARCHAR2(10),                -- NULL on unscheduled/REMARK rows
    SCHEDULED_DATE          DATE,                        -- NULL on unscheduled/REMARK rows
    BALANCE_QTY             NUMBER         NOT NULL,
    GENERATED_AT            TIMESTAMP(6)   NOT NULL,
    BATCH_KEY               VARCHAR2(100),               -- SIZE_INCH~CLASS~MOC~DESIGN
    IS_SAFETY_STOCK         CHAR(1)        DEFAULT 'N',
    FIXTURE_ID              VARCHAR2(40),
    LOCATOR_ID              VARCHAR2(40),
    START_TIMESTAMP         TIMESTAMP(6),
    END_TIMESTAMP           TIMESTAMP(6),
    REMARK                  VARCHAR2(200),               -- human-readable miss/caveat reason
    ORDER_COMPLETION_DATE   DATE,
    ORDER_COMPLETION_SHIFT  VARCHAR2(10),
    CONSTRAINT PK_MCH_SCHEDULE_OUTPUT
        PRIMARY KEY (PRODUCTION_ORDER, OPERATION_NO, LINE_NO)
);

CREATE INDEX IDX_MCH_SCHEDULE_FIXTURE_ID ON MCH_SCHEDULE_OUTPUT(FIXTURE_ID);
CREATE INDEX IDX_MCH_SCHEDULE_LOCATOR_ID ON MCH_SCHEDULE_OUTPUT(LOCATOR_ID);
CREATE INDEX IDX_MCH_SCHEDULE_RUN_ID     ON MCH_SCHEDULE_OUTPUT(RUN_ID);

DESC MCH_SCHEDULE_OUTPUT;

COMMIT;
