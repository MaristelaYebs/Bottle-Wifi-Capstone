"""Hardware bring-up — run ON THE ORANGE PI with the Arduino connected by USB.

Steps through the accept/reject hardware path and prints PASS/FAIL for each
protocol step. You visually confirm the LEDs / servos / buzzer against spec:

  ACCEPT sequence: GREEN LED on, SHORT beep (~150 ms), accept flap sweeps out+back
  REJECT sequence: RED LED on,  LONG beep (~1000 ms), reject flap sweeps out+back

There is no IR sensor: detection is camera-triggered, so the Arduino only ever
runs the sequence the Pi commands. This script drives those commands directly —
nothing has to be dropped into the chute.

    python scripts/hardware_check.py
    python scripts/hardware_check.py --skip-busy   # skip the BUSY-guard test

Added in Phase 9 (updated for camera-triggered detection). Not needed for
normal operation.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config  # noqa: E402
from core.types import EventKind, InboundEvent  # noqa: E402
from services.serial_service import SerialService  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent


class Signals:
    def __init__(self):
        self.ready = threading.Event()
        self.errors: list[str] = []

    def on_event(self, ev: InboundEvent):
        if ev.kind == EventKind.ARDUINO_READY:
            self.ready.set()
        elif ev.kind == EventKind.ARDUINO_ERROR:
            self.errors.append(str(ev.payload.get("message")))


def _p(ok: bool, label: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def run_sequence(serial, outcome: str) -> bool:
    ack = f"ACK:{outcome}"
    ok = _p(serial.send_and_wait_ack(outcome, ack), f"{outcome}: {ack} received")
    if not ok:
        return False
    ok &= _p(serial.wait_for("SERVO_COMPLETE", timeout_ms=10000), f"{outcome}: SERVO_COMPLETE")
    print(f"    -> visually confirm the {outcome} LED/beep/flap sweep were correct")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Bottle Vendo hardware bring-up")
    ap.add_argument("--skip-busy", action="store_true")
    args = ap.parse_args()

    cfg = load_config(APP_ROOT)
    sig = Signals()
    serial = SerialService(cfg, on_event=sig.on_event)

    print("Connecting to Arduino...")
    if not serial.connect():
        print("  [FAIL] serial connect — check the USB cable / serial.port in config")
        return 1
    serial.start_rx()
    time.sleep(0.5)

    results = []
    print("\n1) Link check")
    results.append(_p(serial.ping(1500), "PING -> PONG"))

    print("\n2) RESET to idle")
    results.append(_p(serial.send_and_wait_ack("RESET", "ACK:RESET"), "RESET -> ACK:RESET"))

    print("\n3) ACCEPT sequence")
    results.append(run_sequence(serial, "ACCEPT"))
    time.sleep(1.0)

    print("\n4) REJECT sequence")
    results.append(run_sequence(serial, "REJECT"))
    time.sleep(1.0)

    if not args.skip_busy:
        print("\n5) BUSY guard (a command mid-sweep must not interrupt the flap)")
        sig.errors.clear()
        if serial.send_and_wait_ack("ACCEPT", "ACK:ACCEPT"):
            time.sleep(0.3)                     # sequence is now mid-sweep
            serial.send("REJECT")
            time.sleep(0.5)
            got_busy = any("BUSY" in e for e in sig.errors)
            done = serial.wait_for("SERVO_COMPLETE", timeout_ms=10000)
            results.append(_p(got_busy and done,
                              "ERROR:BUSY returned and the accept sweep still finished"))
        else:
            results.append(_p(False, "BUSY guard: could not start a sequence"))

    serial.close()
    ok = all(results)
    print(f"\n{'ALL HARDWARE CHECKS PASSED' if ok else 'SOME CHECKS FAILED — see above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
