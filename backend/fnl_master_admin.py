"""
fnl_master_admin.py — the Priority Shuffler's read/write path into
MCH_MACHINE_FNL_MASTER (CLAUDE.md "Priority Shuffler").

This is the ONLY place in the application allowed to write to
MCH_MACHINE_FNL_MASTER, and only two kinds of change are permitted:

  1. Re-rank PRIORITY among the machines of one SIZE~CLASS~MOC~DESIGN + TASK.
  2. Add a new machine row to an EXISTING SIZE~CLASS~MOC~DESIGN + TASK
     (fixture/locator identity and timings copied from an existing row,
     optionally overridden) at a chosen priority.

Nothing else is writable here: no deletes, no edits to fixture/locator
columns of existing rows, no brand-new combos.

Every change is appended to MCH_FNL_MASTER_AUDIT in the SAME transaction as
the change itself (backend/testing/fnl_master_audit_schema.sql) — if the
audit insert fails, the master change is rolled back too.

Rows are addressed by ROWID, never by re-matching the key columns, so a
NULL/blank MOC or DESIGN (a real ERP convention) can never make an UPDATE
silently match zero rows.

The engine reads this table fresh on every /schedule/generate, so a change
takes effect on the NEXT generated schedule; it does not alter the schedule
already sitting in MCH_SCHEDULE_OUTPUT.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Optional

import oracledb

from db import get_connection, read_machine_daily, read_machine_master

MASTER_TABLE = "MCH_MACHINE_FNL_MASTER"
AUDIT_TABLE = "MCH_FNL_MASTER_AUDIT"
KEY_COLS = ("SIZE_INCH", "CLASS", "MOC", "DESIGN", "TASK")
TIMING_COLS = ("FIXTURE_CHANGE_TIME", "LOCATOR_CHANGE_TIME", "LOAD_UNLOAD_TIME")
APP_USER_ID = "TOVMLO"


class FnlAdminError(Exception):
    """A user-correctable problem; `status` is the HTTP status main.py returns."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _norm(v) -> str:
    """Canonical text for a key value: NULL/NaN -> '', 8.0 -> '8', trimmed."""
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return str(int(v))
    return str(v).strip()


def _json_value(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _key_of(row: dict) -> tuple[str, ...]:
    return tuple(_norm(row.get(c)) for c in KEY_COLS)


def _fetch_master(cursor) -> tuple[list[str], list[dict]]:
    cursor.execute(f"SELECT ROWID AS RID, t.* FROM {MASTER_TABLE} t")
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
    return cols, rows


def _combo_rows(rows: list[dict], key: tuple[str, ...]) -> list[dict]:
    return [r for r in rows if _key_of(r) == key]


def _key_from_request(size_inch, class_val, moc, design, task) -> tuple[str, ...]:
    return (_norm(size_inch), _norm(class_val), _norm(moc), _norm(design), _norm(task))


def _assert_audit_table(cursor) -> None:
    try:
        cursor.execute(f"SELECT 1 FROM {AUDIT_TABLE} WHERE 1 = 0")
    except oracledb.DatabaseError as e:
        if "ORA-00942" in str(e):
            raise FnlAdminError(
                f"{AUDIT_TABLE} does not exist yet. Apply backend/testing/fnl_master_audit_schema.sql "
                "in Oracle SQL Developer first - priority changes are never made without an audit trail.",
                status=503,
            )
        raise


def _insert_audit(cursor, audit_id, now, changed_by, action, key, work_center, old_p, new_p, note=None) -> None:
    cursor.execute(
        f"""
        INSERT INTO {AUDIT_TABLE}
        (AUDIT_ID, CHANGED_AT, CHANGED_BY, ACTION, SIZE_INCH, CLASS, MOC, DESIGN, TASK,
         WORK_CENTER, OLD_PRIORITY, NEW_PRIORITY, NOTE)
        VALUES (:audit_id, :changed_at, :changed_by, :action, :size_inch, :class_val, :moc, :design, :task,
                :work_center, :old_p, :new_p, :note)
        """,
        {
            "audit_id": audit_id, "changed_at": now, "changed_by": (changed_by or "planner")[:100],
            "action": action, "size_inch": key[0], "class_val": key[1], "moc": key[2], "design": key[3],
            "task": key[4], "work_center": work_center, "old_p": old_p, "new_p": new_p,
            "note": (note[:400] if note else None),
        },
    )


def _stamp(cols: list[str], now: datetime) -> dict:
    """USER_ID / USER_DATE (last-change stamp) if this table has them."""
    out = {}
    if "USER_ID" in cols:
        out["USER_ID"] = APP_USER_ID
    if "USER_DATE" in cols:
        out["USER_DATE"] = now
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Reads
# ─────────────────────────────────────────────────────────────────────────────
def known_machines() -> list[str]:
    """Every machine the availability views know about — the only machines the
    engine can actually place work on (see model_f_pipeline's known_machines)."""
    names = set(read_machine_master()["WORK_CENTER"].astype(str).str.strip())
    names |= set(read_machine_daily()["WORK_CENTER"].astype(str).str.strip())
    return sorted(names)


def read_master_for_ui() -> list[dict]:
    with get_connection() as conn:
        _, rows = _fetch_master(conn.cursor())
    out = []
    for r in rows:
        out.append({
            "size_inch": _norm(r.get("SIZE_INCH")),
            "class": _norm(r.get("CLASS")),
            "moc": _norm(r.get("MOC")),
            "design": _norm(r.get("DESIGN")),
            "task": _norm(r.get("TASK")),
            "work_center": _norm(r.get("WORK_CENTER")),
            "priority": _json_value(r.get("PRIORITY")),
            "fixture_id": _json_value(r.get("FIXTURE_ID")),
            "locator_id": _json_value(r.get("LOCATOR_ID")),
            "fixture_change_time": _json_value(r.get("FIXTURE_CHANGE_TIME")),
            "locator_change_time": _json_value(r.get("LOCATOR_CHANGE_TIME")),
            "load_unload_time": _json_value(r.get("LOAD_UNLOAD_TIME")),
        })
    return out


def read_audit(limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        _assert_audit_table(cur)
        cur.execute(
            f"""
            SELECT AUDIT_ID, CHANGED_AT, CHANGED_BY, ACTION, SIZE_INCH, CLASS, MOC, DESIGN, TASK,
                   WORK_CENTER, OLD_PRIORITY, NEW_PRIORITY, NOTE
            FROM {AUDIT_TABLE}
            ORDER BY CHANGED_AT DESC
            FETCH FIRST :n ROWS ONLY
            """,
            {"n": int(limit)},
        )
        cols = [d[0].lower() for d in cur.description]
        return [{c: _json_value(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Writes
# ─────────────────────────────────────────────────────────────────────────────
def update_priorities(
    size_inch, class_val, moc, design, task,
    items: list[dict],
    changed_by: Optional[str] = None,
) -> dict:
    """
    Re-rank PRIORITY for one combo+task. `items` must list EVERY machine of the
    combo: [{work_center, priority (new), expected_priority (what the UI saw)}].
    New priorities must be unique positive integers. Atomic: all or nothing.
    """
    key = _key_from_request(size_inch, class_val, moc, design, task)
    now = datetime.now()
    audit_id = uuid.uuid4().hex

    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _assert_audit_table(cur)
            cols, all_rows = _fetch_master(cur)
            combo = _combo_rows(all_rows, key)
            if not combo:
                raise FnlAdminError("No MCH_MACHINE_FNL_MASTER rows for this SIZE~CLASS~MOC~DESIGN + TASK.", 404)

            by_wc = {_norm(r["WORK_CENTER"]): r for r in combo}
            requested = {_norm(i["work_center"]): i for i in items}
            if set(requested) != set(by_wc) or len(requested) != len(items):
                raise FnlAdminError(
                    "The request must list every machine of this combo exactly once. Refresh and try again.", 409)

            new_priorities = [int(i["priority"]) for i in items]
            if any(p < 1 for p in new_priorities) or len(set(new_priorities)) != len(new_priorities):
                raise FnlAdminError("Priorities must be unique positive integers (1 = most preferred).")

            for wc, item in requested.items():
                current = _json_value(by_wc[wc].get("PRIORITY"))
                if item.get("expected_priority") is not None and current != int(item["expected_priority"]):
                    raise FnlAdminError(
                        f"{wc} priority changed in Oracle since this page loaded "
                        f"(now {current}). Refresh and redo your edit.", 409)

            changed = 0
            stamp = _stamp(cols, now)
            for wc, item in requested.items():
                old_p = _json_value(by_wc[wc].get("PRIORITY"))
                new_p = int(item["priority"])
                if old_p == new_p:
                    continue
                sets = ["PRIORITY = :new_p"] + [f"{c} = :{c.lower()}" for c in stamp]
                cur.execute(
                    f"UPDATE {MASTER_TABLE} SET {', '.join(sets)} WHERE ROWID = :rid",
                    {"new_p": new_p, "rid": by_wc[wc]["RID"], **{c.lower(): v for c, v in stamp.items()}},
                )
                if cur.rowcount != 1:
                    raise FnlAdminError(f"Expected to update exactly 1 row for {wc}, got {cur.rowcount}.", 409)
                _insert_audit(cur, audit_id, now, changed_by, "PRIORITY", key, wc, old_p, new_p)
                changed += 1

            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"audit_id": audit_id, "rows_changed": changed}


def add_machine(
    size_inch, class_val, moc, design, task,
    work_center: str,
    priority: int,
    fixture_change_time: Optional[float] = None,
    locator_change_time: Optional[float] = None,
    load_unload_time: Optional[float] = None,
    changed_by: Optional[str] = None,
) -> dict:
    """
    Add `work_center` as a capable machine for an EXISTING combo+task at
    `priority`; machines at that priority or lower-preference shift down by 1
    (so priorities stay unique). Fixture/locator identity and timings come from
    the combo's most-preferred existing row unless overridden.
    """
    key = _key_from_request(size_inch, class_val, moc, design, task)
    wc_new = _norm(work_center)
    if not wc_new:
        raise FnlAdminError("work_center is required.")
    if wc_new not in set(known_machines()):
        raise FnlAdminError(
            f"{wc_new} has no rows in MCH_MACHINE_AVAILABILITY / _BY_DATE - the engine could never place work "
            "on it. Add its availability in the ERP first.")

    now = datetime.now()
    audit_id = uuid.uuid4().hex

    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _assert_audit_table(cur)
            cols, all_rows = _fetch_master(cur)
            combo = _combo_rows(all_rows, key)
            if not combo:
                raise FnlAdminError(
                    "No existing rows for this SIZE~CLASS~MOC~DESIGN + TASK. The Priority Shuffler only adds "
                    "machines to an existing combo - new combos are created in the ERP.", 404)
            if any(_norm(r["WORK_CENTER"]) == wc_new for r in combo):
                raise FnlAdminError(f"{wc_new} is already a capable machine for this combo+task.", 409)

            priority = int(priority)
            if priority < 1 or priority > len(combo) + 1:
                raise FnlAdminError(f"priority must be between 1 and {len(combo) + 1}.")

            stamp = _stamp(cols, now)

            # Shift existing machines at/below the insertion point down by one.
            for r in sorted(combo, key=lambda x: x["PRIORITY"], reverse=True):
                old_p = _json_value(r.get("PRIORITY"))
                if old_p is not None and old_p >= priority:
                    sets = ["PRIORITY = :new_p"] + [f"{c} = :{c.lower()}" for c in stamp]
                    cur.execute(
                        f"UPDATE {MASTER_TABLE} SET {', '.join(sets)} WHERE ROWID = :rid",
                        {"new_p": old_p + 1, "rid": r["RID"], **{c.lower(): v for c, v in stamp.items()}},
                    )
                    if cur.rowcount != 1:
                        raise FnlAdminError("Concurrent change detected while shifting priorities. Refresh.", 409)
                    _insert_audit(cur, audit_id, now, changed_by, "PRIORITY", key, _norm(r["WORK_CENTER"]),
                                  old_p, old_p + 1, f"shifted down by new machine {wc_new}")

            template = min(combo, key=lambda x: (x["PRIORITY"] is None, x["PRIORITY"]))
            new_row = {c: template[c] for c in cols if c != "RID"}
            new_row["WORK_CENTER"] = wc_new
            new_row["PRIORITY"] = priority
            for col, override in zip(TIMING_COLS, (fixture_change_time, locator_change_time, load_unload_time)):
                if override is not None and col in new_row:
                    new_row[col] = float(override)
            new_row.update(stamp)

            names = list(new_row)
            cur.execute(
                f"INSERT INTO {MASTER_TABLE} ({', '.join(names)}) VALUES ({', '.join(':v' + str(i) for i in range(len(names)))})",
                {f"v{i}": new_row[n] for i, n in enumerate(names)},
            )
            _insert_audit(cur, audit_id, now, changed_by, "ADD_MACHINE", key, wc_new, None, priority,
                          f"copied fixture/locator from {_norm(template['WORK_CENTER'])}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"audit_id": audit_id, "work_center": wc_new, "priority": priority}
