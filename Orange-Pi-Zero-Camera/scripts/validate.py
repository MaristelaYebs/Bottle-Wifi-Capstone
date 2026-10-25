"""Validate Model — run on the PC/GPU box before deploying.

Evaluates a candidate on the held-out TEST split:
  * Ultralytics metrics: Precision, Recall, mAP50
  * Reward-machine metrics: False Acceptance Rate (FAR) + False Rejection Rate (FRR)
Applies pass/fail gates (FAR is the hard gate) and writes validation.json next to
the candidate so scripts/deploy.py (Phase 14) can gate on it.

    python scripts/validate.py --weights runs/bottle/weights/best.pt \
        --data dataset/dataset.yaml --version bottle-v1

Implemented in Phase 13. Metric math lives in core/metrics.py (unit-tested).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.metrics import compute_reward_metrics, evaluate_thresholds  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp")


def _truth_is_bottle(label_path: Path) -> bool:
    if not label_path.exists():
        return False  # background/negative -> not a bottle
    for line in label_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and line.split()[0] == "0":  # class 0 = plastic_bottle
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a candidate detector")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--data", default=str(APP_ROOT / "dataset" / "dataset.yaml"))
    ap.add_argument("--version", default="bottle-v1")
    ap.add_argument("--imgsz", type=int, default=480)
    ap.add_argument("--conf", type=float, default=0.70, help="accept gate (match config confidence)")
    ap.add_argument("--max-far", type=float, default=0.02)
    ap.add_argument("--min-recall", type=float, default=0.90)
    ap.add_argument("--min-map50", type=float, default=0.80)
    ap.add_argument("--out", default=None, help="validation.json path (default: candidate dir)")
    args = ap.parse_args()

    try:
        import yaml  # type: ignore
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: needs ultralytics+PyYAML on the training PC ({exc})")
        return 1

    model = YOLO(args.weights)

    # 1) standard metrics on the test split
    val = model.val(data=args.data, split="test", imgsz=args.imgsz, verbose=False)
    map50 = float(getattr(val.box, "map50", 0.0))
    precision = float(getattr(val.box, "mp", 0.0))
    recall = float(getattr(val.box, "mr", 0.0))

    # 2) FAR/FRR by replaying the accept decision per test image
    data = yaml.safe_load(open(args.data, encoding="utf-8"))
    root = Path(args.data).parent / data.get("path", ".")
    test_images = (root / data["test"]).resolve()
    labels_dir = Path(str(test_images).replace("images", "labels"))
    pairs = []
    imgs = [p for p in test_images.iterdir() if p.suffix.lower() in IMG_EXT] \
        if test_images.exists() else []
    for img in imgs:
        truth = _truth_is_bottle(labels_dir / (img.stem + ".txt"))
        res = model.predict(str(img), imgsz=args.imgsz, conf=args.conf, verbose=False)
        pred_bottle = False
        if res and len(res[0].boxes):
            pred_bottle = any(int(c) == 0 for c in res[0].boxes.cls.tolist())
        pairs.append((truth, pred_bottle))

    rm = compute_reward_metrics(pairs)
    passed, reasons = evaluate_thresholds(rm, map50, max_far=args.max_far,
                                          min_recall=args.min_recall, min_map50=args.min_map50)

    report = {
        "version": args.version, "weights": str(args.weights),
        "imgsz": args.imgsz, "conf_gate": args.conf,
        "map50": round(map50, 4), "precision": round(precision, 4), "recall": round(recall, 4),
        "far": rm.far, "frr": rm.frr, "test_samples": rm.total,
        "confusion": {"tp": rm.tp, "fp": rm.fp, "fn": rm.fn, "tn": rm.tn},
        "thresholds": {"max_far": args.max_far, "min_recall": args.min_recall,
                       "min_map50": args.min_map50},
        "passed": passed, "reasons": reasons,
    }
    out = Path(args.out) if args.out else (APP_ROOT / "models" / "candidate" / args.version / "validation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nvalidation.json -> {out}")
    print("PASSED" if passed else "FAILED: " + "; ".join(reasons))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
