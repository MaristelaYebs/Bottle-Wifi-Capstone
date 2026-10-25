"""Phase 15 unit tests — MikroTik helpers + unconfigured safety.

Pure helpers + guard behaviour (no librouteros / no router needed).
The live RouterOS calls are verified on the Pi against the real hAP lite.
Run:  python tests/test_mikrotik_service.py   (or pytest)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import MikroTikSecrets  # noqa: E402
from services.mikrotik_service import (  # noqa: E402
    MikroTikService, _fmt_uptime, _norm_mac, _parse_uptime,
)


class FakeCfg:
    def __init__(self, mt=None):
        self.mikrotik = mt or MikroTikSecrets()

    def get(self, key, default=None):
        return default


def test_fmt_uptime():
    assert _fmt_uptime(0) == "0s"
    assert _fmt_uptime(90) == "1m30s"
    assert _fmt_uptime(3661) == "1h1m1s"
    assert _fmt_uptime(-5) == "0s"
    print("ok: _fmt_uptime")


def test_parse_uptime():
    assert _parse_uptime("15m") == 900
    assert _parse_uptime("1h1m1s") == 3661
    assert _parse_uptime("900") == 900
    assert _parse_uptime("00:15:00") == 900
    assert _parse_uptime("1d2h") == 86400 + 7200
    assert _parse_uptime("") == 0
    print("ok: _parse_uptime")


def test_uptime_round_trip():
    for sec in (0, 5, 60, 900, 3661, 93600):
        assert _parse_uptime(_fmt_uptime(sec)) == sec, sec
    print("ok: fmt/parse round-trip (absolute-limit safety)")


def test_norm_mac():
    assert _norm_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert _norm_mac("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"
    assert _norm_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert _norm_mac("nope") == ""
    assert _norm_mac(None) == ""
    print("ok: _norm_mac")


def test_unconfigured_is_safe():
    svc = MikroTikService(FakeCfg())  # blank .env -> not configured
    assert svc.configured is False
    ok, _ = svc.test_connection()
    assert ok is False
    ok, _ = svc.set_user_absolute("VOUCHER1", 900)
    assert ok is False
    assert svc.find_active_user_by_mac("aabbccddeeff") is None
    assert svc.remaining_seconds("VOUCHER1") == 0
    print("ok: unconfigured MikroTik never touches a router, fails safe")


def test_password_mode():
    # username_only=False -> password is the voucher (loginOption 1, current setup)
    mt = MikroTikSecrets(host="10.10.10.1", username="vendo", password="x",
                         username_only=False)
    svc = MikroTikService(FakeCfg(mt))
    assert svc._password_for("ABCD1234") == "ABCD1234"
    mt2 = MikroTikSecrets(host="10.10.10.1", username="vendo", password="x",
                          username_only=True)
    assert MikroTikService(FakeCfg(mt2))._password_for("ABCD1234") == ""
    print("ok: password mode matches login option")


def _run_all():
    test_fmt_uptime()
    test_parse_uptime()
    test_uptime_round_trip()
    test_norm_mac()
    test_unconfigured_is_safe()
    test_password_mode()
    print("\nALL PHASE 15 MIKROTIK TESTS PASSED")


if __name__ == "__main__":
    _run_all()
