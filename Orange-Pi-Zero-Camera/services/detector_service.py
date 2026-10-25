"""Detector Service

YOLO-Nano inference via NCNN at 480x480. Model loaded once at startup,
thread-capped. Two implementations:

  * NcnnDetector  — real inference on the Orange Pi (needs `ncnn` + a model).
  * MockDetector  — returns scripted/empty detections for pipeline testing
                    with no model and no NCNN (used until a model is exported
                    in Phase 12/14).

`build_detector(cfg)` selects one via `detection.backend` ("ncnn" | "mock").

Implemented in Phase 7. Interface: docs/architecture/02-low-level-architecture.md
NOTE: the NCNN output decode assumes an Ultralytics YOLOv8 NCNN export
(`yolo export format=ncnn`, output [1, 4+nc, num_anchors]). It is validated and
tuned against the real export in Phase 14 — no model exists yet.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Protocol

from core.types import Detection

log = logging.getLogger("detector")


class Detector(Protocol):
    def load(self) -> bool: ...
    def warmup(self) -> None: ...
    def infer(self, frame) -> List[Detection]: ...
    def version(self) -> str: ...
    def is_loaded(self) -> bool: ...


# --------------------------------------------------------------------------
# Mock detector
# --------------------------------------------------------------------------
class MockDetector:
    """Returns a fixed/scripted set of detections per infer() call.

    frames=None -> always returns [] (EMPTY -> fail-safe REJECT), the safe default.
    frames=[[Detection,...], ...] -> cycles through the given per-call results.
    """

    def __init__(self, cfg=None, frames: Optional[List[List[Detection]]] = None) -> None:
        self._frames = frames
        self._i = 0

    def load(self) -> bool:
        return True

    def warmup(self) -> None:
        pass

    def is_loaded(self) -> bool:
        return True

    def version(self) -> str:
        return "mock"

    def infer(self, frame) -> List[Detection]:
        if not self._frames:
            return []
        dets = self._frames[self._i % len(self._frames)]
        self._i += 1
        return list(dets)


# --------------------------------------------------------------------------
# Real NCNN detector
# --------------------------------------------------------------------------
class NcnnDetector:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._imgsz = int(cfg.get("detection.imgsz", 480))
        self._num_threads = int(cfg.get("detection.num_threads", 3))
        self._classes: List[str] = list(cfg.get("detection.classes",
                                                 ["plastic_bottle", "reject_object"]))
        self._nms_conf = float(cfg.get("detection.nms_conf", 0.25))
        self._nms_iou = float(cfg.get("detection.nms_iou", 0.45))
        self._input_name = str(cfg.get("detection.model_input", "in0"))
        self._output_name = str(cfg.get("detection.model_output", "out0"))
        self._model_dir = cfg.resolve_path(cfg.get("paths.models_production", "./models/production"))
        self._net = None
        self._version = "unknown"

    # ---------- model discovery ----------
    def _find_model(self):
        base = Path(self._model_dir)
        if not base.exists():
            return None, None
        # models/production/<version>/model.ncnn.param + .bin  (or *.param/*.bin)
        candidates = sorted([p for p in base.glob("**/*.param")])
        if not candidates:
            return None, None
        param = candidates[0]
        bin_ = param.with_suffix(".bin")
        if not bin_.exists():
            alt = list(param.parent.glob("*.bin"))
            bin_ = alt[0] if alt else None
        self._version = param.parent.name
        return (param, bin_) if bin_ else (None, None)

    def load(self) -> bool:
        try:
            import ncnn  # type: ignore
        except Exception as exc:  # noqa: BLE001
            log.error("ncnn not installed: %s", exc)
            return False
        param, bin_ = self._find_model()
        if not param or not bin_:
            log.error("no NCNN model (.param/.bin) under %s", self._model_dir)
            return False
        net = ncnn.Net()
        net.opt.num_threads = self._num_threads
        net.opt.use_vulkan_compute = False  # H618 Vulkan is unreliable; CPU only
        net.opt.lightmode = True
        if net.load_param(str(param)) != 0 or net.load_model(str(bin_)) != 0:
            log.error("failed to load NCNN model %s", param)
            return False
        self._net = net
        log.info("NCNN model loaded: %s (threads=%d, imgsz=%d)",
                 self._version, self._num_threads, self._imgsz)
        return True

    def is_loaded(self) -> bool:
        return self._net is not None

    def version(self) -> str:
        return self._version

    def warmup(self) -> None:
        if self._net is None:
            return
        try:
            import numpy as np  # type: ignore

            dummy = np.zeros((self._imgsz, self._imgsz, 3), dtype=np.uint8)
            self.infer(dummy)
            log.info("detector warmup done")
        except Exception as exc:  # noqa: BLE001
            log.warning("detector warmup skipped: %s", exc)

    # ---------- inference ----------
    def infer(self, frame) -> List[Detection]:
        if self._net is None:
            return []
        try:
            import ncnn  # type: ignore
            import numpy as np  # type: ignore

            img, scale, pad = _letterbox(frame, self._imgsz, np)
            h, w = img.shape[:2]
            mat_in = ncnn.Mat.from_pixels(img.tobytes(), ncnn.Mat.PixelType.PIXEL_BGR2RGB, w, h)
            # normalise 0..1
            mat_in.substract_mean_normalize([0.0, 0.0, 0.0], [1 / 255.0, 1 / 255.0, 1 / 255.0])

            ex = self._net.create_extractor()
            ex.input(self._input_name, mat_in)
            ret, mat_out = ex.extract(self._output_name)
            if ret != 0:
                log.warning("ncnn extract failed (%s)", self._output_name)
                return []
            out = np.array(mat_out)  # shape depends on export
            return _decode_yolov8(out, scale, pad, self._classes,
                                  self._nms_conf, self._nms_iou, np)
        except Exception as exc:  # noqa: BLE001 - inference must never crash the pipeline
            log.exception("inference error -> no detections: %s", exc)
            return []


# --------------------------------------------------------------------------
# helpers (numpy passed in so the module imports without numpy present)
# --------------------------------------------------------------------------
def _letterbox(frame, size: int, np):
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    px, py = (size - nw) // 2, (size - nh) // 2
    canvas[py:py + nh, px:px + nw] = resized
    return canvas, scale, (px, py)


def _decode_yolov8(out, scale, pad, classes, conf_thr, iou_thr, np):
    """Decode an Ultralytics YOLOv8 NCNN output into Detection list.

    Expected raw shape [4+nc, num_anchors] (or its transpose). Returns boxes in
    original-image pixel coords.
    """
    arr = np.asarray(out, dtype="float32")
    if arr.ndim != 2:
        arr = arr.reshape(arr.shape[0], -1)
    nc = len(classes)
    # orient so rows = anchors, cols = 4+nc
    if arr.shape[0] == 4 + nc:
        arr = arr.T
    if arr.shape[1] < 4 + nc:
        return []

    boxes = arr[:, :4]
    scores_all = arr[:, 4:4 + nc]
    class_ids = scores_all.argmax(axis=1)
    confs = scores_all.max(axis=1)

    keep = confs >= conf_thr
    boxes, class_ids, confs = boxes[keep], class_ids[keep], confs[keep]
    if len(boxes) == 0:
        return []

    # xywh (center) -> xyxy, then undo letterbox
    cx, cy, bw, bh = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    x1 = (cx - bw / 2 - pad[0]) / scale
    y1 = (cy - bh / 2 - pad[1]) / scale
    x2 = (cx + bw / 2 - pad[0]) / scale
    y2 = (cy + bh / 2 - pad[1]) / scale
    xyxy = np.stack([x1, y1, x2, y2], axis=1)

    keep_idx = _nms(xyxy, confs, iou_thr, np)
    dets: List[Detection] = []
    for i in keep_idx:
        cid = int(class_ids[i])
        name = classes[cid] if 0 <= cid < len(classes) else str(cid)
        dets.append(Detection(class_id=cid, class_name=name,
                              confidence=float(confs[i]),
                              bbox=(float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i]))))
    return dets


def _nms(boxes, scores, iou_thr, np):
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thr]
    return keep


def build_detector(cfg) -> Detector:
    backend = str(cfg.get("detection.backend", "ncnn")).lower()
    if backend == "mock":
        log.warning("using MOCK detector (no real inference)")
        return MockDetector(cfg)
    return NcnnDetector(cfg)
