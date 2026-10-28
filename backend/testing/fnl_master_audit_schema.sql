-- Audit trail for the Priority Shuffler (the app's only write path into
-- MCH_MACHINE_FNL_MASTER, see backend/fnl_master_admin.py and CLAUDE.md "Priority Shuffler").
-- Apply manually in Oracle SQL Developer, same as the other app-owned tables.
-- Append-only: the application never updates or deletes rows here.

CREATE TABLE MCH_FNL_MASTER_AUDIT (
    AUDIT_ID      VARCHAR2(36)   NOT NULL,   -- one id per user action (shared by every row it touched)
    CHANGED_AT    TIMESTAMP(6)   NOT NULL,
    CHANGED_BY    VARCHAR2(100),
    ACTION        VARCHAR2(20)   NOT NULL,   -- 'PRIORITY' | 'ADD_MACHINE'
    SIZE_INCH     VARCHAR2(40),
    CLASS         VARCHAR2(40),
    MOC           VARCHAR2(80),
    DESIGN        VARCHAR2(80),
    TASK          VARCHAR2(51),
    WORK_CENTER   VARCHAR2(49),
    OLD_PRIORITY  NUMBER,                    -- NULL for a newly added machine row
    NEW_PRIORITY  NUMBER,
    NOTE          VARCHAR2(400)
);

CREATE INDEX IX_FNL_AUDIT_CHANGED_AT ON MCH_FNL_MASTER_AUDIT (CHANGED_AT);
CREATE INDEX IX_FNL_AUDIT_AUDIT_ID   ON MCH_FNL_MASTER_AUDIT (AUDIT_ID);
