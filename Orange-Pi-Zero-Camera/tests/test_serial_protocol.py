"""Phase 5 unit tests — serial protocol logic (no hardware, no pyserial needed).

Run standalone:  python tests/test_serial_protocol.py
Or with pytest:  pytest tests/test_serial_protocol.py
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.types import EventKind, InboundEvent  # noqa: E402
from services.serial_service import SerialService, classify_line  # noqa: E402


class FakeCfg:
    """Returns config defaults for every key."""

    def get(self, key, default=None):
        return default


class LoopbackTransport:
    """Simulates the Arduino: a written command yields mapped response lines."""

    def __init__(self, mapping):
        self.mapping = mapping
        self._out = collections.deque()
        self._lock = threading.Lock()
        self.writes = []
        self.closed = False

    def write(self, data: bytes) -> int:
        cmd = data.decode("ascii").strip()
        self.writes.append(cmd)
        with self._lock:
            for resp in self.mapping.get(cmd, []):
                self._out.append((resp + "\n").encode("ascii"))
        return len(data)

    def readline(self) -> bytes:
        with self._lock:
            if self._out:
                return self._out.popleft()
        time.sleep(0.005)
        return b""

    def push(self, line: str) -> None:
        with self._lock:
            self._out.append((line + "\n").encode("ascii"))

    def close(self) -> None:
        self.closed = True


DEFAULT_MAP = {
    "PING": ["PONG"],
    "ACCEPT": ["ACK:ACCEPT", "SERVO_COMPLETE"],
    "REJECT": ["ACK:REJECT", "SERVO_COMPLETE"],
    "RESET": ["ACK:RESET"],
}


def _make_service(mapping=None):
    events = []
    lock = threading.Lock()

    def on_event(ev: InboundEvent):
        with lock:
            events.append(ev)

    transport = LoopbackTransport(DEFAULT_MAP if mapping is None else mapping)
    svc = SerialService(FakeCfg(), on_event, transport_factory=lambda: transport)
    assert svc.connect() is True
    svc.start_rx()
    return svc, transport, events, lock


# ----------------------- classify_line (pure) -----------------------
def test_classify_line():
    assert classify_line("")[0] == "ignore"
    assert classify_line("# debug note")[0] == "ignore"
    assert classify_line("garbage tokens")[0] == "ignore"
    assert classify_line("PONG") == ("response", "PONG")
    assert classify_line("SERVO_COMPLETE") == ("response", "SERVO_COMPLETE")
    assert classify_line("ACK:ACCEPT") == ("response", "ACK:ACCEPT")

    kind, ev = classify_line("READY")
    assert kind == "event" and ev.kind == EventKind.ARDUINO_READY
    kind, ev = classify_line("OBJECT_DETECTED")
    assert kind == "event" and ev.kind == EventKind.OBJECT_DETECTED
    kind, ev = classify_line("ERROR:AI_TIMEOUT")
    assert kind == "event" and ev.kind == EventKind.ARDUINO_ERROR
    assert ev.payload["message"] == "AI_TIMEOUT"
    print("ok: classify_line")


# ----------------------- ping -----------------------
def test_ping():
    svc, _t, _e, _l = _make_service()
    try:
        assert svc.ping(timeout_ms=1000) is True
    finally:
        svc.close()
    print("ok: ping/PONG")


# ----------------------- accept round trip -----------------------
def test_accept_round_trip():
    svc, transport, _e, _l = _make_service()
    try:
        assert svc.send_and_wait_ack("ACCEPT", "ACK:ACCEPT") is True
        assert svc.wait_for("SERVO_COMPLETE", timeout_ms=1000) is True
        assert "ACCEPT" in transport.writes
    finally:
        svc.close()
    print("ok: ACCEPT -> ACK:ACCEPT -> SERVO_COMPLETE")


# ----------------------- reject round trip -----------------------
def test_reject_round_trip():
    svc, _t, _e, _l = _make_service()
    try:
        assert svc.send_and_wait_ack("REJECT", "ACK:REJECT") is True
        assert svc.wait_for("SERVO_COMPLETE", timeout_ms=1000) is True
    finally:
        svc.close()
    print("ok: REJECT -> ACK:REJECT -> SERVO_COMPLETE")


# ----------------------- ack timeout + retry -----------------------
def test_ack_timeout():
    svc, _t, _e, _l = _make_service(mapping={})  # Arduino never answers
    try:
        t0 = time.monotonic()
        ok = svc.send_and_wait_ack("REJECT", "ACK:REJECT", timeout_ms=80, retries=2)
        elapsed = time.monotonic() - t0
        assert ok is False
        # 3 attempts * 80ms ~= 240ms (allow slack)
        assert 0.2 <= elapsed <= 1.0, elapsed
    finally:
        svc.close()
    print("ok: ack timeout retries then fails")


# ----------------------- unsolicited events -----------------------
def test_events_delivered():
    svc, transport, events, lock = _make_service()
    try:
        transport.push("OBJECT_DETECTED")
        transport.push("READY")
        transport.push("ERROR:AI_TIMEOUT")
        transport.push("# ignored debug")
        transport.push("random noise")
        time.sleep(0.2)
        with lock:
            kinds = [e.kind for e in events]
        assert EventKind.OBJECT_DETECTED in kinds
        assert EventKind.ARDUINO_READY in kinds
        assert EventKind.ARDUINO_ERROR in kinds
        # comment + noise must NOT produce events
        assert len(kinds) == 3, kinds
    finally:
        svc.close()
    print("ok: unsolicited events routed, noise ignored")


def _run_all():
    test_classify_line()
    test_ping()
    test_accept_round_trip()
    test_reject_round_trip()
    test_ack_timeout()
    test_events_delivered()
    print("\nALL PHASE 5 SERIAL TESTS PASSED")


if __name__ == "__main__":
    _run_all()
