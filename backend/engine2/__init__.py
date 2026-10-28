"""
engine2 — the independent what-if engine (CLAUDE.md "Engine 2 — independent what-if engine").

Independence rules, enforced by construction:
  * Engine 1 never imports this package and is never edited for it.
  * This package only CALLS Engine 1's existing code (dispatch_*, model_f_data,
    db readers) read-only, so its baseline is by construction the same engine
    as the real schedule.
  * Reads only. The one permitted write is the explicit "Save this result"
    into MCH_SIM_RESULTS (service.save_result).
  * Results are advisory — nothing here changes the real schedule.

Modules: snapshot (input data) -> scenarios (edit a COPY of the inputs) ->
runner (simulate with Engine 1's own simulator) -> metrics (diff + verdict) ->
service (orchestration, in-memory result store) -> api (FastAPI router).
"""
