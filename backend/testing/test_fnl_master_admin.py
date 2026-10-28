"""
Regression tests for fnl_master_admin.py (Priority Shuffler write path).

Runs against an in-memory SQLite database standing in for Oracle (SQLite has
ROWID and :named binds too), so the live ERP table is never touched. Only the
Oracle-specific bits (FETCH FIRST in read_audit, ORA-00942 detection) are not
exercised here.
"""

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import fnl_master_admin as admin

COMBO = ("8", "300", "WCB", "STD", "VB03")


def _make_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        """CREATE TABLE MCH_MACHINE_FNL_MASTER (
            COMPANY TEXT, SIZE_INCH TEXT, CLASS TEXT, MOC TEXT, DESIGN TEXT, COMBINATION TEXT,
            TASK TEXT, PRIORITY INTEGER, WORK_CENTER TEXT, FIXTURE_ID TEXT, LOCATOR_ID TEXT,
            FIXTURE_CHANGE_TIME REAL, LOCATOR_CHANGE_TIME REAL, LOAD_UNLOAD_TIME REAL,
            USER_ID TEXT, USER_DATE TEXT)"""
    )
    conn.execute(
        """CREATE TABLE MCH_FNL_MASTER_AUDIT (
            AUDIT_ID TEXT, CHANGED_AT TEXT, CHANGED_BY TEXT, ACTION TEXT, SIZE_INCH TEXT, CLASS TEXT,
            MOC TEXT, DESIGN TEXT, TASK TEXT, WORK_CENTER TEXT, OLD_PRIORITY INTEGER, NEW_PRIORITY INTEGER,
            NOTE TEXT)"""
    )
    rows = [
        ("VEL", *COMBO[:4], "x", "VB03", 1, "M1", "FX1", "LC1", 30, 10, 2, None, None),
        ("VEL", *COMBO[:4], "x", "VB03", 2, "M2", "FX1", "LC1", 31, 11, 3, None, None),
        ("VEL", *COMBO[:4], "x", "VB03", 3, "M3", "FX1", "LC1", 32, 12, 4, None, None),
        # a different combo that must never be touched
        ("VEL", "10", "300", "WCB", "STD", "x", "VB03", 1, "M1", "FX9", "LC9", 1, 1, 1, None, None),
    ]
    conn.executemany("INSERT INTO MCH_MACHINE_FNL_MASTER VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


@pytest.fixture
def db(monkeypatch):
    conn = _make_db()

    @contextmanager
    def fake_get_connection():
        yield conn

    monkeypatch.setattr(admin, "get_connection", fake_get_connection)
    monkeypatch.setattr(admin, "known_machines", lambda: ["M1", "M2", "M3", "M4"])
    return conn


def _prios(conn, size="8"):
    return dict(conn.execute(
        "SELECT WORK_CENTER, PRIORITY FROM MCH_MACHINE_FNL_MASTER WHERE SIZE_INCH=? AND TASK='VB03'", (size,)
    ).fetchall())


def _items(m1, m2, m3, expected=(1, 2, 3)):
    return [
        {"work_center": "M1", "priority": m1, "expected_priority": expected[0]},
        {"work_center": "M2", "priority": m2, "expected_priority": expected[1]},
        {"work_center": "M3", "priority": m3, "expected_priority": expected[2]},
    ]


def test_reorder_updates_only_that_combo_and_audits(db):
    result = admin.update_priorities(*COMBO, _items(3, 1, 2), changed_by="harish")
    assert result["rows_changed"] == 3
    assert _prios(db) == {"M1": 3, "M2": 1, "M3": 2}
    assert _prios(db, "10") == {"M1": 1}  # untouched combo
    audit = db.execute("SELECT WORK_CENTER, OLD_PRIORITY, NEW_PRIORITY, CHANGED_BY FROM MCH_FNL_MASTER_AUDIT").fetchall()
    assert sorted(audit) == [("M1", 1, 3, "harish"), ("M2", 2, 1, "harish"), ("M3", 3, 2, "harish")]


def test_unchanged_rows_are_not_rewritten_or_audited(db):
    result = admin.update_priorities(*COMBO, _items(1, 3, 2))
    assert result["rows_changed"] == 2
    assert db.execute("SELECT COUNT(*) FROM MCH_FNL_MASTER_AUDIT").fetchone()[0] == 2


def test_duplicate_priorities_rejected_and_nothing_written(db):
    with pytest.raises(admin.FnlAdminError):
        admin.update_priorities(*COMBO, _items(1, 1, 2))
    assert _prios(db) == {"M1": 1, "M2": 2, "M3": 3}
    assert db.execute("SELECT COUNT(*) FROM MCH_FNL_MASTER_AUDIT").fetchone()[0] == 0


def test_partial_machine_list_rejected(db):
    with pytest.raises(admin.FnlAdminError) as e:
        admin.update_priorities(*COMBO, _items(1, 2, 3)[:2])
    assert e.value.status == 409


def test_stale_expected_priority_rejected(db):
    with pytest.raises(admin.FnlAdminError) as e:
        admin.update_priorities(*COMBO, _items(3, 1, 2, expected=(2, 2, 3)))
    assert e.value.status == 409
    assert _prios(db) == {"M1": 1, "M2": 2, "M3": 3}


def test_unknown_combo_is_404(db):
    with pytest.raises(admin.FnlAdminError) as e:
        admin.update_priorities("99", "300", "WCB", "STD", "VB03", _items(1, 2, 3))
    assert e.value.status == 404


def test_add_machine_inserts_and_shifts_others_down(db):
    admin.add_machine(*COMBO, work_center="M4", priority=2, load_unload_time=9, changed_by="harish")
    assert _prios(db) == {"M1": 1, "M4": 2, "M2": 3, "M3": 4}
    row = db.execute(
        "SELECT FIXTURE_ID, LOCATOR_ID, FIXTURE_CHANGE_TIME, LOAD_UNLOAD_TIME, USER_ID FROM MCH_MACHINE_FNL_MASTER "
        "WHERE WORK_CENTER='M4'"
    ).fetchone()
    assert row == ("FX1", "LC1", 30, 9, "TOVMLO")  # copied from best-priority row, load/unload overridden
    actions = [a[0] for a in db.execute("SELECT ACTION FROM MCH_FNL_MASTER_AUDIT").fetchall()]
    assert actions.count("ADD_MACHINE") == 1 and actions.count("PRIORITY") == 2


def test_add_machine_validations(db):
    with pytest.raises(admin.FnlAdminError):
        admin.add_machine(*COMBO, work_center="M2", priority=1)  # already capable
    with pytest.raises(admin.FnlAdminError):
        admin.add_machine(*COMBO, work_center="NOPE", priority=1)  # no availability data
    with pytest.raises(admin.FnlAdminError):
        admin.add_machine(*COMBO, work_center="M4", priority=9)  # priority out of range
    with pytest.raises(admin.FnlAdminError) as e:
        admin.add_machine("99", "300", "WCB", "STD", "VB03", work_center="M4", priority=1)  # new combo
    assert e.value.status == 404
    assert _prios(db) == {"M1": 1, "M2": 2, "M3": 3}


def test_blank_moc_and_design_still_match(db):
    db.execute("UPDATE MCH_MACHINE_FNL_MASTER SET MOC=NULL, DESIGN=NULL WHERE SIZE_INCH='10'")
    db.commit()
    admin.update_priorities("10", "300", "", "", "VB03", [{"work_center": "M1", "priority": 2, "expected_priority": 1}])
    assert _prios(db, "10") == {"M1": 2}
