"""Shared Types

Dataclasses and enums shared across services: InboundEvent, Detection,
Decision, HealthReport, RewardResult.

Implemented in Phase 5. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    """Asynchronous events the orchestrator consumes from its queue.

    Note: solicited serial replies (PONG, ACK:*, SERVO_COMPLETE) are NOT events —
    they are awaited synchronously via SerialService response waiters.
    """

    ARDUINO_READY = "ARDUINO_READY"
    OBJECT_DETECTED = "OBJECT_DETECTED"
    ARDUINO_ERROR = "ARDUINO_ERROR"
    SERIAL_DOWN = "SERIAL_DOWN"
    SERIAL_UP = "SERIAL_UP"
    # Added in later phases: PORTAL_TOPUP, PORTAL_DONE, HEALTH_TICK, REWARD_RETRY_TICK
    SHUTDOWN = "SHUTDOWN"


@dataclass
class InboundEvent:
    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.monotonic)


@dataclass
class Detection:
    """One YOLO detection (Phase 7 populates these)."""

    class_id: int
    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2


@dataclass
class Decision:
    """Result of the decision engine (Phase 7)."""

    outcome: str  # 'ACCEPT' | 'REJECT'
    logical: str  # 'BOTTLE' | 'REJECT' | 'EMPTY'
    prediction: str  # 'plastic_bottle' | 'reject_object' | 'none'
    confidence: float
    positive_frames: int
    total_frames: int
    reasons: list[str] = field(default_factory=list)
    collect_sample: bool = False

    @classmethod
    def reject(cls, reason: str, logical: str = "EMPTY") -> "Decision":
        """Fail-safe REJECT used whenever detection is impossible/uncertain."""
        return cls(
            outcome="REJECT",
            logical=logical,
            prediction="none",
            confidence=0.0,
            positive_frames=0,
            total_frames=0,
            reasons=[reason],
            collect_sample=True,
        )


@dataclass
class HealthReport:
    camera: bool = False
    serial: bool = False
    model: bool = False
    database: bool = False
    mikrotik: bool = False
    ram_pct: float = 0.0
    swap_pct: float = 0.0
    cpu_temp_c: float = 0.0
    disk_free_mb: float = 0.0
    ts: float = field(default_factory=time.time)

    def is_ready(self) -> bool:
        """Critical subsystems; MikroTik/disk/RAM are degraded-not-fatal."""
        return self.camera and self.serial and self.model and self.database


@dataclass
class RewardResult:
    ok: bool
    status: str  # 'SUCCESS' | 'PENDING' | 'FAILED'
    minutes: int = 0
    detail: str = ""
