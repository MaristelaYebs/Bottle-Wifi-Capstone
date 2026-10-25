"""Phase 10 unit tests — SQLite schema, repositories, transaction IDs.

Pure stdlib sqlite3 (in-memory). Run:  python tests/test_database.py  (or pytest)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.types import Decision  # noqa: E402
from repositories.db import Database  # noqa: E402
from repositories.detection_repository import DetectionRepository  # noqa: E402
from repositories.event_repository import EventRepository  # noqa: E402


def _db():
    db = Database(":memory:")
    db.connect()
    return db


def _decision(outcome="ACCEPT", pred="plastic_bottle", conf=0.91, pos=5, total=5):
    return Decision(outcome=outcome, logical="BOTTLE" if outcome == "ACCEPT" else "EMPTY",
                    prediction=pred, confidence=conf, positive_frames=pos,
                    total_frames=total, reasons=["test"], collect_sample=False)


def test_schema_tables_exist():
    db = _db()
    rows = db.query_all("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    for t in ("DetectionTransaction", "PortalSession", "RewardGrant",
              "SystemEvent", "ModelVersion"):
        assert t in names, (t, names)
    print("ok: all 5 tables created")


def test_insert_and_read():
    db = _db()
    repo = DetectionRepository(db)
    txn = repo.insert(_decision(), "bottle-v1")
    assert txn.startswith("TXN-")
    row = repo.get(txn)
    assert row["DetectionResult"] == "ACCEPT"
    assert row["Prediction"] == "plastic_bottle"
    assert abs(row["Confidence"] - 0.91) < 1e-6
    assert row["ModelVersion"] == "bottle-v1"
    assert row["CreditedToSession"] == 0
    print("ok: insert + read round trip")


def test_transaction_id_monotonic_same_day():
    db = _db()
    repo = DetectionRepository(db)
    day = datetime(2026, 9, 18, 10, 0, 0)
    ids = [repo.insert(_decision(), "v", now=day) for _ in range(3)]
    assert ids == ["TXN-20260918-000001", "TXN-20260918-000002", "TXN-20260918-000003"], ids
    print("ok: transaction IDs monotonic within a day")


def test_transaction_id_new_day_resets():
    db = _db()
    repo = DetectionRepository(db)
    a = repo.insert(_decision(), "v", now=datetime(2026, 9, 18, 23, 59))
    b = repo.insert(_decision(), "v", now=datetime(2026, 9, 19, 0, 1))
    assert a == "TXN-20260918-000001"
    assert b == "TXN-20260919-000001"
    print("ok: counter resets on a new date")


def test_unique_constraint_backstop():
    db = _db()
    repo = DetectionRepository(db)
    repo.insert(_decision(), "v", now=datetime(2026, 9, 18, 10, 0))
    # force a duplicate id insert directly -> must raise (UNIQUE)
    try:
        db.raw.execute(
            "INSERT INTO DetectionTransaction (TransactionId,Timestamp,Prediction,Confidence,"
            "PositiveFrames,TotalFrames,DetectionResult,ModelVersion,CreditedToSession,CreatedAt) "
            "VALUES ('TXN-20260918-000001','t','none',0,0,0,'REJECT','v',0,'t')")
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    assert raised, "duplicate TransactionId should violate UNIQUE"
    print("ok: UNIQUE(TransactionId) backstop enforced")


def test_mark_credited_and_error():
    db = _db()
    repo = DetectionRepository(db)
    txn = repo.insert(_decision(), "v")
    repo.mark_credited(txn)
    repo.set_error(txn, "boom")
    row = repo.get(txn)
    assert row["CreditedToSession"] == 1
    assert row["ErrorMessage"] == "boom"
    print("ok: mark_credited + set_error")


def test_event_log():
    db = _db()
    ev = EventRepository(db)
    ev.add("INFO", "test", "unit", "hello", {"k": 1})
    ev.add("ERROR", "test", "unit", "bad")
    rows = ev.recent(10)
    assert len(rows) == 2
    assert rows[0]["Severity"] == "ERROR"
    print("ok: SystemEvent append + read")


def _run_all():
    test_schema_tables_exist()
    test_insert_and_read()
    test_transaction_id_monotonic_same_day()
    test_transaction_id_new_day_resets()
    test_unique_constraint_backstop()
    test_mark_credited_and_error()
    test_event_log()
    print("\nALL PHASE 10 DATABASE TESTS PASSED")


if __name__ == "__main__":
    _run_all()
