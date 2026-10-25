"""Camera diagnostic — run ON THE ORANGE PI with the USB webcam attached.

Verifies the webcam opens in MJPEG at the configured resolution, times a capture
burst, and saves the last frame to logs/camera_check.jpg for a visual check.

    python scripts/camera_check.py            # uses config/config.yaml
    python scripts/camera_check.py --frames 5 --gap 100

Added in Phase 6. Not needed for normal operation.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config  # noqa: E402
from services.camera_service import CameraService  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Bottle Vendo camera diagnostic")
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--gap", type=int, default=None, help="ms between frames")
    ap.add_argument("--detect", action="store_true",
                    help="also run the production model + decision engine on the burst "
                         "and annotate the saved frame")
    args = ap.parse_args()

    cfg = load_config(APP_ROOT)
    cam = CameraService(cfg)

    print(f"device={cfg.get('camera.device')} target="
          f"{cfg.get('camera.width')}x{cfg.get('camera.height')} "
          f"fourcc={cfg.get('camera.fourcc')}")

    if not cam.open():
        print("FAIL: camera did not open")
        return 1
    print("open OK, negotiated:", cam.info())
    cam.release()

    t0 = time.monotonic()
    frames = cam.capture_burst(args.frames, args.gap)
    dt = time.monotonic() - t0
    print(f"captured {len(frames)} frame(s) in {dt*1000:.0f} ms")
    if not frames:
        print("FAIL: no frames captured")
        return 1

    last = frames[-1]
    if args.detect:
        last = _detect_report(cfg, frames) or last

    try:
        import cv2  # noqa: PLC0415

        logs = cfg.resolve_path(cfg.get("paths.logs", "./logs"))
        logs.mkdir(parents=True, exist_ok=True)
        out = logs / "camera_check.jpg"
        cv2.imwrite(str(out), last)
        h, w = last.shape[:2]
        print(f"last frame {w}x{h}  saved -> {out}")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not save preview: {exc})")

    print("PASS")
    return 0


def _detect_report(cfg, frames):
    """Run the deployed model + decision engine on the burst. Returns the last
    frame annotated, or None if the model could not be loaded."""
    from core.state import AppState  # noqa: PLC0415
    from services.decision_service import DecisionService  # noqa: PLC0415
    from services.detector_service import NcnnDetector  # noqa: PLC0415
    from services.preview_service import PreviewService  # noqa: PLC0415

    det = NcnnDetector(cfg)
    if not det.load():
        print("WARN: no model loaded from models/production — skipping --detect")
        return None
    print(f"model: {det.version()}")

    per_frame = [det.infer(f) for f in frames]
    d = DecisionService(cfg).decide(per_frame)
    print(f"decision -> {d.outcome} / {d.logical}  pred={d.prediction}"
          f"  conf={d.confidence:.2f}  frames={d.positive_frames}/{d.total_frames}")
    for r in d.reasons:
        print(f"  - {r}")

    prev = PreviewService(cfg, None, det, None, AppState())
    info = {"width": frames[-1].shape[1], "height": frames[-1].shape[0],
            "capture_ms": 0, "infer_ms": 0, "model": det.version(), "model_loaded": True}
    try:
        return prev._draw(frames[-1].copy(), per_frame[-1], info)
    except Exception as exc:  # noqa: BLE001
        print(f"(could not annotate: {exc})")
        return None


if __name__ == "__main__":
    raise SystemExit(main())
