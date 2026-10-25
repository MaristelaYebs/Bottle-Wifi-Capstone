"""Database Bootstrap

SQLite connection factory (WAL, single writer) + schema create/migrate.
One shared connection guarded by a lock (several service threads touch the DB).

Implemented in Phase 10. Schema: docs/architecture/02-low-level-architecture.md §4
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS DetectionTransaction (
  Id                INTEGER PRIMARY KEY AUTOINCREMENT,
  TransactionId     TEXT NOT NULL UNIQUE,
  Timestamp         TEXT NOT NULL,
  SessionVoucher    TEXT,
  Prediction        TEXT NOT NULL,
  Confidence        REAL NOT NULL,
  PositiveFrames    INTEGER NOT NULL,
  TotalFrames       INTEGER NOT NULL,
  DetectionResult   TEXT NOT NULL CHECK (DetectionResult IN ('ACCEPT','REJECT')),
  ModelVersion      TEXT NOT NULL,
  ImagePath         TEXT,
  CreditedToSession INTEGER NOT NULL DEFAULT 0,
  ErrorMessage      TEXT,
  CreatedAt         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_txn_session ON DetectionTransaction(SessionVoucher);
CREATE INDEX IF NOT EXISTS ix_txn_time    ON DetectionTransaction(Timestamp);

CREATE TABLE IF NOT EXISTS PortalSession (
  Id               INTEGER PRIMARY KEY AUTOINCREMENT,
  Voucher          TEXT NOT NULL UNIQUE,
  Mac              TEXT, Ip TEXT,
  ExtendTime       INTEGER NOT NULL DEFAULT 0,
  State            TEXT NOT NULL CHECK (State IN
                     ('OPEN','FINALIZING','FINALIZED','CANCELLED','EXPIRED')),
  BottleCount      INTEGER NOT NULL DEFAULT 0,
  MinutesPerBottle INTEGER NOT NULL,
  MaxBottles       INTEGER NOT NULL,
  TimeAddedSeconds INTEGER NOT NULL DEFAULT 0,
  WaitTimeMs       INTEGER NOT NULL,
  StartedAt        TEXT NOT NULL, UpdatedAt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sess_mac   ON PortalSession(Mac);
CREATE INDEX IF NOT EXISTS ix_sess_state ON PortalSession(State);

CREATE TABLE IF NOT EXISTS RewardGrant (
  Id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  GrantId            TEXT NOT NULL UNIQUE,
  SessionId          INTEGER UNIQUE,
  Voucher            TEXT NOT NULL, Mac TEXT, Ip TEXT,
  BottleCount        INTEGER NOT NULL,
  MinutesGranted     INTEGER NOT NULL,
  TargetLimitSeconds INTEGER NOT NULL,
  Extend             INTEGER NOT NULL DEFAULT 0,
  Status             TEXT NOT NULL CHECK (Status IN ('PENDING','SUCCESS','FAILED')),
  Attempts           INTEGER NOT NULL DEFAULT 0,
  LastError          TEXT,
  CreatedAt          TEXT NOT NULL, UpdatedAt TEXT NOT NULL,
  FOREIGN KEY (SessionId) REFERENCES PortalSession(Id)
);
CREATE INDEX IF NOT EXISTS ix_grant_status ON RewardGrant(Status);

CREATE TABLE IF NOT EXISTS SystemEvent (
  Id INTEGER PRIMARY KEY AUTOINCREMENT,
  Timestamp TEXT NOT NULL,
  Severity TEXT NOT NULL,
  Source TEXT NOT NULL,
  EventType TEXT NOT NULL,
  Message TEXT NOT NULL,
  DetailsJson TEXT
);
CREATE INDEX IF NOT EXISTS ix_event_time ON SystemEvent(Timestamp);

CREATE TABLE IF NOT EXISTS ModelVersion (
  Id INTEGER PRIMARY KEY AUTOINCREMENT,
  Version TEXT NOT NULL UNIQUE,
  Stage TEXT NOT NULL,
  Active INTEGER NOT NULL DEFAULT 0,
  Precision REAL, Recall REAL, Map50 REAL,
  FalseAcceptanceRate REAL, FalseRejectionRate REAL,
  ValidatedAt TEXT, DeployedAt TEXT, Notes TEXT
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = str(path)
        self.lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def raw(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("database not connected")
        return self._conn

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self.lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        log.info("database ready: %s", self.path)
        return self._conn

    # simple guarded helpers
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.raw.execute(sql, tuple(params))
            self.raw.commit()
            return cur

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with self.lock:
            return self.raw.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list:
        with self.lock:
            return self.raw.execute(sql, tuple(params)).fetchall()

    def health_ok(self) -> bool:
        try:
            self.query_one("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        with self.lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    self._conn.commit()
                except Exception:  # noqa: BLE001
                    pass
                self._conn.close()
                self._conn = None
