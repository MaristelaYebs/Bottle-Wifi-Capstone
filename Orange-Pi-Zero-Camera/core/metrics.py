"""Validation metrics — pure, testable.

Reward-machine framing: the positive class is "should ACCEPT" (ground truth is a
plastic_bottle). The system "accepts" when it predicts plastic_bottle above the
confidence gate.

  False Acceptance (FAR): truth is NOT a bottle but the system accepted it
                          -> would wrongly grant Wi-Fi. The hard gate.
  False Rejection (FRR):  truth IS a bottle but the system rejected it
                          -> a user is unfairly denied.

Implemented in Phase 13.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, List, Tuple


@dataclass
class RewardMetrics:
    total: int
    tp: int
    fp: int
    fn: int
    tn: int
    far: float           # false acceptance rate
    frr: float           # false rejection rate
    precision: float
    recall: float
    accuracy: float

    def as_dict(self) -> dict:
        return asdict(self)


def compute_reward_metrics(samples: Iterable[Tuple[bool, bool]]) -> RewardMetrics:
    """samples: iterable of (truth_is_bottle, predicted_is_bottle)."""
    tp = fp = fn = tn = 0
    for truth, pred in samples:
        if truth and pred:
            tp += 1
        elif not truth and pred:
            fp += 1
        elif truth and not pred:
            fn += 1
        else:
            tn += 1
    total = tp + fp + fn + tn
    n_pos = tp + fn        # bottles
    n_neg = fp + tn        # non-bottles (reject/empty)
    far = fp / n_neg if n_neg else 0.0
    frr = fn / n_pos if n_pos else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / n_pos if n_pos else 0.0
    accuracy = (tp + tn) / total if total else 0.0
    return RewardMetrics(total, tp, fp, fn, tn,
                         round(far, 4), round(frr, 4),
                         round(precision, 4), round(recall, 4), round(accuracy, 4))


def evaluate_thresholds(metrics: RewardMetrics, map50: float, *,
                        max_far: float, min_recall: float,
                        min_map50: float) -> Tuple[bool, List[str]]:
    """Gate a candidate. FAR is the critical one for a reward machine."""
    reasons: List[str] = []
    if metrics.far > max_far:
        reasons.append(f"FAR {metrics.far:.3f} > max {max_far:.3f} (CRITICAL)")
    if metrics.recall < min_recall:
        reasons.append(f"recall {metrics.recall:.3f} < min {min_recall:.3f}")
    if map50 < min_map50:
        reasons.append(f"mAP50 {map50:.3f} < min {min_map50:.3f}")
    return (len(reasons) == 0, reasons)
