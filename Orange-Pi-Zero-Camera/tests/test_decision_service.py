"""Phase 7 unit tests — decision engine + detector selection (pure, no hardware).

Run:  python tests/test_decision_service.py   (or pytest)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.types import Detection  # noqa: E402
from services.decision_service import DecisionService  # noqa: E402
from services.detector_service import MockDetector, build_detector  # noqa: E402


class FakeCfg:
    def __init__(self, **over):
        self._d = {
            "detection.confidence": 0.70,
            "detection.required_positive_frames": 3,
            "detection.borderline_margin": 0.15,
            "detection.backend": "mock",
        }
        self._d.update(over)

    def get(self, key, default=None):
        return self._d.get(key, default)


def bottle(conf):
    return Detection(0, "plastic_bottle", conf, (0, 0, 10, 10))


def reject(conf):
    return Detection(1, "reject_object", conf, (0, 0, 10, 10))


def frames(*per_frame):
    return list(per_frame)


# ----------------------- ACCEPT paths -----------------------
def test_accept_all_positive():
    d = DecisionService(FakeCfg())
    dec = d.decide(frames([bottle(0.92)], [bottle(0.94)], [bottle(0.91)],
                          [bottle(0.89)], [bottle(0.93)]))
    assert dec.outcome == "ACCEPT" and dec.logical == "BOTTLE"
    assert dec.positive_frames == 5 and dec.total_frames == 5
    assert 0.90 <= dec.confidence <= 0.92
    assert dec.collect_sample is False
    print("ok: 5/5 -> ACCEPT")


def test_accept_three_of_five():
    d = DecisionService(FakeCfg())
    dec = d.decide(frames([bottle(0.80)], [], [bottle(0.85)], [], [bottle(0.90)]))
    assert dec.outcome == "ACCEPT"
    assert dec.positive_frames == 3
    # accepted but with empty frames mixed in -> still accept (>=3), not collected
    assert dec.collect_sample is False
    print("ok: 3/5 -> ACCEPT")


# ----------------------- REJECT paths -----------------------
def test_reject_object():
    d = DecisionService(FakeCfg())
    dec = d.decide(frames([reject(0.82)], [reject(0.80)], [], [reject(0.78)], []))
    assert dec.outcome == "REJECT" and dec.logical == "REJECT"
    assert dec.prediction == "reject_object"
    assert dec.collect_sample is True
    print("ok: reject_object -> REJECT (collected)")


def test_empty_scene():
    d = DecisionService(FakeCfg())
    dec = d.decide(frames([], [], [], [], []))
    assert dec.outcome == "REJECT" and dec.logical == "EMPTY"
    assert dec.prediction == "none" and dec.confidence == 0.0
    assert dec.collect_sample is True
    print("ok: empty -> REJECT/EMPTY (collected)")


def test_low_confidence_below_gate():
    d = DecisionService(FakeCfg())
    # bottles present but under 0.70 -> not positive -> REJECT + borderline collect
    dec = d.decide(frames([bottle(0.60)], [bottle(0.62)], [bottle(0.58)],
                          [bottle(0.61)], [bottle(0.59)]))
    assert dec.outcome == "REJECT" and dec.logical == "EMPTY"
    assert dec.positive_frames == 0
    assert dec.collect_sample is True
    assert any("borderline" in r for r in dec.reasons)
    print("ok: sub-gate bottles -> REJECT + borderline collect")


def test_disagreement_two_of_five():
    d = DecisionService(FakeCfg())
    dec = d.decide(frames([bottle(0.90)], [bottle(0.88)], [], [], []))
    assert dec.outcome == "REJECT"           # only 2/5 positive
    assert dec.positive_frames == 2
    assert dec.collect_sample is True
    assert any("only 2/5" in r for r in dec.reasons)
    print("ok: 2/5 disagreement -> REJECT (collected)")


def test_multi_object_collect():
    d = DecisionService(FakeCfg())
    # a frame with two above-gate detections -> multi-object flag
    multi_frame = [bottle(0.90), reject(0.85)]
    dec = d.decide(frames(multi_frame, [bottle(0.91)], [bottle(0.92)], [bottle(0.9)], [bottle(0.9)]))
    assert dec.collect_sample is True
    assert any("multiple objects" in r for r in dec.reasons)
    print("ok: multi-object frame flagged for collection")


def test_exception_fails_safe():
    d = DecisionService(FakeCfg())
    # malformed: not iterable detections -> decision must fail safe, not crash
    dec = d.decide([object()])  # frame is not a list of detections
    assert dec.outcome == "REJECT"
    assert dec.collect_sample is True
    print("ok: malformed input -> fail-safe REJECT")


# ----------------------- detector selection -----------------------
def test_build_mock_detector():
    det = build_detector(FakeCfg(**{"detection.backend": "mock"}))
    assert isinstance(det, MockDetector)
    assert det.is_loaded() and det.version() == "mock"
    assert det.infer(None) == []  # default empty -> EMPTY -> reject
    print("ok: build_detector('mock') -> MockDetector")


def test_mock_scripted_frames():
    det = MockDetector(frames=[[bottle(0.9)], [reject(0.8)]])
    assert det.infer(None)[0].class_name == "plastic_bottle"
    assert det.infer(None)[0].class_name == "reject_object"
    assert det.infer(None)[0].class_name == "plastic_bottle"  # cycles
    print("ok: MockDetector cycles scripted frames")


def _run_all():
    test_accept_all_positive()
    test_accept_three_of_five()
    test_reject_object()
    test_empty_scene()
    test_low_confidence_below_gate()
    test_disagreement_two_of_five()
    test_multi_object_collect()
    test_exception_fails_safe()
    test_build_mock_detector()
    test_mock_scripted_frames()
    print("\nALL PHASE 7 DECISION/DETECTOR TESTS PASSED")


if __name__ == "__main__":
    _run_all()
