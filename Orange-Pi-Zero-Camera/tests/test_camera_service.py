"""Phase 6 unit tests — CameraService burst/lifecycle logic.

Uses an injected fake VideoCapture, so it runs anywhere (no OpenCV, no webcam).
Real frame capture is validated on the Pi via scripts/camera_check.py.

Run:  python tests/test_camera_service.py   (or pytest)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.camera_service import CameraService  # noqa: E402


class FakeCfg:
    def __init__(self, **over):
        self._d = {
            "camera.device": 0,
            "camera.width": 1280,
            "camera.height": 720,
            "camera.fourcc": "MJPG",
            "camera.warmup_frames": 2,
            "dataset.jpeg_quality": 85,
            "detection.total_frames": 5,
            "detection.frame_delay_ms": 0,  # 0 so tests are fast
        }
        self._d.update(over)

    def get(self, key, default=None):
        return self._d.get(key, default)


class FakeCapture:
    def __init__(self, *, frames_available=100, open_ok=True, fail_after=None):
        self._opened = open_ok
        self.reads = 0
        self.frames_available = frames_available
        self.fail_after = fail_after
        self.released = False
        self.props = {}

    def isOpened(self):
        return self._opened

    def set(self, prop, val):
        self.props[prop] = val
        return True

    def get(self, prop):
        return float(self.props.get(prop, 0))

    def read(self):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            return (False, None)
        if self.reads > self.frames_available:
            return (False, None)
        return (True, object())  # a stand-in frame

    def release(self):
        self.released = True


def test_burst_opens_warms_and_releases():
    cap = FakeCapture()
    cam = CameraService(FakeCfg(), capture_factory=lambda dev: cap)
    frames = cam.capture_burst(n=5, gap_ms=0)
    assert len(frames) == 5
    # 2 warm-up reads + 5 captured reads = 7
    assert cap.reads == 7, cap.reads
    assert cap.released is True
    # MJPEG fourcc + resolution were requested
    assert cam._cap is None  # released -> handle cleared
    print("ok: burst opens, warms up (2), captures 5, releases")


def test_open_failure_returns_empty():
    cap = FakeCapture(open_ok=False)
    cam = CameraService(FakeCfg(), capture_factory=lambda dev: cap)
    frames = cam.capture_burst(n=5, gap_ms=0)
    assert frames == []
    print("ok: open failure -> empty burst (fail-safe)")


def test_partial_read_failures_skipped():
    # Warm-up=2 reads ok, then only 1 more good frame, then failures.
    cap = FakeCapture(fail_after=3)
    cam = CameraService(FakeCfg(), capture_factory=lambda dev: cap)
    frames = cam.capture_burst(n=5, gap_ms=0)
    assert len(frames) == 1, len(frames)  # only read #3 succeeded after 2 warmups
    assert cap.released is True
    print("ok: failed reads skipped, device still released")


def test_probe():
    assert CameraService(FakeCfg(), capture_factory=lambda dev: FakeCapture()).probe() is True
    assert CameraService(FakeCfg(), capture_factory=lambda dev: FakeCapture(open_ok=False)).probe() is False
    print("ok: probe reflects open+grab")


def _run_all():
    test_burst_opens_warms_and_releases()
    test_open_failure_returns_empty()
    test_partial_read_failures_skipped()
    test_probe()
    print("\nALL PHASE 6 CAMERA TESTS PASSED")


if __name__ == "__main__":
    _run_all()
