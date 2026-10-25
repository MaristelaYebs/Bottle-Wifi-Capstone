"""Phase 17 unit tests — HealthService readiness gating + metrics cadence.

Providers are injected; psutil is optional (metrics degrade to 0 if absent).
Run:  python tests/test_health_service.py   (or pytest)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.health_service import HealthService  # noqa: E402


class FakeCfg:
    def get(self, key, default=None):
        return {
            "health.disk_warn_mb": 500,
            "health.ram_warn_pct": 85,
            "health.cpu_temp_warn_c": 75,
            "health.mikrotik_every": 3,
        }.get(key, default)


def _svc(serial=True, model=True, db=True, camera=True, mikrotik=True, mt_counter=None):
    def mt():
        if mt_counter is not None:
            mt_counter[0] += 1
        return mikrotik
    return HealthService(FakeCfg(), serial_ok=lambda: serial, model_ok=lambda: model,
                         db_ok=lambda: db, camera_ok=lambda: camera, mikrotik_ok=mt,
                         disk_path=".")


def test_all_up_is_ready():
    hr = _svc().snapshot()
    assert hr.serial and hr.model and hr.database and hr.camera
    assert hr.is_ready() is True
    print("ok: all subsystems up -> ready")


def test_critical_down_not_ready():
    for kw in ("serial", "model", "db", "camera"):
        hr = _svc(**{kw: False}).snapshot()
        assert hr.is_ready() is False, kw
    print("ok: any critical subsystem down -> not ready")


def test_mikrotik_degraded_not_fatal():
    hr = _svc(mikrotik=False).snapshot()
    assert hr.mikrotik is False
    assert hr.is_ready() is True  # mikrotik is degraded-not-fatal
    print("ok: mikrotik down is degraded, still ready")


def test_provider_exception_is_safe():
    def boom():
        raise RuntimeError("x")
    svc = HealthService(FakeCfg(), serial_ok=boom, model_ok=lambda: True,
                        db_ok=lambda: True, camera_ok=lambda: True,
                        mikrotik_ok=lambda: True, disk_path=".")
    hr = svc.snapshot()
    assert hr.serial is False and hr.is_ready() is False
    print("ok: a raising probe counts as down, never crashes")


def test_mikrotik_polled_on_cadence():
    counter = [0]
    svc = _svc(mt_counter=counter)
    svc.snapshot(); svc.snapshot(); svc.snapshot()   # every=3 -> only tick0 calls
    assert counter[0] == 1, counter
    svc.snapshot()                                    # tick3 -> calls again
    assert counter[0] == 2
    print("ok: mikrotik polled every Nth snapshot (cached between)")


def test_warnings():
    svc = _svc(camera=False, mikrotik=False)
    w = svc.warnings(svc.snapshot())
    assert any("camera" in x for x in w)
    assert any("mikrotik" in x for x in w)
    print("ok: warnings list reflects down subsystems")


def _run_all():
    test_all_up_is_ready()
    test_critical_down_not_ready()
    test_mikrotik_degraded_not_fatal()
    test_provider_exception_is_safe()
    test_mikrotik_polled_on_cadence()
    test_warnings()
    print("\nALL PHASE 17 HEALTH TESTS PASSED")


if __name__ == "__main__":
    _run_all()
