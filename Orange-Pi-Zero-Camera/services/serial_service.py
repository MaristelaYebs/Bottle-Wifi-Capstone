"""Serial Service

USB serial link to the Arduino Uno: protocol parse, ACK/timeout/retry,
keepalive, auto-reconnect.

Implemented in Phase 5. Interface: docs/architecture/02-low-level-architecture.md

Message routing:
  Asynchronous EVENTS (pushed to the orchestrator queue via on_event):
      READY -> ARDUINO_READY,  OBJECT_DETECTED,  ERROR:<msg> -> ARDUINO_ERROR
  Solicited RESPONSES (awaited synchronously by callers):
      PONG,  ACK:ACCEPT / ACK:REJECT / ACK:RESET,  SERVO_COMPLETE
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, Protocol

from core.types import EventKind, InboundEvent

log = logging.getLogger("serial")

# Lines that are awaited as responses rather than emitted as events.
_RESPONSE_EXACT = {"PONG", "SERVO_COMPLETE"}


def classify_line(line: str):
    """Pure classifier for one received line.

    Returns one of:
      ("event", InboundEvent)   -> push to orchestrator queue
      ("response", token:str)   -> signal a waiter
      ("ignore", None)          -> debug/comment/unknown/noise
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return ("ignore", None)
    if line in _RESPONSE_EXACT or line.startswith("ACK:"):
        return ("response", line)
    if line == "READY":
        return ("event", InboundEvent(EventKind.ARDUINO_READY))
    if line == "OBJECT_DETECTED":
        return ("event", InboundEvent(EventKind.OBJECT_DETECTED))
    if line.startswith("ERROR:"):
        return ("event", InboundEvent(EventKind.ARDUINO_ERROR, {"message": line[6:]}))
    return ("ignore", None)


class Transport(Protocol):
    """Minimal transport interface (satisfied by pyserial.Serial and test fakes)."""

    def write(self, data: bytes) -> int: ...
    def readline(self) -> bytes: ...
    def close(self) -> None: ...


class SerialService:
    def __init__(self, cfg, on_event: Callable[[InboundEvent], None],
                 transport_factory: Optional[Callable[[], Transport]] = None) -> None:
        self.cfg = cfg
        self._on_event = on_event
        self._transport_factory = transport_factory  # tests inject a fake
        self._ser: Optional[Transport] = None

        self._baud = int(cfg.get("serial.baudrate", 115200))
        self._port_cfg = str(cfg.get("serial.port", "auto"))
        self._ack_timeout_ms = int(cfg.get("serial.ack_timeout_ms", 500))
        self._ack_retries = int(cfg.get("serial.ack_retries", 3))
        self._servo_complete_timeout_ms = int(cfg.get("serial.servo_complete_timeout_ms", 6000))
        self._ping_interval_ms = int(cfg.get("serial.ping_interval_ms", 5000))

        self._cond = threading.Condition()
        self._responses: set[str] = set()
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connected = threading.Event()

    # ---------------- connection ----------------
    def _make_transport(self) -> Transport:
        if self._transport_factory is not None:
            return self._transport_factory()
        import serial  # lazy: only needed on real hardware

        port = self._port_cfg if self._port_cfg != "auto" else discover_port()
        if not port:
            raise RuntimeError("no serial port found (set serial.port explicitly)")
        log.info("opening serial port %s @ %d", port, self._baud)
        return serial.Serial(port, self._baud, timeout=0.2)

    def connect(self) -> bool:
        try:
            self._ser = self._make_transport()
            self._connected.set()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("serial connect failed: %s", exc)
            self._connected.clear()
            return False

    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ---------------- rx thread ----------------
    def start_rx(self) -> None:
        if self._rx_thread and self._rx_thread.is_alive():
            return
        self._stop.clear()
        self._rx_thread = threading.Thread(target=self._rx_loop, name="serial-rx", daemon=True)
        self._rx_thread.start()

    def _rx_loop(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            if self._ser is None:
                if not self.connect():
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue
                backoff = 0.5
                self._emit_event(InboundEvent(EventKind.SERIAL_UP))
            try:
                raw = self._ser.readline()
            except Exception as exc:  # noqa: BLE001 - device removed / read error
                log.warning("serial read error: %s (reconnecting)", exc)
                self._drop_connection()
                continue
            if not raw:
                continue  # read timeout, keep looping
            try:
                line = raw.decode("ascii", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            self._route(line)

    def _drop_connection(self) -> None:
        self._connected.clear()
        try:
            if self._ser:
                self._ser.close()
        except Exception:  # noqa: BLE001
            pass
        self._ser = None
        self._emit_event(InboundEvent(EventKind.SERIAL_DOWN))

    def _route(self, line: str) -> None:
        kind, obj = classify_line(line)
        if kind == "response":
            with self._cond:
                self._responses.add(obj)  # type: ignore[arg-type]
                self._cond.notify_all()
        elif kind == "event":
            self._emit_event(obj)  # type: ignore[arg-type]
        # else ignore

    def _emit_event(self, ev: InboundEvent) -> None:
        try:
            self._on_event(ev)
        except Exception as exc:  # noqa: BLE001
            log.error("on_event handler raised: %s", exc)

    # ---------------- sending / waiting ----------------
    def send(self, command: str) -> None:
        if self._ser is None:
            raise RuntimeError("serial not connected")
        payload = (command.strip() + "\n").encode("ascii")
        self._ser.write(payload)

    def _wait_response(self, token: str, timeout_ms: int) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000.0
        with self._cond:
            while token not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            self._responses.discard(token)
            return True

    def _clear_response(self, token: str) -> None:
        with self._cond:
            self._responses.discard(token)

    def send_and_wait_ack(self, command: str, ack_token: str,
                          timeout_ms: Optional[int] = None,
                          retries: Optional[int] = None) -> bool:
        timeout_ms = self._ack_timeout_ms if timeout_ms is None else timeout_ms
        retries = self._ack_retries if retries is None else retries
        for attempt in range(retries + 1):
            self._clear_response(ack_token)
            try:
                self.send(command)
            except Exception as exc:  # noqa: BLE001
                log.error("send %s failed: %s", command, exc)
                return False
            if self._wait_response(ack_token, timeout_ms):
                return True
            log.warning("no %s (attempt %d/%d)", ack_token, attempt + 1, retries + 1)
        return False

    def wait_for(self, token: str, timeout_ms: Optional[int] = None) -> bool:
        """Wait for a solicited response already expected to arrive (e.g. SERVO_COMPLETE)."""
        if timeout_ms is None:
            timeout_ms = self._servo_complete_timeout_ms
        return self._wait_response(token, timeout_ms)

    def ping(self, timeout_ms: int = 1000) -> bool:
        self._clear_response("PONG")
        try:
            self.send("PING")
        except Exception as exc:  # noqa: BLE001
            log.error("ping send failed: %s", exc)
            return False
        return self._wait_response("PONG", timeout_ms)

    # ---------------- lifecycle ----------------
    def close(self) -> None:
        self._stop.set()
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        try:
            if self._ser:
                self._ser.close()
        except Exception:  # noqa: BLE001
            pass
        self._ser = None
        self._connected.clear()


def discover_port() -> Optional[str]:
    """Best-effort auto-detect of the Arduino serial device (Linux-first)."""
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return None

    ports = list(list_ports.comports())
    # Prefer a stable by-id symlink if the device exposes one via /dev/serial/by-id
    def score(p) -> int:
        name = (p.device or "").lower()
        desc = ((p.description or "") + " " + (getattr(p, "manufacturer", "") or "")).lower()
        s = 0
        if "acm" in name:
            s += 100
        elif "usb" in name:
            s += 50
        if "arduino" in desc or "acm" in desc:
            s += 25
        return s

    ports.sort(key=score, reverse=True)
    return ports[0].device if ports else None
