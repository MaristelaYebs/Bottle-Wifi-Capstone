"""Application State

Thread-safe shared state: health, active portal session, and the single-flight
detection lock. SQLite is the durable source of truth; this is a cache.

Implemented in Phase 5. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from core.types import HealthReport


class AppState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._health = HealthReport()
        self._serial_connected = False
        self._camera_ok = True          # last-known camera status (updated by pipeline)
        self._mikrotik_ok = False
        self._detection_busy = False
        self._detection_armed = False   # chute watcher is scanning the chute
        self._active_session: Optional[Any] = None  # PortalSession (Phase 16)
        self._running = True

    # --- serial link ---
    @property
    def serial_connected(self) -> bool:
        with self._lock:
            return self._serial_connected

    @serial_connected.setter
    def serial_connected(self, value: bool) -> None:
        with self._lock:
            self._serial_connected = value

    @property
    def camera_ok(self) -> bool:
        with self._lock:
            return self._camera_ok

    @camera_ok.setter
    def camera_ok(self, value: bool) -> None:
        with self._lock:
            self._camera_ok = value

    @property
    def mikrotik_ok(self) -> bool:
        with self._lock:
            return self._mikrotik_ok

    @mikrotik_ok.setter
    def mikrotik_ok(self, value: bool) -> None:
        with self._lock:
            self._mikrotik_ok = value

    # --- single-flight detection lock ---
    def try_begin_detection(self) -> bool:
        """Atomically claim the detection slot. Returns False if already busy."""
        with self._lock:
            if self._detection_busy:
                return False
            self._detection_busy = True
            return True

    def end_detection(self) -> None:
        with self._lock:
            self._detection_busy = False

    @property
    def detection_busy(self) -> bool:
        with self._lock:
            return self._detection_busy

    # --- continuous chute watch (camera-triggered detection) ---
    @property
    def detection_armed(self) -> bool:
        with self._lock:
            return self._detection_armed

    @detection_armed.setter
    def detection_armed(self, value: bool) -> None:
        with self._lock:
            self._detection_armed = bool(value)

    # --- health snapshot ---
    def set_health(self, report: HealthReport) -> None:
        with self._lock:
            self._health = report

    def get_health(self) -> HealthReport:
        with self._lock:
            return self._health

    # --- active portal session (Phase 16) ---
    @property
    def active_session(self) -> Optional[Any]:
        with self._lock:
            return self._active_session

    @active_session.setter
    def active_session(self, session: Optional[Any]) -> None:
        with self._lock:
            self._active_session = session

    # --- lifecycle ---
    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def stop(self) -> None:
        with self._lock:
            self._running = False
