"""Chute Watcher — continuous camera-triggered detection.

Replaces the Arduino IR trigger. There is no sensor any more: while a portal
session is open (the phone is showing "Please insert a water bottle"), the Pi
keeps the webcam open and runs the detector frame after frame. As soon as a
sliding window of frames agrees, it fires ACCEPT/REJECT at the Arduino — the
bottle is still in view, so the flap moves right away instead of after an
IR debounce plus a cold camera open.

    armed (session OPEN)
      -> open camera once, keep it open
      -> per frame: infer -> label BOTTLE / REJECT / EMPTY -> push into window
      -> >= required_positive_frames BOTTLE in the window -> decide() -> ACCEPT
      -> >= required_positive_frames REJECT in the window -> decide() -> REJECT
      -> after acting: cooldown_ms AND required_clear_frames empty frames
         before the next bottle can be scored (one object cannot count twice)
    disarmed (no session / bottle cap reached) -> camera released, no CPU burn

EMPTY is NOT a verdict here. With an IR beam, "nothing visible" meant a failed
read and had to reject; with continuous scanning it is simply the idle state of
the chute, so the watcher keeps looking.

Interface: docs/architecture/08-camera-triggered-workflow.md
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("watcher")


def _field(row, name: str, default=None):
    """sqlite3.Row / dict / None-safe column read."""
    if row is None:
        return default
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return default


class ChuteWatcher:
    """Owns the camera while armed and calls `on_decision(decision, frames)`.

    `on_decision` runs on the watcher thread and is expected to block until the
    servo sequence finishes (record -> actuate -> credit), which is exactly what
    keeps a second verdict from being raised mid-sweep.
    """

    def __init__(self, cfg, camera, detector, decision, state,
                 session_provider: Callable[[], Any],
                 on_decision: Callable[[Any, list], None]) -> None:
        self.cfg = cfg
        self.camera = camera
        self.detector = detector
        self.decision = decision
        self.state = state
        self.session_provider = session_provider
        self.on_decision = on_decision

        self.enabled = bool(cfg.get("detection.continuous", True))
        self.always_on = bool(cfg.get("detection.always_on", False))
        self.window_size = int(cfg.get("detection.total_frames", 5))
        self.required = int(cfg.get("detection.required_positive_frames", 3))
        self.scan_interval_ms = int(cfg.get("detection.scan_interval_ms", 0))
        self.cooldown_ms = int(cfg.get("detection.cooldown_ms", 2000))
        self.required_clear = int(cfg.get("detection.required_clear_frames", 3))
        self.arm_poll_ms = int(cfg.get("detection.arm_poll_ms", 250))
        self.flush_frames = int(cfg.get("detection.flush_frames", 0))

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._holds_lock = False
        self._read_failures = 0
        self._armed_cache = False
        self._armed_checked_at = 0.0
        self._reset_window()

    # ---------------- lifecycle ----------------
    def start(self) -> None:
        if not self.enabled:
            log.info("continuous detection disabled (detection.continuous=false)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="chute-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._disarm()

    # ---------------- arming ----------------
    def is_armed(self) -> bool:
        """Scan for the whole open session — INCLUDING after the bottle cap is
        reached, so an extra bottle is physically rejected (handled in the
        orchestrator) instead of being ignored in the chute."""
        if self.always_on:
            return True
        s = self.session_provider()
        return bool(s and _field(s, "State") == "OPEN")

    def _armed_throttled(self) -> bool:
        """is_armed() hits SQLite; at full scan speed that would be one query
        per frame. arm_poll_ms is fresh enough for both arming and disarming."""
        now = time.monotonic()
        if now - self._armed_checked_at >= self.arm_poll_ms / 1000.0:
            self._armed_checked_at = now
            self._armed_cache = self.is_armed()
        return self._armed_cache

    def _arm(self) -> bool:
        """Claim the single-flight detection slot and open the camera."""
        if self._holds_lock and self.camera.is_open():
            return True
        if not self._holds_lock:
            if not self.state.try_begin_detection():
                return False           # a /preview or /detect dry-run is in flight
            self._holds_lock = True
        if not self.camera.is_open() and not self.camera.open():
            self.state.camera_ok = False
            self._release_lock()
            return False
        self.state.camera_ok = True
        self.state.detection_armed = True
        self._read_failures = 0
        self._reset_window()
        log.info("chute watch ARMED (window=%d need=%d)", self.window_size, self.required)
        return True

    def _disarm(self) -> None:
        if self.camera.is_open() or self._holds_lock:
            log.info("chute watch DISARMED")
        try:
            self.camera.release()
        except Exception:  # noqa: BLE001
            pass
        self.state.detection_armed = False
        self._release_lock()
        self._reset_window()

    def _release_lock(self) -> None:
        if self._holds_lock:
            self._holds_lock = False
            self.state.end_detection()

    # ---------------- window ----------------
    def _reset_window(self) -> None:
        self._dets = collections.deque(maxlen=self.window_size)
        self._labels = collections.deque(maxlen=self.window_size)
        self._frames = collections.deque(maxlen=self.window_size)
        self._clear_streak = 0
        self._resume_at = 0.0
        # True right after an ACCEPT until the chute is seen empty again — stops
        # the SAME bottle (still sitting in view) being credited twice. Rejects
        # are never gated by this (rejecting is fail-safe, it credits nothing).
        self._accept_locked = False

    def _clear_deques(self) -> None:
        self._dets.clear()
        self._labels.clear()
        self._frames.clear()

    # ---------------- main loop ----------------
    def run(self) -> None:
        poll = max(0.05, self.arm_poll_ms / 1000.0)
        while not self._stop.is_set():
            # Idle: check every poll. Scanning: throttle, the loop is fast.
            armed = self._armed_throttled() if self._holds_lock else self.is_armed()
            if not armed:
                self._disarm()
                self._stop.wait(poll)
                continue
            if not self._arm():
                self._stop.wait(poll)
                continue
            try:
                self._scan_once()
            except Exception:  # noqa: BLE001 - one bad frame must not kill the watcher
                log.exception("scan error")
                self._stop.wait(0.5)
            if self.scan_interval_ms > 0:
                self._stop.wait(self.scan_interval_ms / 1000.0)
        self._disarm()

    def _scan_once(self) -> None:
        frame = self.camera.read_frame(self.flush_frames)
        if frame is None:
            self._read_failures += 1
            if self._read_failures >= 3:
                log.warning("camera read failed %dx — reopening", self._read_failures)
                self.state.camera_ok = False
                self.camera.release()      # next _arm() reopens it
                self._reset_window()
                self._stop.wait(1.0)
            return
        self._read_failures = 0

        if not self.detector.is_loaded():
            # No model = no blind accepts. Say so once a second, scan nothing.
            self._stop.wait(1.0)
            return

        dets = self.detector.infer(frame)
        label = self.decision.frame_label(dets)

        # ---- brief quiet period right after a servo sequence ----
        # Ignore frames captured during/just after the sweep. This is purely
        # time-based now, so a lingering or freshly-inserted object can't wedge
        # the watcher waiting for an "empty" that never comes.
        if time.monotonic() < self._resume_at:
            return

        # An empty chute releases the accept-lock (the previous bottle is gone).
        if label == "EMPTY":
            self._clear_streak += 1
            if self._accept_locked and self._clear_streak >= self.required_clear:
                log.info("chute clear — accept re-armed")
                self._accept_locked = False
        else:
            self._clear_streak = 0

        self._dets.append(dets)
        self._labels.append(label)
        self._frames.append(frame)

        if len(self._labels) < self.required:
            return
        bottles = sum(1 for x in self._labels if x == "BOTTLE")
        rejects = sum(1 for x in self._labels if x == "REJECT")
        if bottles < self.required and rejects < self.required:
            return

        decision = self.decision.decide(list(self._dets))
        # Don't credit the same bottle twice: an ACCEPT only counts once the
        # chute has been seen empty since the last one. A REJECT always acts.
        if decision.outcome == "ACCEPT" and self._accept_locked:
            self._clear_deques()          # keep looking; wait for it to clear
            return

        frames = list(self._frames)
        log.info("window verdict: %s (%d bottle / %d reject / %d frames)",
                 decision.outcome, bottles, rejects, len(self._labels))
        if decision.outcome == "ACCEPT":
            self._accept_locked = True
            self._clear_streak = 0
        self._begin_cooldown()
        try:
            self.on_decision(decision, frames)
        finally:
            # Frames captured during the servo sweep are stale; drop them and
            # start the post-sweep quiet period.
            self._clear_deques()
            self._begin_cooldown()

    def _begin_cooldown(self) -> None:
        self._resume_at = time.monotonic() + self.cooldown_ms / 1000.0
