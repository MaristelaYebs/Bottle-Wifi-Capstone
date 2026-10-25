"""Phase 13 unit tests — FAR/FRR metrics + ModelVersion registry.

Pure logic + in-memory sqlite. Run:  python tests/test_validation.py  (or pytest)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.metrics import compute_reward_metrics, evaluate_thresholds  # noqa: E402
from repositories.db import Database  # noqa: E402
from repositories.model_repository import ModelRepository  # noqa: E402


# ----------------------- metrics -----------------------
def test_perfect_classifier():
    m = compute_reward_metrics([(True, True)] * 5 + [(False, False)] * 5)
    assert m.far == 0.0 and m.frr == 0.0
    assert m.precision == 1.0 and m.recall == 1.0 and m.accuracy == 1.0
    print("ok: perfect -> FAR=FRR=0")


def test_false_acceptance():
    # 4 non-bottles, 1 wrongly accepted -> FAR 0.25; bottles all correct
    m = compute_reward_metrics([(True, True), (True, True),
                                (False, True), (False, False), (False, False), (False, False)])
    assert m.fp == 1 and m.far == 0.25
    assert m.frr == 0.0
    print("ok: false acceptance computed (FAR=0.25)")


def test_false_rejection():
    # 4 bottles, 1 wrongly rejected -> FRR 0.25
    m = compute_reward_metrics([(True, True), (True, True), (True, True), (True, False),
                                (False, False)])
    assert m.fn == 1 and m.frr == 0.25
    assert m.far == 0.0
    print("ok: false rejection computed (FRR=0.25)")


def test_threshold_gate():
    good = compute_reward_metrics([(True, True)] * 10 + [(False, False)] * 10)
    ok, reasons = evaluate_thresholds(good, map50=0.9, max_far=0.02, min_recall=0.9, min_map50=0.8)
    assert ok and reasons == []

    bad_far = compute_reward_metrics([(False, True)] * 5 + [(False, False)] * 5 + [(True, True)] * 10)
    ok, reasons = evaluate_thresholds(bad_far, map50=0.9, max_far=0.02, min_recall=0.9, min_map50=0.8)
    assert not ok and any("FAR" in r and "CRITICAL" in r for r in reasons)

    ok, reasons = evaluate_thresholds(good, map50=0.5, max_far=0.02, min_recall=0.9, min_map50=0.8)
    assert not ok and any("mAP50" in r for r in reasons)
    print("ok: threshold gate (pass, FAR-fail critical, mAP50-fail)")


# ----------------------- model registry -----------------------
def _db():
    db = Database(":memory:")
    db.connect()
    return db


def test_upsert_and_get():
    repo = ModelRepository(_db())
    repo.upsert("bottle-v1", "candidate",
                metrics={"precision": 0.95, "recall": 0.93, "map50": 0.88, "far": 0.01, "frr": 0.04})
    row = repo.get("bottle-v1")
    assert row["Stage"] == "candidate"
    assert abs(row["FalseAcceptanceRate"] - 0.01) < 1e-9
    # update in place
    repo.upsert("bottle-v1", "archive", metrics={"map50": 0.90})
    row = repo.get("bottle-v1")
    assert row["Stage"] == "archive" and abs(row["Map50"] - 0.90) < 1e-9
    print("ok: upsert insert + update")


def test_set_active_single():
    repo = ModelRepository(_db())
    repo.upsert("bottle-v1", "production")
    repo.upsert("bottle-v2", "candidate")
    repo.set_active("bottle-v1")
    repo.set_active("bottle-v2")  # switching active
    active = repo.get_active()
    assert active["Version"] == "bottle-v2" and active["Active"] == 1
    assert repo.get("bottle-v1")["Active"] == 0
    assert active["Stage"] == "production"
    print("ok: exactly one active model at a time")


def _run_all():
    test_perfect_classifier()
    test_false_acceptance()
    test_false_rejection()
    test_threshold_gate()
    test_upsert_and_get()
    test_set_active_single()
    print("\nALL PHASE 13 VALIDATION TESTS PASSED")


if __name__ == "__main__":
    _run_all()
