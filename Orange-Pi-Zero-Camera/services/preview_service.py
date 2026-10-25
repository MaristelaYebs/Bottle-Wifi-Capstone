"""Preview Service — see what the camera (and the model) actually sees.

Commissioning aid for the headless Orange Pi: aim the webcam, confirm framing,
lighting and focus, and check what the decision engine WOULD decide — without
tripping the servo, writing a transaction or crediting a session.

Three surfaces, all served by the portal API (Phase 16):
    GET /preview            browser page (phone on the hotspot works)
    GET /snapshot?annotate=1  one annotated JPEG frame
    GET /detect             JSON dry-run of the real burst + decision

Camera contention: a preview claims the SAME single-flight lock the chute
watcher uses, so the two can never open /dev/video0 at once. While a portal
session is open the watcher holds that lock for its whole scan, so previews
report "busy" — commission with no session open (or set
`detection.always_on: false`, the default, and close the insert-bottle modal).

Added in Phase 17 (commissioning). Nothing here actuates hardware.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, Tuple

log = logging.getLogger("preview")

try:  # keep importable without OpenCV (unit tests, dev boxes)
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None  # type: ignore

# BGR
_GREEN = (80, 220, 80)      # bottle at/above the accept gate
_AMBER = (60, 190, 250)     # bottle below the gate
_RED = (70, 70, 235)        # reject_object
_GREY = (170, 170, 170)
_BLACK = (0, 0, 0)


class PreviewService:
    def __init__(self, cfg, camera, detector, decision, state) -> None:
        self.cfg = cfg
        self.camera = camera
        self.detector = detector
        self.decision = decision
        self.state = state
        self.gate = float(cfg.get("detection.confidence", 0.70))
        self.required = int(cfg.get("detection.required_positive_frames", 3))
        self.total_frames = int(cfg.get("detection.total_frames", 5))
        self.frame_delay_ms = int(cfg.get("detection.frame_delay_ms", 100))
        self.imgsz = int(cfg.get("detection.imgsz", 480))
        self.jpeg_quality = int(cfg.get("dataset.jpeg_quality", 85))

    # ---------------- capture helpers ----------------
    def _grab_one(self):
        """One frame, camera released immediately (RAM discipline)."""
        frames = self.camera.capture_burst(1, 0)
        return frames[0] if frames else None

    # ---------------- annotated single frame ----------------
    def annotated_jpeg(self) -> Tuple[Optional[bytes], dict]:
        """Capture one frame, run the detector, draw the result. No side effects."""
        if cv2 is None:
            return None, {"error": "OpenCV not installed"}
        if not self.state.try_begin_detection():
            return None, {"error": "busy", "detail": "a real detection is in progress"}
        try:
            t0 = time.perf_counter()
            frame = self._grab_one()
            t_cap = (time.perf_counter() - t0) * 1000
            if frame is None:
                return None, {"error": "camera", "detail": "no frame captured"}

            dets: List[Any] = []
            t_inf = 0.0
            if self.detector.is_loaded():
                t1 = time.perf_counter()
                dets = self.detector.infer(frame)
                t_inf = (time.perf_counter() - t1) * 1000

            info = {
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "capture_ms": round(t_cap),
                "infer_ms": round(t_inf),
                "model": self.detector.version() if self.detector.is_loaded() else None,
                "model_loaded": bool(self.detector.is_loaded()),
                "gate": self.gate,
                "detections": [
                    {"class": d.class_name, "confidence": round(d.confidence, 3),
                     "bbox": [round(v) for v in d.bbox]} for d in dets
                ],
            }
            canvas = self._draw(frame.copy(), dets, info)
            ok, buf = cv2.imencode(".jpg", canvas,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
            return (buf.tobytes() if ok else None), info
        except Exception as exc:  # noqa: BLE001 - a preview must never take the app down
            log.exception("preview failed")
            return None, {"error": "preview_failed", "detail": str(exc)}
        finally:
            self.state.end_detection()

    # ---------------- full dry-run ----------------
    def dry_run(self) -> dict:
        """Run the REAL burst + decision engine and report what it would do.

        Identical to the production path except nothing is actuated, recorded or
        credited — this is what a bottle dropped right now would get.
        """
        if not self.state.try_begin_detection():
            return {"ok": False, "error": "busy",
                    "detail": "a real detection is in progress"}
        try:
            if not self.detector.is_loaded():
                return {"ok": False, "error": "model_not_loaded",
                        "detail": "no NCNN model under models/production — "
                                  "every bottle would REJECT"}
            t0 = time.perf_counter()
            frames = self.camera.capture_burst(self.total_frames, self.frame_delay_ms)
            t_cap = (time.perf_counter() - t0) * 1000
            if not frames:
                return {"ok": False, "error": "camera", "detail": "no frames captured"}

            t1 = time.perf_counter()
            per_frame = [self.detector.infer(f) for f in frames]
            t_inf = (time.perf_counter() - t1) * 1000
            d = self.decision.decide(per_frame)

            return {
                "ok": True,
                "would_actuate": d.outcome,
                "decision": {
                    "outcome": d.outcome, "logical": d.logical,
                    "prediction": d.prediction, "confidence": round(d.confidence, 3),
                    "positive_frames": d.positive_frames, "total_frames": d.total_frames,
                    "reasons": d.reasons, "would_collect_sample": d.collect_sample,
                },
                "gate": {"confidence": self.gate, "required_positive_frames": self.required},
                "timing_ms": {"capture": round(t_cap), "infer_total": round(t_inf),
                              "infer_per_frame": round(t_inf / max(1, len(frames)))},
                "frames": len(frames),
                "model": self.detector.version(),
                "per_frame": [
                    [{"class": x.class_name, "confidence": round(x.confidence, 3)} for x in fr]
                    for fr in per_frame
                ],
            }
        except Exception as exc:  # noqa: BLE001
            log.exception("dry run failed")
            return {"ok": False, "error": "dry_run_failed", "detail": str(exc)}
        finally:
            self.state.end_detection()

    # ---------------- drawing ----------------
    def _draw(self, frame, dets, info: dict):
        h, w = frame.shape[:2]
        scale = max(0.5, min(w, h) / 900.0)     # keep text readable at any resolution
        font = cv2.FONT_HERSHEY_SIMPLEX

        # the region the model actually sees after letterboxing (aiming aid)
        s = min(self.imgsz / w, self.imgsz / h)
        nw, nh = int(round(w * s)), int(round(h * s))
        if nw < self.imgsz or nh < self.imgsz:
            # content is padded, not cropped -> whole frame is seen; draw a hint box
            cv2.rectangle(frame, (1, 1), (w - 2, h - 2), _GREY, 1)

        for d in dets:
            x1, y1, x2, y2 = [int(v) for v in d.bbox]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            if d.class_name == "reject_object":
                colour = _RED
            elif d.confidence >= self.gate:
                colour = _GREEN
            else:
                colour = _AMBER
            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, max(2, int(3 * scale)))

            label = f"{d.class_name} {d.confidence:.2f}"
            if d.class_name == "plastic_bottle" and d.confidence < self.gate:
                label += f" (below {self.gate:g})"
            (tw, th), _ = cv2.getTextSize(label, font, 0.6 * scale, max(1, int(2 * scale)))
            ty = y1 - 6 if y1 - 6 - th > 0 else y1 + th + 8
            cv2.rectangle(frame, (x1, ty - th - 6), (x1 + tw + 10, ty + 6), colour, -1)
            cv2.putText(frame, label, (x1 + 5, ty), font, 0.6 * scale, _BLACK,
                        max(1, int(2 * scale)), cv2.LINE_AA)

        # ---- HUD ----
        best = max((d.confidence for d in dets
                    if d.class_name == "plastic_bottle"), default=0.0)
        if not info.get("model_loaded"):
            verdict, vcol = "NO MODEL LOADED", _RED
        elif best >= self.gate:
            verdict, vcol = f"BOTTLE {best:.2f} >= gate {self.gate:g}", _GREEN
        elif any(d.class_name == "reject_object" and d.confidence >= self.gate for d in dets):
            verdict, vcol = "REJECT OBJECT", _RED
        elif best > 0:
            verdict, vcol = f"BOTTLE {best:.2f} BELOW gate {self.gate:g}", _AMBER
        else:
            verdict, vcol = "EMPTY", _GREY

        lines = [
            (verdict, vcol),
            (f"{info['width']}x{info['height']}  capture {info['capture_ms']}ms  "
             f"infer {info['infer_ms']}ms", _GREY),
            (f"model {info.get('model') or '-'}  |  single frame, not the "
             f"{self.total_frames}-frame burst", _GREY),
        ]
        pad = int(10 * scale)
        lh = int(26 * scale)
        box_h = lh * len(lines) + pad
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, box_h), _BLACK, -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
        for i, (text, colour) in enumerate(lines):
            size = 0.78 * scale if i == 0 else 0.55 * scale
            cv2.putText(frame, text, (pad, int(lh * (i + 0.8))), font, size, colour,
                        max(1, int(2 * scale)), cv2.LINE_AA)
        return frame
