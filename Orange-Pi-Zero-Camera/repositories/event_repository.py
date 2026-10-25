"""Event Repository

Append-only SystemEvent audit log.

Implemented in Phase 10. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("repo.event")


class EventRepository:
    def __init__(self, db) -> None:
        self.db = db

    def add(self, severity: str, source: str, event_type: str, message: str,
            details: Optional[dict] = None) -> None:
        try:
            self.db.execute(
                "INSERT INTO SystemEvent (Timestamp, Severity, Source, EventType, Message, DetailsJson) "
                "VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), severity, source,
                 event_type, message, json.dumps(details) if details else None),
            )
        except Exception as exc:  # noqa: BLE001 - audit logging must never break the pipeline
            log.error("failed to write SystemEvent: %s", exc)

    def recent(self, limit: int = 50) -> list:
        return self.db.query_all(
            "SELECT * FROM SystemEvent ORDER BY Id DESC LIMIT ?", (limit,))
