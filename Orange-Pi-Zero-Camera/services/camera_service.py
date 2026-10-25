"""Camera Service

On-demand USB webcam capture via OpenCV (MJPEG). Opens per event, releases
immediately. Never streams — RAM discipline for the 1.5 GB board.

Implemented in Phase 6. Interface: docs/architecture/02-low-level-architecture.md

The capture pipeline calls `capture_burst(n, gap_ms)` which opens the device,
discards a few warm-up frames (auto-exposure), grabs N frames, and guarantees
release. `open()/capture_frames()/release()` are exposed as building blocks too.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import time
from typing import Any, Callable, List, Optional

log = logging.getLogger("camera")

try:  # OpenCV is only present on the Pi; keep the module importable elsewhere.
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None  # type: ignore

try:
    import numpy as _np  # type: ignore
except Exception:  # noqa: BLE001
    _np = None  # type: ignore

# OpenCV VideoCapture property ids (hard-coded so the module works without cv2
# when a fake capture is injected for tests).
_PROP_FRAME_WIDTH = 3
_PROP_FRAME_HEIGHT = 4
_PROP_FPS = 5
_PROP_FOURCC = 6
_PROP_BUFFERSIZE = 38


def _fourcc_int(code: str) -> int:
    code = (code + "    ")[:4]
    return (ord(code[0]) | (ord(code[1]) << 8) | (ord(code[2]) << 16) | (ord(code[3]) << 24))


class CameraService:
    def __init__(self, cfg, capture_factory: Optional[Callable[[int], Any]] = None) -> None:
        self.cfg = cfg
        # camera.device may be an integer index (e.g. 0) OR a device path
        # (e.g. "/dev/v4l/by-id/usb-...-index0"). A stable by-id path is
        # preferred on the Pi, where the raw /dev/videoN number is not stable
        # across reboots (the cedrus codec and the USB camera race for video0).
        self._device = self._coerce_device(cfg.get("camera.device", 0))
        self._width = int(cfg.get("camera.width", 1280))
        self._height = int(cfg.get("camera.height", 720))
        self._fourcc = str(cfg.get("camera.fourcc", "MJPG"))
        self._warmup_frames = int(cfg.get("camera.warmup_frames", 2))
        self._jpeg_quality = int(cfg.get("dataset.jpeg_quality", 85))
        self._capture_factory = capture_factory
        self._cap: Any = None
        # Capture backend: OpenCV's V4L2 read() is unreliable on the H618 (times
        # out intermittently even at 480M) while `v4l2-ctl` grabs MJPG frames
        # rock-solid. "auto" uses v4l2-ctl when the device is a real path on
        # Linux and the tool exists; "opencv" forces the old path; "v4l2ctl"
        # forces the tool. See docs/architecture/06-usb-webcam.md.
        self._backend = str(cfg.get("camera.capture_backend", "auto")).lower()
        self._v4l2_open = False   # logical open state for the v4l2-ctl backend

    @staticmethod
    def _coerce_device(dev):
        """An int index stays an int; anything else (a /dev path) stays a str."""
        try:
            return int(dev)
        except (TypeError, ValueError):
            return str(dev)

    # ---------------- default capture factory ----------------
    def _open_capture(self):
        if self._capture_factory is not None:
            return self._capture_factory(self._device)
        if cv2 is None:
            raise RuntimeError("OpenCV (cv2) not installed")
        # Prefer V4L2 on Linux and DirectShow on Windows for webcam devices.
        if platform.system() == "Linux" and hasattr(cv2, "CAP_V4L2"):
            cap = cv2.VideoCapture(self._device, cv2.CAP_V4L2)
        elif platform.system() == "Windows" and hasattr(cv2, "CAP_DSHOW"):
            cap = cv2.VideoCapture(self._device, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(self._device)
        return cap

    # ---------------- v4l2-ctl backend (reliable MJPG grab on the H618) --------
    def _using_v4l2ctl(self) -> bool:
        if self._capture_factory is not None:      # tests inject a fake -> OpenCV path
            return False
        if self._backend == "v4l2ctl":
            return True
        if self._backend == "opencv":
            return False
        # auto: a real device path on Linux, with the tool and numpy present
        return (platform.system() == "Linux" and isinstance(self._device, str)
                and _np is not None and shutil.which("v4l2-ctl") is not None)

    def _v4l2_grab(self, n: int) -> List[Any]:
        """Grab n MJPG frames via `v4l2-ctl` and decode them. Reliable where
        OpenCV's V4L2 read() is not. One device open per call."""
        if _np is None or cv2 is None:
            return []
        n = max(1, n)
        # count = requested + warmup; the first frames are often dark/garbage
        total = n + max(0, self._warmup_frames)
        cmd = ["v4l2-ctl", "-d", str(self._device),
               f"--set-fmt-video=width={self._width},height={self._height},pixelformat=MJPG",
               "--stream-mmap", f"--stream-count={total}", "--stream-to=-"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=8)
        except Exception as exc:  # noqa: BLE001
            log.warning("v4l2-ctl grab failed: %s", exc)
            return []
        data = p.stdout or b""
        if not data:
            log.warning("v4l2-ctl produced no data (rc=%s)", p.returncode)
            return []
        # stdout is concatenated JPEGs: split on SOI (FFD8), end each at its EOI
        # (FFD9) so trailing stream padding isn't fed to the decoder (which would
        # log "Corrupt JPEG data: N extraneous bytes before marker 0xd9").
        starts = [i for i in range(len(data) - 1)
                  if data[i] == 0xFF and data[i + 1] == 0xD8]
        frames: List[Any] = []
        for idx, s in enumerate(starts):
            limit = starts[idx + 1] if idx + 1 < len(starts) else len(data)
            eoi = data.find(b"\xff\xd9", s, limit)
            end = eoi + 2 if eoi != -1 else limit
            img = cv2.imdecode(_np.frombuffer(data[s:end], _np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                frames.append(img)
        # drop warmup frames, keep the last n
        return frames[-n:] if len(frames) > n else frames

    # ---------------- lifecycle ----------------
    def open(self) -> bool:
        if self._using_v4l2ctl():
            # No persistent handle; each grab opens the device itself. Probe once
            # so open() still means "camera is actually usable".
            self._v4l2_open = bool(self._v4l2_grab(1))
            if not self._v4l2_open:
                log.error("camera device %s did not open (v4l2-ctl)", self._device)
            return self._v4l2_open
        try:
            cap = self._open_capture()
        except Exception as exc:  # noqa: BLE001
            log.error("camera open failed: %s", exc)
            return False
        if not cap or not cap.isOpened():
            log.error("camera device %s did not open", self._device)
            try:
                if cap:
                    cap.release()
            except Exception:  # noqa: BLE001
                pass
            return False
        # Force MJPEG first (cuts USB bandwidth + RAM), then resolution.
        try:
            cap.set(_PROP_FOURCC, _fourcc_int(self._fourcc))
            cap.set(_PROP_FRAME_WIDTH, self._width)
            cap.set(_PROP_FRAME_HEIGHT, self._height)
            cap.set(_PROP_BUFFERSIZE, 1)
        except Exception as exc:  # noqa: BLE001
            log.warning("camera property set failed (continuing): %s", exc)
        self._cap = cap
        return True

    def release(self) -> None:
        self._v4l2_open = False
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:  # noqa: BLE001
                pass
            self._cap = None

    # ---------------- capture ----------------
    def capture_frames(self, n: int, gap_ms: int) -> List[Any]:
        """Grab n frames from an already-open device, gap_ms apart."""
        if self._cap is None:
            raise RuntimeError("camera not open")
        frames: List[Any] = []
        for i in range(n):
            if i > 0 and gap_ms > 0:
                time.sleep(gap_ms / 1000.0)
            ok, frame = self._cap.read()
            if not ok or frame is None:
                log.warning("frame %d/%d read failed", i + 1, n)
                continue
            frames.append(frame)
        return frames

    def is_open(self) -> bool:
        if self._using_v4l2ctl():
            return self._v4l2_open
        return self._cap is not None

    def read_frame(self, flush: int = 0) -> Optional[Any]:
        """One frame from an ALREADY-OPEN device, or None if the read failed.

        Used by the continuous chute watcher. `flush` discards that many queued
        frames first (OpenCV backend only); the v4l2-ctl backend always grabs a
        fresh frame per call, so it needs no flushing.
        """
        if self._using_v4l2ctl():
            frames = self._v4l2_grab(1)
            return frames[0] if frames else None
        if self._cap is None:
            return None
        try:
            for _ in range(max(0, flush)):
                self._cap.read()
            ok, frame = self._cap.read()
        except Exception as exc:  # noqa: BLE001 - device yanked mid-scan
            log.warning("frame read raised: %s", exc)
            return None
        return frame if (ok and frame is not None) else None

    def capture_burst(self, n: Optional[int] = None, gap_ms: Optional[int] = None) -> List[Any]:
        """Open -> warm up -> grab n frames -> release (guaranteed).

        This is the primary entry point for the detection pipeline.
        """
        n = int(self.cfg.get("detection.total_frames", 5)) if n is None else n
        gap_ms = int(self.cfg.get("detection.frame_delay_ms", 100)) if gap_ms is None else gap_ms
        if self._using_v4l2ctl():
            return self._v4l2_grab(n)          # one open, n frames, warmup handled inside
        if not self.open():
            return []
        try:
            # discard warm-up frames for auto-exposure/white-balance to settle
            for _ in range(max(0, self._warmup_frames)):
                self._cap.read()
            return self.capture_frames(n, gap_ms)
        finally:
            self.release()

    def snapshot_jpeg(self) -> Optional[bytes]:
        """Single JPEG frame for the portal /snapshot preview."""
        if cv2 is None:
            return None
        if self._using_v4l2ctl():
            frames = self._v4l2_grab(1)
            if not frames:
                return None
            ok, buf = cv2.imencode(".jpg", frames[0],
                                   [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality])
            return buf.tobytes() if ok else None
        if not self.open():
            return None
        try:
            for _ in range(max(1, self._warmup_frames)):
                ok, frame = self._cap.read()
            if not ok or frame is None:
                return None
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality])
            return buf.tobytes() if ok else None
        finally:
            self.release()

    # ---------------- health ----------------
    def probe(self) -> bool:
        """Light health check: can we open + grab one frame?"""
        if self._using_v4l2ctl():
            return bool(self._v4l2_grab(1))
        if not self.open():
            return False
        try:
            ok, frame = self._cap.read()
            return bool(ok and frame is not None)
        finally:
            self.release()

    def info(self) -> dict:
        """Report actual negotiated settings (call while open)."""
        if self._cap is None:
            return {}
        try:
            return {
                "width": int(self._cap.get(_PROP_FRAME_WIDTH)),
                "height": int(self._cap.get(_PROP_FRAME_HEIGHT)),
                "fps": self._cap.get(_PROP_FPS),
            }
        except Exception:  # noqa: BLE001
            return {}
