"""Integration test — camera-triggered detection workflow, end to end.

Fake serial transport (auto-answers ACK + SERVO_COMPLETE) + fake streaming
camera + MockDetector + real DecisionService + real ChuteWatcher, so the full
scan -> window agrees -> decide -> ACCEPT/REJECT -> SERVO_COMPLETE loop runs
with no hardware and no IR sensor.

Run:  python tests/test_pipeline_integration.py   (or pytest)
"""

from __future__ import annotations

import collections
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import MikroTikSecrets  # noqa: E402
from core.state import AppState  # noqa: E402
from core.types import Detection  # noqa: E402
from repositories.db import Database  # noqa: E402
from services.decision_service import DecisionService  # noqa: E402
from services.detector_service import MockDetector  # noqa: E402
import main as app  # noqa: E402

log = logging.getLogger("test")


class FakeCfg:
    def __init__(self, **over):
        self._d = {
            "detection.confidence": 0.70,
            "detection.required_positive_frames": 3,
            "detection.borderline_margin": 0.15,
            "detection.total_frames": 5,
            "detection.frame_delay_ms": 0,
            "detection.continuous": True,
            "detection.always_on": False,
            "detection.scan_interval_ms": 0,
            "detection.arm_poll_ms": 10,
            "detection.required_clear_frames": 2,
            "detection.cooldown_ms": 0,
            "serial.baudrate": 115200,
            "serial.ack_timeout_ms": 500,
            "serial.ack_retries": 2,
            "serial.servo_complete_timeout_ms": 2000,
            "reward.minutes_per_bottle": 15,
            "reward.max_bottles_per_session": 5,
            "reward.wait_time_ms": 90000,
            "reward.session_stale_ms": 180000,
        }
        self._d.update(over)
        self.mikrotik = MikroTikSecrets()  # unconfigured -> reward no-ops in tests

    def get(self, key, default=None):
        return self._d.get(key, default)


class LoopbackTransport:
    MAP = {
        "PING": ["PONG"],
        "ACCEPT": ["ACK:ACCEPT", "SERVO_COMPLETE"],
        "REJECT": ["ACK:REJECT", "SERVO_COMPLETE"],
        "RESET": ["ACK:RESET"],
    }

    def __init__(self, mapping=None):
        self._out = collections.deque()
        self._lock = threading.Lock()
        self.writes = []
        self._map = mapping if mapping is not None else self.MAP

    def write(self, data: bytes) -> int:
        cmd = data.decode("ascii").strip()
        self.writes.append(cmd)
        with self._lock:
            for r in self._map.get(cmd, []):
                self._out.append((r + "\n").encode("ascii"))
        return len(data)

    def readline(self) -> bytes:
        with self._lock:
            if self._out:
                return self._out.popleft()
        time.sleep(0.005)
        return b""

    def close(self):
        pass


class FakeStreamCamera:
    """Opens once and hands out frames, like the real camera does while armed."""

    def __init__(self, frames_before_failure=None):
        self._open = False
        self.opens = 0
        self.reads = 0
        self._budget = frames_before_failure   # None = endless

    def open(self):
        self._open = True
        self.opens += 1
        return True

    def is_open(self):
        return self._open

    def release(self):
        self._open = False

    def read_frame(self, flush=0):
        if not self._open:
            return None
        self.reads += 1
        if self._budget is not None and self.reads > self._budget:
            return None
        return object()

    def capture_burst(self, n=None, gap_ms=None):   # only used by /detect
        return [object() for _ in range(n or 5)]


class FakeDataset:
    def __init__(self):
        self.saved = []

    def maybe_save(self, frames, decision, model_version, txn_id, now=None):
        if getattr(decision, "collect_sample", False):
            self.saved.append(txn_id)
            return f"/tmp/{txn_id}.jpg"
        return None


def bottle(conf):
    return Detection(0, "plastic_bottle", conf, (0, 0, 10, 10))


def _mem_db():
    db = Database(":memory:")
    db.connect()
    return db


def _session(count=0, cap=5, state="OPEN"):
    return {"State": state, "BottleCount": count, "MaxBottles": cap, "Voucher": "TESTVCHR"}


def _make_orch(detector, camera=None, transport=None, cfg=None, session=None):
    cfg = cfg or FakeCfg()
    state = AppState()
    transport = transport or LoopbackTransport()
    orch = app.Orchestrator(
        cfg, state, log,
        serial_transport_factory=lambda: transport,
        camera=camera or FakeStreamCamera(),
        detector=detector,
        decision=DecisionService(cfg),
        db=_mem_db(),
        dataset=FakeDataset(),
    )
    # No real portal session in these tests: feed the watcher a fixed one.
    orch.watcher.session_provider = lambda: session if session is not None else _session()
    orch.start(background=False)       # background=False -> watcher thread not started
    return orch, transport


def _scan(orch, n):
    """Drive the watcher deterministically instead of racing its thread."""
    assert orch.watcher._arm() is True
    for _ in range(n):
        orch.watcher._scan_once()


def test_bottle_accepts():
    orch, transport = _make_orch(MockDetector(frames=[[bottle(0.92)]]))
    try:
        _scan(orch, 3)
        assert "ACCEPT" in transport.writes, transport.writes
        assert "REJECT" not in transport.writes
        assert orch.detections.count() == 1
        row = orch.detections.recent(1)[0]
        assert row["DetectionResult"] == "ACCEPT"
        assert row["TransactionId"].startswith("TXN-")
    finally:
        orch.shutdown()
    print("ok: 3 agreeing bottle frames -> ACCEPT -> SERVO_COMPLETE (+txn recorded)")


def test_empty_chute_does_not_actuate():
    # No IR trigger any more: an empty chute is the idle state, not a reject.
    orch, transport = _make_orch(MockDetector(frames=None))
    try:
        _scan(orch, 10)
        assert "ACCEPT" not in transport.writes, transport.writes
        assert "REJECT" not in transport.writes, transport.writes
        assert orch.detections.count() == 0
    finally:
        orch.shutdown()
    print("ok: empty chute -> keep watching, servo untouched")


def test_reject_object_rejects():
    det = MockDetector(frames=[[Detection(1, "reject_object", 0.85, (0, 0, 5, 5))]])
    orch, transport = _make_orch(det)
    try:
        _scan(orch, 3)
        assert "REJECT" in transport.writes
        assert "ACCEPT" not in transport.writes
    finally:
        orch.shutdown()
    print("ok: 3 agreeing reject_object frames -> REJECT")


def test_below_gate_never_accepts():
    orch, transport = _make_orch(MockDetector(frames=[[bottle(0.55)]]))
    try:
        _scan(orch, 10)
        assert "ACCEPT" not in transport.writes, transport.writes
    finally:
        orch.shutdown()
    print("ok: bottle below the confidence gate -> no accept")


def test_one_bottle_counts_once():
    """The bottle stays in frame after the flap opens — it must not score twice
    until the chute has been seen clear again."""
    det = MockDetector(frames=[[bottle(0.92)]])
    orch, transport = _make_orch(det)
    try:
        _scan(orch, 3)
        assert transport.writes.count("ACCEPT") == 1
        orch.watcher._scan_once()      # same bottle still visible
        orch.watcher._scan_once()
        orch.watcher._scan_once()
        assert transport.writes.count("ACCEPT") == 1, transport.writes
        assert orch.detections.count() == 1
        # now the chute clears, and a second bottle can be scored
        det._frames = [[]]
        for _ in range(3):
            orch.watcher._scan_once()
        det._frames = [[bottle(0.95)]]
        for _ in range(3):
            orch.watcher._scan_once()
        assert transport.writes.count("ACCEPT") == 2, transport.writes
    finally:
        orch.shutdown()
    print("ok: one bottle = one credit (clear frames required before re-arming)")


def test_reject_fires_between_bottles_without_empty_gap():
    """A reject item inserted right after an accepted bottle (no empty gap yet)
    must still be rejected — rejects are fail-safe and not gated by the
    accept-lock that guards against double-crediting the same bottle."""
    det = MockDetector(frames=[[bottle(0.95)]])
    orch, transport = _make_orch(det)
    try:
        _scan(orch, 3)                                  # accept one bottle
        assert transport.writes.count("ACCEPT") == 1
        assert orch.watcher._accept_locked is True
        transport.writes.clear()
        det._frames = [[Detection(1, "reject_object", 0.95, (0, 0, 5, 5))]]
        for _ in range(3):                              # reject item, no empty gap
            orch.watcher._scan_once()
        assert "REJECT" in transport.writes, transport.writes
        assert "ACCEPT" not in transport.writes
    finally:
        orch.shutdown()
    print("ok: reject between bottles fires without needing an empty gap")


def test_camera_failure_does_not_actuate():
    cam = FakeStreamCamera(frames_before_failure=0)
    orch, transport = _make_orch(MockDetector(frames=[[bottle(0.92)]]), camera=cam)
    try:
        assert orch.watcher._arm() is True
        orch.watcher._scan_once()
        orch.watcher._scan_once()
        assert "ACCEPT" not in transport.writes
        assert "REJECT" not in transport.writes
    finally:
        orch.shutdown()
    print("ok: dead camera -> no verdict, no servo")


def test_detector_exception_survives():
    class RaisingDetector:
        def load(self): return True
        def warmup(self): pass
        def is_loaded(self): return True
        def version(self): return "boom"
        def infer(self, frame):
            raise RuntimeError("inference blew up")

    orch, transport = _make_orch(RaisingDetector())
    try:
        assert orch.watcher._arm() is True
        raised = False
        try:
            orch.watcher._scan_once()
        except RuntimeError:
            raised = True               # run() catches it; record that it bubbled
        assert raised
        assert "ACCEPT" not in transport.writes
        assert "REJECT" not in transport.writes
    finally:
        orch.shutdown()
    print("ok: detector exception -> no blind actuation (watcher loop catches it)")


def test_watcher_disarms_without_session():
    cam = FakeStreamCamera()
    orch, _ = _make_orch(MockDetector(frames=[[bottle(0.92)]]), camera=cam)
    try:
        orch.watcher.session_provider = lambda: None
        assert orch.watcher.is_armed() is False
        orch.watcher.session_provider = lambda: _session(state="CANCELLED")
        assert orch.watcher.is_armed() is False
        orch.watcher.session_provider = lambda: _session()
        assert orch.watcher.is_armed() is True
        # At the bottle cap the watcher STAYS armed, so an extra bottle can be
        # physically rejected rather than ignored in the chute.
        orch.watcher.session_provider = lambda: _session(count=5, cap=5)
        assert orch.watcher.is_armed() is True
    finally:
        orch.shutdown()
    print("ok: watcher arms for any open session, incl. at the bottle cap")


def test_max_bottles_rejects_and_does_not_credit():
    orch, transport = _make_orch(MockDetector(frames=[[bottle(0.95)]]))
    try:
        s, err = orch.session_mgr.begin("", "AA:BB:CC:DD:EE:09", "10.0.0.9", False)
        assert err is None
        v = s["Voucher"]
        for _ in range(5):                       # fill to the cap
            orch.session_mgr.credit_bottle(v)
        assert orch.session_mgr.active()["BottleCount"] == 5
        transport.writes.clear()
        accept = orch.decision.decide([[bottle(0.95)]] * 5)   # a clean ACCEPT
        assert accept.outcome == "ACCEPT"
        orch._handle_decision(accept, [])
        assert "REJECT" in transport.writes, transport.writes
        assert "ACCEPT" not in transport.writes
        assert orch.session_mgr.active()["BottleCount"] == 5   # not credited past cap
        assert orch.detections.recent(1)[0]["ErrorMessage"] == "session_full"
    finally:
        orch.shutdown()
    print("ok: at max bottles an extra ACCEPT is rejected (servo) and not credited")


def test_watcher_releases_camera_when_disarmed():
    cam = FakeStreamCamera()
    orch, _ = _make_orch(MockDetector(frames=[[bottle(0.92)]]), camera=cam)
    try:
        assert orch.watcher._arm() is True
        assert cam.is_open() is True
        assert orch.state.detection_busy is True     # preview is locked out
        orch.watcher._disarm()
        assert cam.is_open() is False
        assert orch.state.detection_busy is False    # preview usable again
    finally:
        orch.shutdown()
    print("ok: disarming releases the camera and the single-flight lock")


def test_actuation_failure_recovers_with_reset():
    # Arduino ACKs ACCEPT but never sends SERVO_COMPLETE -> Pi must RESET to recover.
    cfg = FakeCfg(**{"serial.servo_complete_timeout_ms": 200,
                     "serial.ack_timeout_ms": 200, "serial.ack_retries": 1})
    transport = LoopbackTransport(mapping={
        "PING": ["PONG"],
        "RESET": ["ACK:RESET"],
        "ACCEPT": ["ACK:ACCEPT"],  # deliberately NO SERVO_COMPLETE
    })
    orch, _ = _make_orch(MockDetector(frames=[[bottle(0.92)]]),
                         transport=transport, cfg=cfg)
    try:
        transport.writes.clear()  # ignore startup PING/RESET
        _scan(orch, 3)
        assert "ACCEPT" in transport.writes
        assert "RESET" in transport.writes, "expected RESET recovery after missing SERVO_COMPLETE"
        assert orch.detections.recent(1)[0]["ErrorMessage"] == "actuation_failed"
    finally:
        orch.shutdown()
    print("ok: missing SERVO_COMPLETE -> RESET recovery")


def _run_all():
    test_bottle_accepts()
    test_empty_chute_does_not_actuate()
    test_reject_object_rejects()
    test_below_gate_never_accepts()
    test_one_bottle_counts_once()
    test_reject_fires_between_bottles_without_empty_gap()
    test_camera_failure_does_not_actuate()
    test_detector_exception_survives()
    test_watcher_disarms_without_session()
    test_max_bottles_rejects_and_does_not_credit()
    test_watcher_releases_camera_when_disarmed()
    test_actuation_failure_recovers_with_reset()
    print("\nALL CAMERA-TRIGGERED PIPELINE TESTS PASSED")


if __name__ == "__main__":
    _run_all()
