"""Phase 16 tests — portal sessions, reward idempotency, PENDING retry.

Real DB + repositories + services with a FAKE MikroTik (no router). Covers the
four idempotency guarantees and the JuanFi endpoint behaviour.
Run:  python tests/test_reward_flow.py   (or pytest)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.types import Decision  # noqa: E402
from repositories.db import Database  # noqa: E402
from repositories.detection_repository import DetectionRepository  # noqa: E402
from repositories.reward_repository import RewardRepository  # noqa: E402
from repositories.session_repository import SessionRepository  # noqa: E402
from services.mikrotik_service import _norm_mac  # noqa: E402
from services.portal_api_service import PortalApi  # noqa: E402
from services.reward_service import RewardService  # noqa: E402
from services.session_service import SessionManager  # noqa: E402


class FakeCfg:
    def get(self, key, default=None):
        return {
            "reward.minutes_per_bottle": 15,
            "reward.max_bottles_per_session": 5,
            "reward.wait_time_ms": 90000,
            "reward.session_stale_ms": 180000,
            "portal_api.static_ip_hint": "10.10.10.252",
        }.get(key, default)


class FakeMikroTik:
    def __init__(self, up=True):
        self.up = up
        self.configured = True
        self.set_calls = []
        self.login_calls = []
        self.remaining = {}
        self.active_by_mac = {}

    def test_connection(self):
        return (self.up, "ok" if self.up else "down")

    def remaining_seconds(self, voucher):
        return self.remaining.get(voucher, 0)

    def find_active_user_by_mac(self, mac):
        return self.active_by_mac.get(_norm_mac(mac))

    def find_active_user_by_ip(self, ip):
        return None

    def set_user_absolute(self, voucher, target):
        if not self.up:
            return (False, "down")
        self.set_calls.append((voucher, target))
        return (True, "set")

    def kick(self, voucher, mac):
        return (True, "kicked")

    def login_mac(self, voucher, mac, ip):
        self.login_calls.append(voucher)
        return (True, "logged-in")


def _stack(mt=None):
    cfg = FakeCfg()
    db = Database(":memory:")
    db.connect()
    det = DetectionRepository(db)
    smgr = SessionManager(cfg, SessionRepository(db))
    mt = mt or FakeMikroTik()
    rew = RewardService(cfg, det, RewardRepository(db), SessionRepository(db), smgr, mt)
    portal = PortalApi(cfg, smgr, rew, mt, camera=None)
    return cfg, db, det, smgr, rew, portal, mt


def _accept():
    return Decision("ACCEPT", "BOTTLE", "plastic_bottle", 0.95, 5, 5, ["ok"], False)


def _drop_bottle(det, smgr, rew):
    txn = det.insert(_accept(), "bottle-v1")
    rew.credit_accepted_bottle(txn, smgr.active())
    return txn


# ----------------------- happy path -----------------------
def test_full_happy_path():
    _cfg, _db, det, smgr, rew, portal, mt = _stack()
    r = portal.top_up("", "AA:BB:CC:DD:EE:01", "10.0.0.5", False)
    assert r["status"] == "true"
    v = r["voucher"]

    _drop_bottle(det, smgr, rew)

    c = portal.check_coin(v)
    assert c["status"] == "true" and c["newCoin"] == "1" and c["totalCoin"] == "1"
    assert c["timeAdded"] == "900"                      # 1 * 15 * 60

    c2 = portal.check_coin(v)                            # no new coin since last poll
    assert c2["status"] == "false" and c2["errorCode"] == "coin.not.inserted"

    d = portal.use_voucher(v)
    assert d["status"] == "true" and d["validity"] == "15" and d["loggedIn"] is True
    assert mt.set_calls == [(v, 900)]
    print("ok: topUp -> bottle -> checkCoin -> useVoucher grants 15 min")


# ----------------------- idempotency -----------------------
def test_credit_not_double_counted():
    _cfg, _db, det, smgr, rew, portal, mt = _stack()
    v = portal.top_up("", "AA:BB:CC:DD:EE:02", "10.0.0.6", False)["voucher"]
    txn = det.insert(_accept(), "bottle-v1")
    assert rew.credit_accepted_bottle(txn, smgr.active()) is True
    assert rew.credit_accepted_bottle(txn, smgr.active()) is True   # replay
    assert smgr.active()["BottleCount"] == 1                        # not 2
    print("ok: same detection credits a session at most once")


def test_reward_not_double_granted():
    _cfg, _db, det, smgr, rew, portal, mt = _stack()
    v = portal.top_up("", "AA:BB:CC:DD:EE:03", "10.0.0.7", False)["voucher"]
    _drop_bottle(det, smgr, rew)
    portal.check_coin(v)
    d1 = portal.use_voucher(v)
    d2 = portal.use_voucher(v)                          # user taps Done twice
    assert d1["status"] == "true" and d2["status"] == "true"
    assert len(mt.set_calls) == 1                       # only one grant to MikroTik
    print("ok: repeated useVoucher does not double-grant")


# ----------------------- MikroTik down -> PENDING -> retry -----------------------
def test_mikrotik_down_then_retry():
    mt = FakeMikroTik(up=False)
    _cfg, _db, det, smgr, rew, portal, _mt = _stack(mt)
    v = portal.top_up("", "AA:BB:CC:DD:EE:04", "10.0.0.8", False)["voucher"]
    _drop_bottle(det, smgr, rew)

    d = portal.use_voucher(v)
    assert d["status"] == "false" and d["errorCode"] == "mikrotik.failed"
    assert mt.set_calls == []                           # nothing granted yet

    mt.up = True
    assert rew.retry_pending() == 1                     # drained
    assert mt.set_calls == [(v, 900)]
    assert rew.retry_pending() == 0                     # idempotent: nothing left
    assert len(mt.set_calls) == 1                       # no double grant
    print("ok: MikroTik down -> PENDING -> retry grants once, never twice")


# ----------------------- busy / edge cases -----------------------
def test_busy_rejects_other_phone():
    _cfg, _db, det, smgr, rew, portal, mt = _stack()
    a = portal.top_up("", "AA:BB:CC:DD:EE:05", "10.0.0.9", False)
    assert a["status"] == "true"
    b = portal.top_up("", "AA:BB:CC:DD:EE:06", "10.0.0.10", False)
    assert b["status"] == "false" and b["errorCode"] == "coinslot.busy"
    a2 = portal.top_up("", "AA:BB:CC:DD:EE:05", "10.0.0.9", False)   # same phone
    assert a2["status"] == "true" and a2["voucher"] == a["voucher"]
    print("ok: second phone gets coinslot.busy; same phone reuses session")


def test_usevoucher_without_bottles():
    _cfg, _db, det, smgr, rew, portal, mt = _stack()
    v = portal.top_up("", "AA:BB:CC:DD:EE:07", "10.0.0.11", False)["voucher"]
    d = portal.use_voucher(v)
    assert d["status"] == "false" and d["errorCode"] == "coin.not.inserted"
    assert mt.set_calls == []
    print("ok: Done with 0 bottles -> no grant")


def test_extend_adds_previous_plus_new():
    # Full extend flow: grant a first session (15 min), then EXTEND with the SAME
    # hotspot username and add 2 more bottles. The new grant must be
    # remaining(previous) + new, NOT the previous total again, and NOT crash on
    # the reused voucher (UNIQUE -> old "ERROR").
    _cfg, _db, det, smgr, rew, portal, mt = _stack()
    mac = "AA:BB:CC:DD:EE:08"
    v = portal.top_up("", mac, "10.0.0.12", False)["voucher"]
    _drop_bottle(det, smgr, rew)                     # 1 bottle
    d1 = portal.use_voucher(v)                        # grant 900s (15 min)
    assert d1["status"] == "true" and mt.set_calls[-1] == (v, 900)

    # user is now online as v with ~900s remaining; they tap EXTEND
    mt.active_by_mac[_norm_mac(mac)] = v
    mt.remaining[v] = 900                             # what's left on the router
    r = portal.top_up(v, mac, "10.0.0.12", True)      # EXTEND (extendTime=1)
    assert r["status"] == "true" and r["voucher"] == v   # no UNIQUE crash
    s = smgr.active()
    assert s and s["Voucher"] == v and s["BottleCount"] == 0 and bool(s["ExtendTime"])

    _drop_bottle(det, smgr, rew)
    _drop_bottle(det, smgr, rew)                      # 2 more bottles = 1800s
    d2 = portal.use_voucher(v)
    assert d2["status"] == "true"
    assert mt.set_calls[-1] == (v, 900 + 1800), mt.set_calls   # remaining + new
    print("ok: EXTEND adds previous remaining + new credit (900 + 1800 = 2700s)")


def test_get_rates():
    *_ , portal, _mt = _stack()
    assert portal.get_rates() == "1 bottle (250ml-1L)#0#0#15#"
    print("ok: getRates line")


def _run_all():
    test_full_happy_path()
    test_credit_not_double_counted()
    test_reward_not_double_granted()
    test_mikrotik_down_then_retry()
    test_busy_rejects_other_phone()
    test_usevoucher_without_bottles()
    test_extend_adds_previous_plus_new()
    test_get_rates()
    print("\nALL PHASE 16 REWARD-FLOW TESTS PASSED")


if __name__ == "__main__":
    _run_all()
