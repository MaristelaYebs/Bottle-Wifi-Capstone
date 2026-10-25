"""Export to NCNN — run on the PC/GPU box (produces the files the Pi runs).

Exports a trained YOLO .pt to NCNN (.param + .bin) at the on-device inference
size, and optionally stages it under models/candidate/<version>/ ready to copy
to the Orange Pi and deploy (Phase 14).

    python scripts/export.py --weights runs/bottle/weights/best.pt \
        --version bottle-v1 --to-candidate

Implemented in Phase 12.
NOTE: verify the exported blob names (Ultralytics YOLOv8 NCNN uses in0/out0) and
class order match config/config.yaml detection.model_input/output/classes.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent


def _find_ncnn_files(export_dir: Path):
    params = list(Path(export_dir).glob("**/*.param"))
    if not params:
        return None, None
    param = params[0]
    bins = list(param.parent.glob("*.bin"))
    return param, (bins[0] if bins else None)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export YOLO -> NCNN for the Orange Pi")
    ap.add_argument("--weights", required=True, help="trained .pt (e.g. best.pt)")
    ap.add_argument("--imgsz", type=int, default=480, help="MUST match config detection.imgsz")
    ap.add_argument("--version", default="bottle-v1", help="model version name")
    ap.add_argument("--to-candidate", action="store_true",
                    help="copy the export into models/candidate/<version>/")
    args = ap.parse_args()

    if not Path(args.weights).exists():
        print(f"ERROR: weights not found: {args.weights}")
        return 1
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: ultralytics not installed ({exc}). pip install -r requirements-train.txt")
        return 1

    model = YOLO(args.weights)
    out = model.export(format="ncnn", imgsz=args.imgsz)
    export_dir = Path(out) if out else Path(args.weights).parent
    param, bin_ = _find_ncnn_files(export_dir)
    if not param or not bin_:
        print(f"ERROR: could not locate .param/.bin under {export_dir}")
        return 1
    print(f"NCNN export: {param}  +  {bin_}")

    if args.to_candidate:
        dest = APP_ROOT / "models" / "candidate" / args.version
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(param, dest / "model.ncnn.param")
        shutil.copy2(bin_, dest / "model.ncnn.bin")
        print(f"staged candidate -> {dest}")
        print("Copy models/candidate/<version>/ to the Pi, then: python scripts/deploy.py "
              f"--version {args.version}  (Phase 14, after validate.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
