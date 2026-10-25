"""Detection Repository

CRUD for DetectionTransaction: insert, mark credited, query by session/time.
Transaction IDs are TXN-YYYYMMDD-NNNNNN with a per-date counter derived from the
table itself (monotonic even if the clock is wrong — the Pi has no RTC).

Implemented in Phase 10. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("repo.detection")


class DetectionRepository:
    def __init__(self, db) -> None:
        self.db = db

    def _next_transaction_id(self, date_str: str) -> str:
        prefix = f"TXN-{date_str}-"
        row = self.db.raw.execute(
            "SELECT TransactionId FROM DetectionTransaction "
            "WHERE TransactionId LIKE ? ORDER BY Id DESC LIMIT 1",
            (prefix + "%",),
        ).fetchone()
        nxt = 1
        if row:
            try:
                nxt = int(row["TransactionId"].rsplit("-", 1)[1]) + 1
            except (ValueError, IndexError):
                nxt = 1
        return f"{prefix}{nxt:06d}"

    def insert(self, decision, model_version: str, *, session_voucher: Optional[str] = None,
               image_path: Optional[str] = None, now: Optional[datetime] = None) -> str:
        """Record a decision. Returns the generated TransactionId.

        ID generation + insert happen under one lock so the per-date counter is
        race-free (single-writer discipline)."""
        now = now or datetime.now()
        date_str = now.strftime("%Y%m%d")
        ts = now.isoformat(timespec="seconds")
        with self.db.lock:
            txn_id = self._next_transaction_id(date_str)
            self.db.raw.execute(
                "INSERT INTO DetectionTransaction "
                "(TransactionId, Timestamp, SessionVoucher, Prediction, Confidence, "
                " PositiveFrames, TotalFrames, DetectionResult, ModelVersion, ImagePath, "
                " CreditedToSession, ErrorMessage, CreatedAt) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (txn_id, ts, session_voucher, decision.prediction, float(decision.confidence),
                 int(decision.positive_frames), int(decision.total_frames), decision.outcome,
                 model_version, image_path, 0, None, ts),
            )
            self.db.raw.commit()
        return txn_id

    def get(self, txn_id: str):
        return self.db.query_one(
            "SELECT * FROM DetectionTransaction WHERE TransactionId=?", (txn_id,))

    def mark_credited(self, txn_id: str) -> None:
        self.db.execute(
            "UPDATE DetectionTransaction SET CreditedToSession=1 WHERE TransactionId=?",
            (txn_id,))

    def set_session_voucher(self, txn_id: str, voucher: str) -> None:
        self.db.execute(
            "UPDATE DetectionTransaction SET SessionVoucher=? WHERE TransactionId=?",
            (voucher, txn_id))

    def set_error(self, txn_id: str, message: str) -> None:
        self.db.execute(
            "UPDATE DetectionTransaction SET ErrorMessage=? WHERE TransactionId=?",
            (message, txn_id))

    def set_image_path(self, txn_id: str, path: str) -> None:
        self.db.execute(
            "UPDATE DetectionTransaction SET ImagePath=? WHERE TransactionId=?",
            (path, txn_id))

    def recent(self, limit: int = 20) -> list:
        return self.db.query_all(
            "SELECT * FROM DetectionTransaction ORDER BY Id DESC LIMIT ?", (limit,))

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM DetectionTransaction")
        return int(row["n"]) if row else 0
