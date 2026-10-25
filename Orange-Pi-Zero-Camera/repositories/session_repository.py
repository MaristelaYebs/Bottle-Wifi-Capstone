"""Session Repository

CRUD for PortalSession (the JuanFi "coin-slot" session): open, credit, finalize,
reap stale. One OPEN session at a time (single chute).

Implemented in Phase 16. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("repo.session")


class SessionRepository:
    def __init__(self, db) -> None:
        self.db = db

    def open(self, voucher, mac, ip, extend, minutes, max_bottles, wait_ms,
             now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        ts = now.isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO PortalSession (Voucher, Mac, Ip, ExtendTime, State, BottleCount, "
            "MinutesPerBottle, MaxBottles, TimeAddedSeconds, WaitTimeMs, StartedAt, UpdatedAt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (voucher, mac, ip, 1 if extend else 0, "OPEN", 0, minutes, max_bottles, 0,
             wait_ms, ts, ts))
        return self.get_by_voucher(voucher)

    def reopen(self, voucher, mac, ip, extend, minutes, max_bottles, wait_ms,
               now: Optional[datetime] = None) -> dict:
        """Reset an existing session row to a fresh OPEN state (counts to 0),
        keeping its Id and Voucher. Used for EXTEND-time, where the voucher IS
        the live hotspot username and must be kept, so we cannot INSERT a new
        row (UNIQUE) nor change the code. The caller clears the old RewardGrant
        so the extend grant adds new time on top of the remaining."""
        now = now or datetime.now()
        ts = now.isoformat(timespec="seconds")
        self.db.execute(
            "UPDATE PortalSession SET Mac=?, Ip=?, ExtendTime=?, State='OPEN', "
            "BottleCount=0, MinutesPerBottle=?, MaxBottles=?, TimeAddedSeconds=0, "
            "WaitTimeMs=?, StartedAt=?, UpdatedAt=? WHERE Voucher=?",
            (mac, ip, 1 if extend else 0, minutes, max_bottles, wait_ms, ts, ts, voucher))
        return self.get_by_voucher(voucher)

    def get_by_voucher(self, voucher: str):
        return self.db.query_one("SELECT * FROM PortalSession WHERE Voucher=?", (voucher,))

    def active(self):
        return self.db.query_one(
            "SELECT * FROM PortalSession WHERE State='OPEN' ORDER BY Id DESC LIMIT 1")

    def open_by_mac(self, mac: str):
        if not mac:
            return None
        return self.db.query_one(
            "SELECT * FROM PortalSession WHERE State='OPEN' AND Mac=? ORDER BY Id DESC LIMIT 1",
            (mac,))

    def update_counts(self, voucher, bottle_count, time_added, now=None) -> None:
        now = now or datetime.now()
        self.db.execute(
            "UPDATE PortalSession SET BottleCount=?, TimeAddedSeconds=?, UpdatedAt=? WHERE Voucher=?",
            (bottle_count, time_added, now.isoformat(timespec="seconds"), voucher))

    def set_state(self, voucher, state, now=None) -> None:
        now = now or datetime.now()
        self.db.execute(
            "UPDATE PortalSession SET State=?, UpdatedAt=? WHERE Voucher=?",
            (state, now.isoformat(timespec="seconds"), voucher))

    def reap_stale(self, stale_ms: int, now=None) -> int:
        now = now or datetime.now()
        cutoff = (now - timedelta(milliseconds=stale_ms)).isoformat(timespec="seconds")
        cur = self.db.execute(
            "UPDATE PortalSession SET State='EXPIRED' WHERE State='OPEN' AND UpdatedAt < ?",
            (cutoff,))
        return cur.rowcount if cur else 0
