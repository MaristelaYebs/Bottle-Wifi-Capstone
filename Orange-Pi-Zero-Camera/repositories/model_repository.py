"""Model Repository

CRUD for the ModelVersion registry + metrics. Used by deploy/rollback (Phase 14)
to compare candidate vs production and to record what is live.

Implemented in Phase 13. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("repo.model")


class ModelRepository:
    def __init__(self, db) -> None:
        self.db = db

    def upsert(self, version: str, stage: str, *, metrics: Optional[dict] = None,
               notes: Optional[str] = None) -> None:
        """Insert or update a model row by Version. metrics keys (optional):
        precision, recall, map50, far, frr."""
        m = metrics or {}
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.lock:
            existing = self.db.raw.execute(
                "SELECT Id FROM ModelVersion WHERE Version=?", (version,)).fetchone()
            if existing:
                self.db.raw.execute(
                    "UPDATE ModelVersion SET Stage=?, Precision=?, Recall=?, Map50=?, "
                    "FalseAcceptanceRate=?, FalseRejectionRate=?, ValidatedAt=?, Notes=? "
                    "WHERE Version=?",
                    (stage, m.get("precision"), m.get("recall"), m.get("map50"),
                     m.get("far"), m.get("frr"), now, notes, version))
            else:
                self.db.raw.execute(
                    "INSERT INTO ModelVersion (Version, Stage, Active, Precision, Recall, "
                    "Map50, FalseAcceptanceRate, FalseRejectionRate, ValidatedAt, Notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (version, stage, 0, m.get("precision"), m.get("recall"), m.get("map50"),
                     m.get("far"), m.get("frr"), now, notes))
            self.db.raw.commit()

    def set_stage(self, version: str, stage: str) -> None:
        self.db.execute("UPDATE ModelVersion SET Stage=? WHERE Version=?", (stage, version))

    def set_active(self, version: str) -> None:
        """Mark exactly one version active (the deployed production model)."""
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.lock:
            self.db.raw.execute("UPDATE ModelVersion SET Active=0")
            self.db.raw.execute(
                "UPDATE ModelVersion SET Active=1, Stage='production', DeployedAt=? WHERE Version=?",
                (now, version))
            self.db.raw.commit()

    def get(self, version: str):
        return self.db.query_one("SELECT * FROM ModelVersion WHERE Version=?", (version,))

    def get_active(self):
        return self.db.query_one("SELECT * FROM ModelVersion WHERE Active=1 LIMIT 1")

    def by_stage(self, stage: str) -> list:
        return self.db.query_all(
            "SELECT * FROM ModelVersion WHERE Stage=? ORDER BY Id DESC", (stage,))

    def all(self) -> list:
        return self.db.query_all("SELECT * FROM ModelVersion ORDER BY Id DESC")
