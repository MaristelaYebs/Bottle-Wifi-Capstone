"""Bottle Wi-Fi Vendo — Orange Pi Zero 3 application entrypoint.

Detection is camera-triggered: there is no IR sensor. While a portal session is
open the ChuteWatcher keeps the webcam open and scans frame after frame —
    frame -> YOLO/NCNN infer -> label -> sliding window agrees -> decide
          -> ACCEPT/REJECT to Arduino -> await SERVO_COMPLETE -> credit session.
SQLite logging (Phase 10), dataset capture (Phase 11), and reward/MikroTik
(Phases 15-16) all hang off the same decision callback.

Architecture: docs/architecture/01-high-level-architecture.md
              docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import logging
import queue
import signal
import threading
from pathlib import Path

from core.config import load_config
from core.logging_setup import setup_logging
from core.state import AppState
from core.types import EventKind, InboundEvent
from repositories.db import Database
from repositories.detection_repository import DetectionRepository
from repositories.event_repository import EventRepository
from repositories.reward_repository import RewardRepository
from repositories.session_repository import SessionRepository
from services.camera_service import CameraService
from services.dataset_service import DatasetService
from services.decision_service import DecisionService
from services.detector_service import build_detector
from services.health_service import HealthService
from services.mikrotik_service import MikroTikService
from services.portal_api_service import PortalApi
from services.preview_service import PreviewService
from services.reward_service import RewardService
from services.serial_service import SerialService
from services.session_service import SessionManager
from services.watch_service import ChuteWatcher

APP_ROOT = Path(__file__).resolve().parent


class Orchestrator:
    def __init__(self, cfg, state: AppState, log: logging.Logger, *,
                 serial_transport_factory=None, camera=None, detector=None,
                 decision=None, db=None, dataset=None) -> None:
        self.cfg = cfg
        self.state = state
        self.log = log
        self.events: "queue.Queue[InboundEvent]" = queue.Queue(maxsize=64)

        self.db = db if db is not None else Database(
            str(cfg.resolve_path(cfg.get("paths.db", "./database/bottle_vendo.db"))))
        self.detections = DetectionRepository(self.db)
        self.system_events = EventRepository(self.db)

        self.serial = SerialService(cfg, on_event=self._enqueue,
                                    transport_factory=serial_transport_factory)
        self.camera = camera if camera is not None else CameraService(cfg)
        self.detector = detector if detector is not None else build_detector(cfg)
        self.decision = decision if decision is not None else DecisionService(cfg)
        self.dataset = dataset if dataset is not None else DatasetService(cfg)

        # reward stack (Phase 16)
        self.mikrotik = MikroTikService(cfg)
        self.session_repo = SessionRepository(self.db)
        self.reward_repo = RewardRepository(self.db)
        self.session_mgr = SessionManager(cfg, self.session_repo)
        self.reward = RewardService(cfg, self.detections, self.reward_repo,
                                    self.session_repo, self.session_mgr, self.mikrotik)
        self.preview = PreviewService(cfg, self.camera, self.detector, self.decision,
                                      self.state)
        self.portal = PortalApi(cfg, self.session_mgr, self.reward, self.mikrotik,
                                self.camera, health_provider=self.state.get_health,
                                preview=self.preview)
        # Camera-triggered detection: arms itself whenever a portal session is
        # open, so nothing has to tell it a bottle is coming.
        self.watcher = ChuteWatcher(cfg, self.camera, self.detector, self.decision,
                                    self.state, session_provider=self.session_mgr.active,
                                    on_decision=self._handle_decision)
        self.health = HealthService(
            cfg,
            serial_ok=lambda: self.state.serial_connected,
            model_ok=self.detector.is_loaded,
            db_ok=self.db.health_ok,
            camera_ok=lambda: self.state.camera_ok,
            mikrotik_ok=lambda: self.mikrotik.test_connection()[0],
            disk_path=str(cfg.resolve_path(cfg.get("paths.logs", "./logs"))
                          if hasattr(cfg, "resolve_path") else "."),
        )
        self._retry_interval_ms = int(cfg.get("reward.retry_interval_ms", 30000))
        self._health_interval_ms = int(cfg.get("health.interval_ms", 10000))
        self._bg_stop = threading.Event()
        self._bg_threads: list[threading.Thread] = []

        self.total_frames = int(cfg.get("detection.total_frames", 5))
        self.frame_delay_ms = int(cfg.get("detection.frame_delay_ms", 100))

    def _enqueue(self, ev: InboundEvent) -> None:
        try:
            self.events.put_nowait(ev)
        except queue.Full:
            self.log.warning("event queue full, dropping %s", ev.kind)

    # ---------------- startup ----------------
    def start(self, background: bool = True) -> bool:
        self.db.connect()
        self.system_events.add("INFO", "orchestrator", "service_start",
                               f"board={self.cfg.get('hardware.board')}")
        if not self.serial.connect():
            self.log.error("Arduino serial not available at startup (will keep retrying)")
        self.serial.start_rx()
        self.state.serial_connected = self.serial.is_connected()
        if self.serial.is_connected():
            self.log.info("Arduino link %s", "verified (PONG)" if self.serial.ping()
                          else "did not answer PING")
            self.serial.send_and_wait_ack("RESET", "ACK:RESET")

        if self.detector.load():
            self.detector.warmup()
            self.log.info("detector ready: %s", self.detector.version())
        else:
            self.log.error("detector NOT loaded — all detections will REJECT (fail-safe)")

        ok, detail = self.mikrotik.test_connection()
        self.log.info("MikroTik: %s (%s)", "OK" if ok else "unavailable", detail)

        if background:
            self._start_background()

        self.log.info("READY")
        return True

    def _start_background(self) -> None:
        self.watcher.start()
        self._bg_threads = [
            threading.Thread(target=self._serve_portal, name="http-portal", daemon=True),
            threading.Thread(target=self._reward_retry_loop, name="reward-retry", daemon=True),
            threading.Thread(target=self._health_loop, name="health-timer", daemon=True),
        ]
        for t in self._bg_threads:
            t.start()

    def _health_loop(self) -> None:
        interval = self._health_interval_ms / 1000.0
        prev_ready = None
        while not self._bg_stop.wait(interval):
            try:
                hr = self.health.snapshot()
                self.state.set_health(hr)
                self.state.mikrotik_ok = hr.mikrotik
                warns = self.health.warnings(hr)
                if warns:
                    self.log.warning("health: %s", "; ".join(warns))
                ready = hr.is_ready()
                if ready != prev_ready:
                    self.system_events.add("INFO" if ready else "ERROR", "health",
                                           "readiness", "READY" if ready else "DEGRADED",
                                           {"warnings": warns})
                    prev_ready = ready
            except Exception as exc:  # noqa: BLE001
                self.log.error("health loop error: %s", exc)

    def _serve_portal(self) -> None:
        try:
            self.portal.serve()
        except Exception as exc:  # noqa: BLE001
            self.log.error("portal server stopped: %s", exc)

    def _reward_retry_loop(self) -> None:
        interval = self._retry_interval_ms / 1000.0
        while not self._bg_stop.wait(interval):
            try:
                n = self.reward.retry_pending()
                if n:
                    self.log.info("applied %d pending reward(s)", n)
            except Exception as exc:  # noqa: BLE001
                self.log.error("reward retry error: %s", exc)

    # ---------------- event loop ----------------
    def run(self) -> None:
        while self.state.running:
            try:
                ev = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._dispatch(ev)
            except Exception:  # noqa: BLE001 - one bad event must not kill the loop
                self.log.exception("dispatch error for %s", ev.kind)

    def _dispatch(self, ev: InboundEvent) -> None:
        if ev.kind == EventKind.SHUTDOWN:
            self.state.stop()
        elif ev.kind == EventKind.ARDUINO_READY:
            self.log.info("Arduino READY")
            self.serial.send_and_wait_ack("RESET", "ACK:RESET")
        elif ev.kind == EventKind.OBJECT_DETECTED:
            # Old IR firmware still flashed? Detection is camera-triggered now,
            # so the watcher has already seen (or will see) the bottle itself.
            self.log.info("OBJECT_DETECTED ignored (camera-triggered detection)")
        elif ev.kind == EventKind.ARDUINO_ERROR:
            self.log.warning("Arduino ERROR: %s", ev.payload.get("message"))
        elif ev.kind == EventKind.SERIAL_UP:
            self.state.serial_connected = True
            self.log.info("serial link up")
        elif ev.kind == EventKind.SERIAL_DOWN:
            self.state.serial_connected = False
            self.log.warning("serial link down")

    # ---------------- detection pipeline ----------------
    def _handle_decision(self, decision, frames) -> None:
        """Record -> actuate -> credit. Called by the ChuteWatcher on its own
        thread once a window of frames agrees; it blocks until the servo
        sequence has finished, which is what stops a second verdict mid-sweep.
        The watcher owns the single-flight lock for the whole scan, so nothing
        is claimed here."""
        try:
            self.log.info(
                "decision=%s logical=%s pred=%s conf=%.2f frames=%d/%d reasons=%s",
                decision.outcome, decision.logical, decision.prediction,
                decision.confidence, decision.positive_frames, decision.total_frames,
                "; ".join(decision.reasons),
            )
            session = self.session_mgr.active()

            # Max-bottle cap: once the session is full, a real bottle is still
            # physically REJECTED (reject servo) rather than ignored, so nothing
            # can pile up in the chute. The portal shows the "max reached" notice
            # off the same count. Applies to fresh and extend-time sessions alike.
            outcome = decision.outcome
            session_full = bool(session and outcome == "ACCEPT"
                                and session["BottleCount"] >= session["MaxBottles"])
            if session_full:
                outcome = "REJECT"
                self.log.info("session %s at max (%d/%d) — rejecting extra bottle",
                              session["Voucher"], session["BottleCount"], session["MaxBottles"])

            txn_id = self.detections.insert(decision, self.detector.version())
            self.log.info("recorded %s", txn_id)
            if session_full:
                self.detections.set_error(txn_id, "session_full")
            if decision.collect_sample:
                path = self.dataset.maybe_save(frames, decision, self.detector.version(), txn_id)
                if path:
                    self.detections.set_image_path(txn_id, path)
            ok = self._actuate(outcome)
            if not ok:
                self.detections.set_error(txn_id, "actuation_failed")
                self.system_events.add("ERROR", "orchestrator", "actuation_failed",
                                       f"{outcome} for {txn_id}")
            elif outcome == "ACCEPT":
                credited = self.reward.credit_accepted_bottle(txn_id, session)
                self.log.info("bottle credited to session: %s", credited)
        except Exception:  # noqa: BLE001 - never let one bottle kill the watcher
            self.log.exception("decision handling failed")

    def _actuate(self, outcome: str) -> bool:
        """Command the hardware sequence and confirm completion.

        On any failure (no ACK, or no SERVO_COMPLETE) force the Arduino back to
        IDLE with RESET so a dropped command can't leave the chute jammed or the
        state machine stuck.
        """
        cmd = "ACCEPT" if outcome == "ACCEPT" else "REJECT"
        ack = f"ACK:{cmd}"
        if not self.serial.send_and_wait_ack(cmd, ack):
            self.log.error("no %s from Arduino — recovering with RESET", ack)
            self._recover()
            return False
        if not self.serial.wait_for("SERVO_COMPLETE"):
            self.log.error("no SERVO_COMPLETE within timeout — recovering with RESET")
            self._recover()
            return False
        self.log.info("actuation complete: %s", outcome)
        return True

    def _recover(self) -> None:
        try:
            self.serial.send_and_wait_ack("RESET", "ACK:RESET", timeout_ms=500, retries=1)
        except Exception as exc:  # noqa: BLE001
            self.log.error("recovery RESET failed: %s", exc)

    def shutdown(self) -> None:
        self.log.info("shutting down")
        self._bg_stop.set()
        try:
            self.watcher.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.serial.is_connected():
                self.serial.send_and_wait_ack("RESET", "ACK:RESET", timeout_ms=300, retries=0)
        except Exception:  # noqa: BLE001
            pass
        self.serial.close()
        self.db.close()


def main() -> int:
    cfg = load_config(APP_ROOT)
    log = setup_logging(cfg.resolve_path(cfg.get("paths.logs", "./logs")),
                        level=cfg.get("logging.level", "INFO"),
                        rotate_mb=int(cfg.get("logging.rotate_mb", 5)),
                        backups=int(cfg.get("logging.backups", 5)))
    log.info("Bottle Wi-Fi Vendo starting (board=%s)", cfg.get("hardware.board"))

    state = AppState()
    orch = Orchestrator(cfg, state, log)

    def _on_signal(signum, _frame):
        log.info("signal %s received", signum)
        orch._enqueue(InboundEvent(EventKind.SHUTDOWN))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

    try:
        orch.start()
        orch.run()
    finally:
        orch.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
