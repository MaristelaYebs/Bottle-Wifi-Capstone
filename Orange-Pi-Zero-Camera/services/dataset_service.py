"""Dataset Service

Selective image capture for future training. Saves an image ONLY when the
decision flags it (low-conf / reject / empty-after-trigger / disagreement /
multi-object / borderline), with a JSON sidecar of metadata. Disk-guarded so it
never fills the SD card.

Implemented in Phase 11. Interface: docs/architecture/02-low-level-architecture.md
Spec metadata: project brief §13.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional

log = logging.getLogger("dataset")


def _default_encoder(frame) -> Optional[bytes]:
    try:
        import cv2  # type: ignore

        ok, buf = cv2.imencode(".jpg", frame)
        return buf.tobytes() if ok else None
    except Exception as exc:  # noqa: BLE001
        log.error("jpeg encode failed: %s", exc)
        return None


class DatasetService:
    def __init__(self, cfg, incoming_dir: Optional[str] = None,
                 encoder: Optional[Callable[[Any], Optional[bytes]]] = None) -> None:
        self.cfg = cfg
        if incoming_dir is not None:
            self.dir = Path(incoming_dir)
        else:
            base = cfg.resolve_path(cfg.get("dataset.dir", "./dataset"))
            self.dir = Path(base) / "incoming"
        self.min_free_mb = int(cfg.get("dataset.min_free_mb", 500))
        self._encoder = encoder or _default_encoder
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            log.error("could not create dataset dir %s: %s", self.dir, exc)

    def _free_mb(self) -> float:
        try:
            return shutil.disk_usage(str(self.dir)).free / (1024 * 1024)
        except Exception:  # noqa: BLE001
            return float("inf")  # if we can't tell, don't block capture

    def maybe_save(self, frames: List[Any], decision, model_version: str, txn_id: str,
                   now: Optional[datetime] = None) -> Optional[str]:
        """Save a training sample if the decision asked for it. Returns the image
        path (or sidecar path if no frame was available), else None."""
        if not getattr(decision, "collect_sample", False):
            return None

        free = self._free_mb()
        if free < self.min_free_mb:
            log.warning("skip dataset capture: low disk (%.0f MB < %d MB)", free, self.min_free_mb)
            return None

        now = now or datetime.now()
        img_path: Optional[Path] = None
        frame = frames[-1] if frames else None
        if frame is not None:
            data = self._encoder(frame)
            if data:
                img_path = self.dir / f"{txn_id}.jpg"
                try:
                    img_path.write_bytes(data)
                except Exception as exc:  # noqa: BLE001
                    log.error("could not write %s: %s", img_path, exc)
                    img_path = None

        meta = {
            "transaction_id": txn_id,
            "timestamp": now.isoformat(timespec="seconds"),
            "image_path": str(img_path) if img_path else None,
            "prediction": decision.prediction,
            "confidence": decision.confidence,
            "decision": decision.outcome,
            "logical": decision.logical,
            "positive_frames": decision.positive_frames,
            "total_frames": decision.total_frames,
            "model_version": model_version,
            "collect_reasons": list(decision.reasons),
        }
        sidecar = self.dir / f"{txn_id}.json"
        try:
            sidecar.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.error("could not write sidecar %s: %s", sidecar, exc)
            return str(img_path) if img_path else None

        result = str(img_path) if img_path else str(sidecar)
        log.info("dataset sample saved: %s (%s)", result, "; ".join(decision.reasons))
        return result
