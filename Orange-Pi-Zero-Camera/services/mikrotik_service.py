"""MikroTik Service

RouterOS API client for the existing hAP lite hotspot. Creates/extends a hotspot
user (ABSOLUTE limit — retry-safe), finds the active user by MAC/IP, kicks stale
sessions, and logs a phone in by MAC. NEVER changes hotspot/DHCP/NAT/firewall
config — it only manages hotspot *users* and *active* sessions.

Ported from ESP32-CAM-Vendo/src/mikrotik_api.cpp. Credentials come from .env via
config.mikrotik. Implemented in Phase 15.

librouteros is imported lazily (only needed on the Pi / with a real router).
Pure helpers (_fmt_uptime/_parse_uptime/_norm_mac) are unit-tested.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, List, Optional, Tuple

log = logging.getLogger("mikrotik")


# ----------------------------- pure helpers -----------------------------
def _fmt_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m{s}s"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def _parse_uptime(text: str) -> int:
    if not text:
        return 0
    text = str(text).strip()
    if ":" in text:  # HH:MM:SS or MM:SS
        parts = [int(p or 0) for p in text.split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0]
    units = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
    total, val, saw = 0, 0, False
    for ch in text:
        if ch.isdigit():
            val = val * 10 + int(ch)
            saw = True
        elif ch.lower() in units:
            total += val * units[ch.lower()]
            val, saw = 0, False
    if saw and total == 0:
        total = val  # bare number -> seconds
    return total


def _norm_mac(mac: Optional[str]) -> str:
    if not mac:
        return ""
    hexs = "".join(c for c in mac.upper() if c in "0123456789ABCDEF")
    if len(hexs) != 12:
        return ""
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))


# ----------------------------- service -----------------------------
class MikroTikService:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.mt = cfg.mikrotik  # MikroTikSecrets
        self.profile = self.mt.hotspot_profile
        self.username_only = self.mt.username_only

    @property
    def configured(self) -> bool:
        return self.mt.configured

    def _password_for(self, voucher: str) -> str:
        return "" if self.username_only else voucher

    @contextmanager
    def _api(self) -> Iterator:
        from librouteros import connect  # type: ignore

        kwargs = dict(host=self.mt.host, username=self.mt.username,
                      password=self.mt.password, port=self.mt.port, timeout=5)
        if self.mt.use_ssl:
            import ssl

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            kwargs["ssl_wrapper"] = ctx.wrap_socket
        api = connect(**kwargs)
        try:
            yield api
        finally:
            try:
                api.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------- diagnostics ----------
    def test_connection(self) -> Tuple[bool, str]:
        if not self.configured:
            return False, "MikroTik not configured (.env)"
        try:
            with self._api() as api:
                tuple(api("/system/identity/print"))
            return True, "login OK"
        except Exception as exc:  # noqa: BLE001
            return False, f"connect/login failed: {exc}"

    # ---------- queries ----------
    def _users(self, api) -> List[dict]:
        return list(api("/ip/hotspot/user/print"))

    def _active(self, api) -> List[dict]:
        return list(api("/ip/hotspot/active/print"))

    def _find_user(self, api, voucher: str) -> Optional[dict]:
        for u in self._users(api):
            if u.get("name") == voucher:
                return u
        return None

    def find_active_user_by_mac(self, mac: str) -> Optional[str]:
        macf = _norm_mac(mac)
        if not macf or not self.configured:
            return None
        try:
            with self._api() as api:
                for a in self._active(api):
                    if a.get("mac-address", "").upper() == macf:
                        return a.get("user")
        except Exception as exc:  # noqa: BLE001
            log.warning("find_active_user_by_mac failed: %s", exc)
        return None

    def find_active_user_by_ip(self, ip: str) -> Optional[str]:
        if not ip or not self.configured:
            return None
        try:
            with self._api() as api:
                for a in self._active(api):
                    if a.get("address") == ip:
                        return a.get("user")
        except Exception as exc:  # noqa: BLE001
            log.warning("find_active_user_by_ip failed: %s", exc)
        return None

    def remaining_seconds(self, voucher: str) -> int:
        if not voucher or not self.configured:
            return 0
        try:
            with self._api() as api:
                u = self._find_user(api, voucher)
                if not u:
                    return 0
                limit = _parse_uptime(u.get("limit-uptime", "0"))
                used = _parse_uptime(u.get("uptime", "0"))
                return max(0, limit - used) if limit > 0 else 0
        except Exception as exc:  # noqa: BLE001
            log.warning("remaining_seconds failed: %s", exc)
            return 0

    # ---------- mutations ----------
    def set_user_absolute(self, voucher: str, target_seconds: int) -> Tuple[bool, str]:
        """Create the user (if missing) or SET its limit-uptime to an ABSOLUTE
        target. Idempotent: re-running with the same target is a no-op change, so
        reward retries can never stack extra time."""
        if not self.configured:
            return False, "MikroTik not configured"
        uptime = _fmt_uptime(target_seconds)
        pw = self._password_for(voucher)
        try:
            with self._api() as api:
                existing = self._find_user(api, voucher)
                if existing:
                    tuple(api("/ip/hotspot/user/set",
                              **{".id": existing[".id"], "limit-uptime": uptime}))
                    return True, f"set limit={uptime}"
                tuple(api("/ip/hotspot/user/add",
                          name=voucher, password=pw, profile=self.profile,
                          **{"limit-uptime": uptime}))
                return True, f"created limit={uptime}"
        except Exception as exc:  # noqa: BLE001
            return False, f"set_user_absolute failed: {exc}"

    def kick(self, voucher: Optional[str], mac: Optional[str]) -> Tuple[bool, str]:
        if not self.configured:
            return False, "MikroTik not configured"
        macf = _norm_mac(mac)
        try:
            with self._api() as api:
                ids = set()
                for a in self._active(api):
                    if (voucher and a.get("user") == voucher) or \
                       (macf and a.get("mac-address", "").upper() == macf):
                        ids.add(a[".id"])
                for _id in ids:
                    tuple(api("/ip/hotspot/active/remove", **{".id": _id}))
                return True, f"kicked={len(ids)}"
        except Exception as exc:  # noqa: BLE001
            return False, f"kick failed: {exc}"

    def login_mac(self, voucher: str, mac: str, ip: Optional[str]) -> Tuple[bool, str]:
        if not self.configured:
            return False, "MikroTik not configured"
        macf = _norm_mac(mac)
        if not voucher or not macf:
            return False, "missing voucher/mac"
        # try the configured password mode first, then the alternate
        pw_primary = self._password_for(voucher)
        pw_alt = voucher if self.username_only else ""
        try:
            with self._api() as api:
                last = ""
                for pw in (pw_primary, pw_alt):
                    kw = {"user": voucher, "password": pw, "mac-address": macf}
                    if ip:
                        kw["ip"] = ip
                    try:
                        tuple(api("/ip/hotspot/active/login", **kw))
                        return True, "logged-in"
                    except Exception as exc:  # noqa: BLE001
                        last = str(exc)
                return False, f"login failed: {last}"
        except Exception as exc:  # noqa: BLE001
            return False, f"login connect failed: {exc}"
