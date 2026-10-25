"""Laptop bench run — exercise the full bottle->credit->reward chain off-device.

Runs the REAL orchestrator (real Arduino over USB serial, real webcam, real NCNN
model from models/production, real SQLite + session/reward logic) on a dev PC so
the crediting flow can be proven before the Orange Pi is in the loop.

Two things are isolated from the production setup so a bench run cannot pollute it:
  * a separate DB          (database/bench_laptop.db)
  * a separate sample dir  (dataset/_bench)
and two can be substituted when the hardware is not on the desk:
  * --sim-serial      fake Arduino (auto-ACKs; 'b' key fakes a bottle verdict)
  * --fake-mikrotik   fake RouterOS (records the calls it would have made)

    python scripts/bench_laptop.py --fake-mikrotik
    python scripts/bench_laptop.py --serial-port COM7 --http-port 8080

Detection is camera-triggered: the watcher only scans while a session is OPEN,
so press `t` first, then hold a bottle in front of the camera.

Console commands mirror what the captive portal calls:
    t = /topUp (open session)   c = /checkCoin   d = /useVoucher (Done)
    x = /cancelTopUp            s = status       b = fake a bottle verdict
    q = quit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.state import AppState  # noqa: E402
from core.types import EventKind, InboundEvent  # noqa: E402
import main as app  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# config with bench overrides layered on top of the real config.yaml/.env
# --------------------------------------------------------------------------
class BenchConfig:
    def __init__(self, base, overrides: dict):
        self._base = base
        self._ov = overrides
        self.mikrotik = base.mikrotik
        self.app_root = base.app_root
        self.data = base.data

    def get(self, dotted: str, default=None):
        if dotted in self._ov:
            return self._ov[dotted]
        return self._base.get(dotted, default)

    def resolve_path(self, p):
        return self._base.resolve_path(p)


# --------------------------------------------------------------------------
# stand-ins for hardware that is not on the bench
# --------------------------------------------------------------------------
class FakeMikroTik:
    """Records what it would have done to RouterOS. Always succeeds."""

    configured = True

    def __init__(self):
        self.calls: list[str] = []
        self.limits: dict[str, int] = {}

    def test_connection(self):
        return True, "FAKE router (bench)"

    def find_active_user_by_mac(self, mac):
        return None

    def find_active_user_by_ip(self, ip):
        return None

    def remaining_seconds(self, voucher):
        return self.limits.get(voucher, 0)

    def set_user_absolute(self, voucher, target_seconds):
        self.limits[voucher] = int(target_seconds)
        self.calls.append(f"set_user_absolute({voucher}, {target_seconds}s"
                          f" = {target_seconds // 60}min)")
        return True, f"FAKE set limit={target_seconds}s"

    def kick(self, voucher, mac):
        self.calls.append(f"kick({voucher}, {mac})")
        return True, "FAKE kicked=0"

    def login_mac(self, voucher, mac, ip):
        self.calls.append(f"login_mac({voucher}, {mac}, {ip})")
        return True, "FAKE logged-in"


class SimTransport:
    """Fake Arduino: auto-answers the protocol and lets the bench inject events."""

    MAP = {
        "PING": ["PONG"],
        "ACCEPT": ["ACK:ACCEPT", "SERVO_COMPLETE"],
        "REJECT": ["ACK:REJECT", "SERVO_COMPLETE"],
        "RESET": ["ACK:RESET"],
    }

    def __init__(self, servo_ms: int = 1800):
        import collections

        self._out = collections.deque()
        self._lock = threading.Lock()
        self._servo_ms = servo_ms
        self.writes: list[str] = []

    def write(self, data: bytes) -> int:
        cmd = data.decode("ascii").strip()
        self.writes.append(cmd)
        replies = self.MAP.get(cmd, [])
        with self._lock:
            for r in replies:
                if r == "SERVO_COMPLETE":
                    # mimic the real servo dwell so timeouts are exercised honestly
                    threading.Timer(self._servo_ms / 1000.0, self._push, [r]).start()
                else:
                    self._out.append((r + "\n").encode("ascii"))
        return len(data)

    def _push(self, line: str) -> None:
        with self._lock:
            self._out.append((line + "\n").encode("ascii"))

    def readline(self) -> bytes:
        with self._lock:
            if self._out:
                return self._out.popleft()
        time.sleep(0.005)
        return b""

    def close(self):
        pass


# --------------------------------------------------------------------------
def pick_serial_port() -> str | None:
    """Windows-friendly port pick: prefer a USB-UART bridge, never Bluetooth."""
    try:
        from serial.tools import list_ports
    except Exception:  # noqa: BLE001
        return None

    def score(p) -> int:
        name = (p.device or "").lower()
        desc = f"{p.description or ''} {getattr(p, 'manufacturer', '') or ''}".lower()
        s = 0
        if "bluetooth" in desc:
            return -100
        if "acm" in name:
            s += 100
        elif "usb" in name:
            s += 50
        for kw in ("arduino", "ch340", "ch9102", "cp210", "ft232", "usb-serial", "wch"):
            if kw in desc:
                s += 40
        return s

    ports = sorted(list_ports.comports(), key=score, reverse=True)
    if not ports or score(ports[0]) < 0:
        return None
    return ports[0].device


def main() -> int:
    ap = argparse.ArgumentParser(description="Laptop bench run of the full vendo pipeline")
    ap.add_argument("--serial-port", default=None, help="e.g. COM7 (default: auto-detect)")
    ap.add_argument("--sim-serial", action="store_true", help="no Arduino: simulate it")
    ap.add_argument("--fake-mikrotik", action="store_true",
                    help="no router: record the RouterOS calls instead of making them")
    ap.add_argument("--camera", type=int, default=None, help="OpenCV camera index")
    ap.add_argument("--mock-detector", action="store_true",
                    help="skip real inference: every trigger sees a 0.95 bottle "
                         "(proves the credit plumbing, not the model)")
    ap.add_argument("--http-port", type=int, default=8080, help="portal API port")
    ap.add_argument("--db", default="./database/bench_laptop.db")
    args = ap.parse_args()

    base = load_config(APP_ROOT)
    overrides: dict = {
        "paths.db": args.db,
        "portal_api.port": args.http_port,
        "portal_api.host": "127.0.0.1",
        "dataset.dir": "./dataset/_bench",
    }
    if args.camera is not None:
        overrides["camera.device"] = args.camera
    if not args.sim_serial:
        port = args.serial_port or pick_serial_port()
        if not port:
            print("no serial port found — re-run with --sim-serial or --serial-port COMx")
            return 1
        overrides["serial.port"] = port

    cfg = BenchConfig(base, overrides)
    log = setup_logging(cfg.resolve_path(cfg.get("paths.logs", "./logs")), level="INFO")

    sim = SimTransport(servo_ms=int(cfg.get("servo.hold_ms", 1800))) if args.sim_serial else None
    detector = None
    if args.mock_detector:
        from core.types import Detection
        from services.detector_service import MockDetector

        detector = MockDetector(frames=[[Detection(0, "plastic_bottle", 0.95, (0, 0, 10, 10))]])

    state = AppState()
    orch = app.Orchestrator(cfg, state, log, detector=detector,
                            serial_transport_factory=(lambda: sim) if sim else None)
    if args.fake_mikrotik:
        fake = FakeMikroTik()
        orch.mikrotik = fake
        orch.reward.mikrotik = fake
        orch.portal.mikrotik = fake
        orch.health._mikrotik_ok = lambda: True
    else:
        fake = None

    print("\n" + "=" * 72)
    print("  BENCH RUN — laptop stand-in for the Orange Pi Zero 3")
    print("=" * 72)
    print(f"  serial     : {'SIMULATED' if sim else overrides.get('serial.port')}")
    print(f"  camera     : index {cfg.get('camera.device')}")
    det_desc = "MOCK (always a bottle)" if detector else \
        f"{cfg.get('detection.backend')} @ {cfg.get('detection.imgsz')}px (models/production)"
    print(f"  detector   : {det_desc}")
    print(f"  mikrotik   : {'FAKE (recording calls)' if fake else base.mikrotik.host}")
    print(f"  portal     : http://127.0.0.1:{args.http_port}")
    print(f"  preview    : http://127.0.0.1:{args.http_port}/preview")
    print(f"  database   : {args.db}")
    print(f"  reward     : {cfg.get('reward.minutes_per_bottle')} min/bottle,"
          f" max {cfg.get('reward.max_bottles_per_session')}/session")
    print("=" * 72 + "\n")

    orch.start(background=True)
    runner = threading.Thread(target=orch.run, name="orch-run", daemon=True)
    runner.start()

    voucher = {"v": ""}
    mac, ip = "AA:BB:CC:DD:EE:01", "10.10.10.99"

    def show(label, d):
        print(f"\n  [{label}] {d}\n")

    def status():
        s = orch.session_mgr.active()
        print("\n  --- status ---")
        if s:
            print(f"  session  : {s['Voucher']}  state={s['State']}"
                  f"  bottles={s['BottleCount']}/{s['MaxBottles']}"
                  f"  timeAdded={s['TimeAddedSeconds']}s")
        else:
            print("  session  : (none open)")
        print(f"  detections recorded: {orch.detections.count()}")
        for row in orch.detections.recent(5):
            print(f"    {row['TransactionId']}  {row['DetectionResult']:<6}"
                  f" conf={row['Confidence']:.2f}  credited={bool(row['CreditedToSession'])}")
        if fake and fake.calls:
            print("  RouterOS calls that would have been made:")
            for c in fake.calls:
                print(f"    {c}")
        print("  --------------\n")

    def fake_bottle():
        """Skip the camera: push a synthetic ACCEPT through the real
        record -> actuate -> credit path the watcher uses."""
        from core.types import Detection

        dets = [[Detection(0, "plastic_bottle", 0.95, (0, 0, 10, 10))]] * 3
        orch._handle_decision(orch.decision.decide(dets), [])

    HELP = ("  t=topUp(open session)  c=checkCoin  d=useVoucher(Done)  x=cancel"
            "  b=fake bottle verdict  s=status  q=quit")
    print(HELP)
    print("  >> the chute watcher only scans while a session is OPEN: press t, then"
          " hold a bottle in front of the camera <<\n")

    try:
        while True:
            try:
                cmd = input("bench> ").strip().lower()
            except EOFError:
                break
            if cmd in ("q", "quit", "exit"):
                break
            elif cmd == "t":
                r = orch.portal.top_up("", mac, ip, False)
                voucher["v"] = r.get("voucher", "")
                show("topUp", r)
            elif cmd == "c":
                show("checkCoin", orch.portal.check_coin(voucher["v"]))
            elif cmd == "d":
                show("useVoucher", orch.portal.use_voucher(voucher["v"]))
            elif cmd == "x":
                show("cancelTopUp", orch.portal.cancel_top_up(voucher["v"]))
            elif cmd == "b":
                print("  (faking a bottle verdict — bypasses the camera)")
                fake_bottle()
            elif cmd == "s":
                status()
            elif cmd in ("h", "?", "help"):
                print(HELP)
            elif cmd:
                print(HELP)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nshutting down...")
        status()
        state.stop()
        orch._enqueue(InboundEvent(EventKind.SHUTDOWN))
        runner.join(timeout=3.0)
        orch.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
