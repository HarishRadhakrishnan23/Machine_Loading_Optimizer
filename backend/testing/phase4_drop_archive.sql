-- Phase 4: Remove archiving — this application no longer keeps historical
-- schedule/sim data. Each Engine 1 run deletes MCH_SCHEDULE_OUTPUT and writes
-- fresh (see pipeline.py::_persist_schedule_output); no archive table needed.

DROP TABLE MCH_SCHEDULE_OUTPUT_ARCHIVE;
DROP TABLE MCH_SIM_RESULTS_ARCHIVE;

COMMIT;
