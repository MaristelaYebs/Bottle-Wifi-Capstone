"""Reward Repository

CRUD for RewardGrant: one grant per session (UNIQUE SessionId), PENDING/SUCCESS/
FAILED, with an ABSOLUTE TargetLimitSeconds so retries are idempotent.

Implemented in Phase 16. Interface: docs/architecture/02-low-level-architecture.md §8
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("repo.reward")


class RewardRepository:
    def __init__(self, db) -> None:
        self.db = db

    def _next_grant_id(self, date_str: str) -> str:
        prefix = f"GR-{date_str}-"
        row = self.db.raw.execute(
            "SELECT GrantId FROM RewardGrant WHERE GrantId LIKE ? ORDER BY Id DESC LIMIT 1",
            (prefix + "%",)).fetchone()
        nxt = 1
        if row:
            try:
                nxt = int(row["GrantId"].rsplit("-", 1)[1]) + 1
            except (ValueError, IndexError):
                nxt = 1
        return f"{prefix}{nxt:06d}"

    def get_by_session(self, session_id: int):
        return self.db.query_one("SELECT * FROM RewardGrant WHERE SessionId=?", (session_id,))

    def delete_by_session(self, session_id: int) -> None:
        """Drop grants for a session so a reopened (EXTEND) session grants anew
        instead of matching the previous cycle's already-SUCCESS grant."""
        self.db.execute("DELETE FROM RewardGrant WHERE SessionId=?", (session_id,))

    def get(self, grant_id: str):
        return self.db.query_one("SELECT * FROM RewardGrant WHERE GrantId=?", (grant_id,))

    def create(self, session_id, voucher, mac, ip, bottle_count, minutes,
               target_seconds, extend, now: Optional[datetime] = None) -> str:
        now = now or datetime.now()
        ts = now.isoformat(timespec="seconds")
        with self.db.lock:
            gid = self._next_grant_id(now.strftime("%Y%m%d"))
            self.db.raw.execute(
                "INSERT INTO RewardGrant (GrantId, SessionId, Voucher, Mac, Ip, BottleCount, "
                "MinutesGranted, TargetLimitSeconds, Extend, Status, Attempts, CreatedAt, UpdatedAt) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gid, session_id, voucher, mac, ip, bottle_count, minutes,
                 target_seconds, 1 if extend else 0, "PENDING", 0, ts, ts))
            self.db.raw.commit()
        return gid

    def list_pending(self) -> list:
        return self.db.query_all(
            "SELECT * FROM RewardGrant WHERE Status='PENDING' ORDER BY Id ASC")

    def mark_success(self, grant_id, now=None) -> None:
        now = now or datetime.now()
        self.db.execute(
            "UPDATE RewardGrant SET Status='SUCCESS', UpdatedAt=? WHERE GrantId=?",
            (now.isoformat(timespec="seconds"), grant_id))

    def update_attempt(self, grant_id, error, now=None) -> None:
        now = now or datetime.now()
        self.db.execute(
            "UPDATE RewardGrant SET Attempts=Attempts+1, LastError=?, UpdatedAt=? WHERE GrantId=?",
            (error, now.isoformat(timespec="seconds"), grant_id))

    def mark_failed(self, grant_id, error, now=None) -> None:
        now = now or datetime.now()
        self.db.execute(
            "UPDATE RewardGrant SET Status='FAILED', LastError=?, UpdatedAt=? WHERE GrantId=?",
            (error, now.isoformat(timespec="seconds"), grant_id))
