"""Reward Service

Ties physical detections to portal sessions and grants Wi-Fi, idempotently.

Guarantees (docs/architecture/02-low-level-architecture.md §8):
  1. one DetectionTransaction per physical event (UNIQUE id, Phase 10)
  2. a bottle credits a session at most once (CreditedToSession flag)
  3. one RewardGrant per session (UNIQUE SessionId)
  4. absolute TargetLimitSeconds SET on MikroTik -> retries never double-grant

Implemented in Phase 16.
"""

from __future__ import annotations

import logging

log = logging.getLogger("reward")


def _ceil_minutes(seconds: int) -> int:
    return (max(0, int(seconds)) + 59) // 60


class RewardService:
    def __init__(self, cfg, detection_repo, reward_repo, session_repo, session_mgr,
                 mikrotik) -> None:
        self.cfg = cfg
        self.detections = detection_repo
        self.rewards = reward_repo
        self.sessions = session_repo
        self.session_mgr = session_mgr
        self.mikrotik = mikrotik

    # ---------- credit a physical ACCEPT to the open session ----------
    def credit_accepted_bottle(self, txn_id: str, session) -> bool:
        if not session:
            log.info("ACCEPT with no open session — not credited")
            return False
        txn = self.detections.get(txn_id)
        if txn and txn["CreditedToSession"]:
            return True  # idempotent: already credited
        _total, new = self.session_mgr.credit_bottle(session["Voucher"])
        if new > 0:
            self.detections.set_session_voucher(txn_id, session["Voucher"])
            self.detections.mark_credited(txn_id)
            return True
        return False

    def clear_grant_for_session(self, session_id) -> None:
        """Drop any RewardGrant for this session so a reopened EXTEND session
        grants fresh (remaining + new) rather than short-circuiting on the
        previous cycle's SUCCESS grant."""
        try:
            self.rewards.delete_by_session(session_id)
        except Exception as exc:  # noqa: BLE001 - never block a top-up on this
            log.warning("clear_grant_for_session(%s) failed: %s", session_id, exc)

    # ---------- grant Wi-Fi at Done (/useVoucher) ----------
    def grant_for_session(self, session) -> dict:
        voucher = session["Voucher"]
        sid = session["Id"]
        mac, ip = session["Mac"], session["Ip"]
        bottles = session["BottleCount"]
        minutes = session["MinutesPerBottle"]
        extend = bool(session["ExtendTime"])
        add_sec = bottles * minutes * 60

        existing = self.rewards.get_by_session(sid)
        if existing and existing["Status"] == "SUCCESS":
            target = existing["TargetLimitSeconds"]
            return {"ok": True, "status": "SUCCESS", "voucher": voucher,
                    "validity_minutes": _ceil_minutes(target),
                    "time_added": existing["MinutesGranted"] * 60,
                    "total_seconds": target, "logged_in": True, "detail": "already granted"}

        if existing is None:
            remaining = self.mikrotik.remaining_seconds(voucher) if extend else 0
            target = remaining + add_sec
            gid = self.rewards.create(sid, voucher, mac, ip, bottles, bottles * minutes,
                                      target, extend)
        else:
            gid = existing["GrantId"]
            target = existing["TargetLimitSeconds"]  # reuse stored target (retry-safe)

        ok_set, detail = self.mikrotik.set_user_absolute(voucher, target)
        if ok_set:
            self.mikrotik.kick(voucher, mac)
            logged_in, _ = self.mikrotik.login_mac(voucher, mac, ip)
            self.rewards.mark_success(gid)
            self.session_mgr.set_state(voucher, "FINALIZED")
            log.info("reward SUCCESS voucher=%s target=%ds logged_in=%s", voucher, target, logged_in)
            return {"ok": True, "status": "SUCCESS", "voucher": voucher,
                    "validity_minutes": _ceil_minutes(target), "time_added": add_sec,
                    "total_seconds": target, "logged_in": logged_in, "detail": "granted"}

        self.rewards.update_attempt(gid, detail)
        self.session_mgr.set_state(voucher, "FINALIZING")
        log.warning("reward PENDING voucher=%s (%s) — will retry", voucher, detail)
        return {"ok": False, "status": "PENDING", "voucher": voucher,
                "validity_minutes": _ceil_minutes(target), "time_added": add_sec,
                "total_seconds": target, "logged_in": False, "detail": detail}

    # ---------- drain PENDING grants (reward-retry thread) ----------
    def retry_pending(self) -> int:
        pending = self.rewards.list_pending()
        done = 0
        for g in pending:
            voucher, target, gid = g["Voucher"], g["TargetLimitSeconds"], g["GrantId"]
            ok, detail = self.mikrotik.set_user_absolute(voucher, target)
            if ok:
                self.mikrotik.login_mac(voucher, g["Mac"], g["Ip"])  # best effort
                self.rewards.mark_success(gid)
                self.session_mgr.set_state(voucher, "FINALIZED")
                log.info("PENDING reward applied: %s", gid)
                done += 1
            else:
                self.rewards.update_attempt(gid, detail)
        return done
