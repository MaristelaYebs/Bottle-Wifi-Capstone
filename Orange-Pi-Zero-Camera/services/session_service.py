"""Session Manager

Portal "coin-slot" session lifecycle (topUp -> checkCoin -> useVoucher). One open
session at a time (single chute). Persisted to PortalSession so a mid-session
restart recovers. A bottle == a coin.

Implemented in Phase 16. Interface: docs/architecture/02-low-level-architecture.md §3.5
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime
from typing import Optional, Tuple

log = logging.getLogger("session")

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class SessionManager:
    def __init__(self, cfg, session_repo) -> None:
        self.cfg = cfg
        self.repo = session_repo
        self.minutes = int(cfg.get("reward.minutes_per_bottle", 15))
        self.max_bottles = int(cfg.get("reward.max_bottles_per_session", 5))
        self.wait_ms = int(cfg.get("reward.wait_time_ms", 90000))
        self.stale_ms = int(cfg.get("reward.session_stale_ms", 180000))
        self._lock = threading.RLock()
        self._last_reported: dict[str, int] = {}  # voucher -> last check_coin count

    def gen_voucher(self) -> str:
        return "".join(secrets.choice(_ALPHABET) for _ in range(8))

    def _fresh_voucher(self) -> str:
        """A voucher not already present in the table, so open()'s INSERT can't
        hit the UNIQUE constraint. Falls back to a random one after a few tries."""
        for _ in range(10):
            v = self.gen_voucher()
            if not self.repo.get_by_voucher(v):
                return v
        return self.gen_voucher()

    def reap_stale(self) -> None:
        self.repo.reap_stale(self.stale_ms)

    def active(self):
        return self.repo.active()

    def get_by_voucher(self, voucher: str):
        return self.repo.get_by_voucher(voucher)

    def get_or_active(self, voucher: Optional[str]):
        s = self.repo.get_by_voucher(voucher) if voucher else None
        return s or self.repo.active()

    @staticmethod
    def mac_matches(session, mac: str) -> bool:
        return bool(session and mac and session["Mac"] and
                    session["Mac"].upper() == mac.upper())

    def begin(self, voucher: str, mac: str, ip: str, extend: bool):
        """Open a session (or reuse an existing OPEN one for the same phone).
        Returns (session_row, error_code|None)."""
        with self._lock:
            self.reap_stale()
            current = self.repo.active()
            if current:
                same_mac = self.mac_matches(current, mac)
                same_voucher = voucher and current["Voucher"] == voucher
                if same_mac or same_voucher:
                    return current, None          # same phone tapped INSERT again
                return None, "coinslot.busy"       # another session in progress
            if not voucher:
                voucher = self._fresh_voucher()
            existing = self.repo.get_by_voucher(voucher)
            # A resent voucher whose row still exists (non-OPEN — active() above
            # already handled the OPEN case) would collide on INSERT.
            if existing and extend:
                # Extend must KEEP the hotspot username, so reopen the existing
                # row (fresh count) rather than INSERT a duplicate. The portal
                # clears the old RewardGrant first so this cycle grants anew.
                self._last_reported[voucher] = 0
                s = self.repo.reopen(voucher, mac, ip, extend, self.minutes,
                                     self.max_bottles, self.wait_ms)
                log.info("session REOPEN (extend) voucher=%s mac=%s", voucher, mac)
                return s, None
            if existing:
                # A stale client voucher (localStorage) for a fresh insert — just
                # mint a new code.
                log.info("voucher %s already used — issuing a fresh one", voucher)
                voucher = self._fresh_voucher()
            self._last_reported[voucher] = 0
            s = self.repo.open(voucher, mac, ip, extend, self.minutes,
                               self.max_bottles, self.wait_ms)
            log.info("session OPEN voucher=%s mac=%s extend=%s", voucher, mac, extend)
            return s, None

    def credit_bottle(self, voucher: str) -> Tuple[int, int]:
        """Credit one accepted bottle to the open session. Returns (total, new)."""
        with self._lock:
            s = self.repo.get_by_voucher(voucher)
            if not s or s["State"] != "OPEN":
                return (s["BottleCount"] if s else 0, 0)
            if s["BottleCount"] >= s["MaxBottles"]:
                return (s["BottleCount"], 0)       # cap reached
            new_count = s["BottleCount"] + 1
            time_added = new_count * s["MinutesPerBottle"] * 60
            self.repo.update_counts(voucher, new_count, time_added)
            log.info("credited bottle voucher=%s count=%d", voucher, new_count)
            return (new_count, 1)

    def new_bottles_since_poll(self, voucher: str, current_count: int) -> int:
        with self._lock:
            last = self._last_reported.get(voucher, 0)
            self._last_reported[voucher] = current_count
            return max(0, current_count - last)

    def remaining_wait_ms(self, session) -> int:
        try:
            updated = datetime.fromisoformat(session["UpdatedAt"])
        except Exception:  # noqa: BLE001
            return 0
        elapsed_ms = (datetime.now() - updated).total_seconds() * 1000
        return max(0, int(session["WaitTimeMs"] - elapsed_ms))

    def cancel(self, voucher: str) -> None:
        self.repo.set_state(voucher, "CANCELLED")
        self._last_reported.pop(voucher, None)

    def set_state(self, voucher: str, state: str) -> None:
        self.repo.set_state(voucher, state)
