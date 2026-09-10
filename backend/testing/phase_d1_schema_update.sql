-- Model D Phase 1: Schema update for fixture/locator tracking on MCH_SCHEDULE_OUTPUT.
--
-- FIXTURE_ID / LOCATOR_ID: record the DEVICE_NAME (from MCH_FIXTURE_LOCATOR) actually
-- assigned to this (order, operation) row, so the finite fixture/locator pool stays
-- auditable (Model D D.13).
--
-- START_TIMESTAMP / END_TIMESTAMP: absolute continuous-time start/end for this row,
-- added now (rather than as a second migration before Phase 3) since Model D's
-- dispatch simulator computes continuous wall-clock times, not just a date+shift.
-- SCHEDULED_DATE/SHIFT/START_OFFSET_MIN/END_OFFSET_MIN are Model C's display
-- convention and are left in place; Phase 3 will decide their Model D meaning.

ALTER TABLE MCH_SCHEDULE_OUTPUT
ADD (
    FIXTURE_ID       VARCHAR2(40),
    LOCATOR_ID       VARCHAR2(40),
    START_TIMESTAMP  TIMESTAMP,
    END_TIMESTAMP    TIMESTAMP
);

-- Indexes for pool-auditability queries (e.g. "what's using Fixture B right now").
CREATE INDEX IDX_MCH_SCHEDULE_FIXTURE_ID ON MCH_SCHEDULE_OUTPUT(FIXTURE_ID);
CREATE INDEX IDX_MCH_SCHEDULE_LOCATOR_ID ON MCH_SCHEDULE_OUTPUT(LOCATOR_ID);

-- Verify
DESC MCH_SCHEDULE_OUTPUT;

COMMIT;
