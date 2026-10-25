"""Train YOLO — run on a PC/GPU box, NOT on the Orange Pi.

Trains a YOLO-Nano detector for {plastic_bottle, reject_object} from the labelled
dataset. Heavy deps (ultralytics/torch) are intentionally kept off the Pi.

    pip install -r requirements-train.txt
    python scripts/train.py                       # defaults: yolov8n, 480px
    python scripts/train.py --epochs 150 --batch 16 --model yolov8n.pt

Output: runs/<name>/weights/best.pt  -> feed to scripts/export.py (Phase 12).
Implemented in Phase 12. Local training is supported but NOT required to operate.
"""

from __future__ import annotations

import argparse
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = APP_ROOT / "dataset" / "dataset.yaml"


def main() -> int:
    ap = argparse.ArgumentParser(description="Train YOLO-Nano bottle detector")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="dataset YAML")
    ap.add_argument("--model", default="yolov8n.pt", help="base weights (nano)")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--imgsz", type=int, default=480, help="match on-device inference size")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--name", default="bottle", help="run name under project dir")
    ap.add_argument("--project", default=str(APP_ROOT / "runs"))
    ap.add_argument("--device", default=None, help="e.g. 0 for GPU, cpu for CPU")
    ap.add_argument("--patience", type=int, default=30, help="early-stopping patience")
    args = ap.parse_args()

    if not Path(args.data).exists():
        print(f"ERROR: dataset yaml not found: {args.data}")
        print("Create it / populate dataset/{train,val,test}/ first (see dataset/LABELING.md).")
        return 1

    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: ultralytics not installed ({exc}).")
        print("On the training PC:  pip install -r requirements-train.txt")
        return 1

    model = YOLO(args.model)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        name=args.name,
        project=args.project,
        device=args.device,
        patience=args.patience,
    )
    save_dir = getattr(results, "save_dir", Path(args.project) / args.name)
    print("\nTraining complete.")
    print(f"weights: {Path(save_dir) / 'weights' / 'best.pt'}")
    print("Next:  python scripts/validate.py --weights <best.pt>  (Phase 13)")
    print("Then:  python scripts/export.py   --weights <best.pt>  (NCNN export)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
