"""Decision Service

Aggregate multi-frame detections into ACCEPT/REJECT (BOTTLE / REJECT / EMPTY).
Pure and stateless. Fail-safe: any error or uncertainty -> REJECT.

Implemented in Phase 7. Interface: docs/architecture/02-low-level-architecture.md §7
"""

from __future__ import annotations

import logging
from typing import List, Sequence

from core.types import Decision

log = logging.getLogger("decision")

CLASS_BOTTLE = "plastic_bottle"
CLASS_REJECT = "reject_object"


class DecisionService:
    def __init__(self, cfg) -> None:
        self.confidence = float(cfg.get("detection.confidence", 0.70))
        self.required = int(cfg.get("detection.required_positive_frames", 3))
        self.borderline = float(cfg.get("detection.borderline_margin", 0.15))

    def frame_label(self, dets: Sequence) -> str:
        """Label ONE frame: 'BOTTLE' | 'REJECT' | 'EMPTY'.

        Used by the continuous chute watcher to tally a sliding window; the
        rule is identical to decide()'s (highest-confidence detection in the
        frame, gated by detection.confidence) so a window that labels BOTTLE
        cannot then be decided EMPTY.
        """
        try:
            if not dets:
                return "EMPTY"
            top = max(dets, key=lambda d: d.confidence)
            if top.confidence < self.confidence:
                return "EMPTY"
            if top.class_name == CLASS_BOTTLE:
                return "BOTTLE"
            if top.class_name == CLASS_REJECT:
                return "REJECT"
            return "EMPTY"
        except Exception:  # noqa: BLE001 - a bad frame must not stop the watcher
            log.exception("frame_label failed -> EMPTY")
            return "EMPTY"

    def decide(self, frames_dets: Sequence[Sequence]) -> Decision:
        """frames_dets: list (per frame) of detection lists. Each detection has
        .class_name and .confidence."""
        try:
            total = len(frames_dets)
            tops = []           # highest-confidence detection per frame, or None
            multi = False
            for dets in frames_dets:
                above = [d for d in dets if d.confidence >= self.confidence]
                if len(above) > 1:
                    multi = True
                tops.append(max(dets, key=lambda d: d.confidence) if dets else None)

            positive = [t for t in tops
                        if t and t.class_name == CLASS_BOTTLE and t.confidence >= self.confidence]
            reject_hits = [t for t in tops
                           if t and t.class_name == CLASS_REJECT and t.confidence >= self.confidence]
            max_top_conf = max((t.confidence for t in tops if t), default=0.0)

            reasons: List[str] = []
            if len(positive) >= self.required:
                outcome, logical, prediction = "ACCEPT", "BOTTLE", CLASS_BOTTLE
                conf = sum(t.confidence for t in positive) / len(positive)
                reasons.append(f"{len(positive)}/{total} positive bottle frames")
            else:
                outcome = "REJECT"
                conf = max_top_conf
                if reject_hits:
                    logical, prediction = "REJECT", CLASS_REJECT
                    reasons.append(f"{len(reject_hits)}/{total} reject_object frames")
                else:
                    logical, prediction = "EMPTY", "none"
                    reasons.append("no valid bottle detected")
                if 0 < len(positive) < self.required:
                    reasons.append(f"only {len(positive)}/{total} bottle frames (need {self.required})")

            borderline = (self.confidence - self.borderline) <= max_top_conf < self.confidence
            if borderline:
                reasons.append("borderline confidence")
            if multi:
                reasons.append("multiple objects in a frame")

            collect = (
                outcome == "REJECT"
                or logical == "EMPTY"
                or (0 < len(positive) < self.required)
                or multi
                or borderline
            )

            return Decision(
                outcome=outcome,
                logical=logical,
                prediction=prediction,
                confidence=round(conf, 4),
                positive_frames=len(positive),
                total_frames=total,
                reasons=reasons,
                collect_sample=collect,
            )
        except Exception as exc:  # noqa: BLE001 - never let a decision crash the pipeline
            log.exception("decision failed -> REJECT")
            return Decision.reject(f"decision_error: {exc}")
