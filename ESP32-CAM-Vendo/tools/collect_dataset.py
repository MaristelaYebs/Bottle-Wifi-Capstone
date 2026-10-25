#!/usr/bin/env python3
"""
Pull /snapshot from ESP32-CAM into tools/dataset/{empty|bottle|reject}/.

  python tools/label_live.py --ip 10.10.10.252
  python tools/collect_dataset.py --ip 10.10.10.252 --label empty --count 40
  python tools/collect_dataset.py --ip 10.10.10.252 --label bottle --count 40
  python tools/collect_dataset.py --ip 10.10.10.252 --label reject --count 40
"""

from __future__ import annotations

import argparse
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="10.10.10.252")
    ap.add_argument("--label", choices=["empty", "bottle", "reject"], required=True)
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    out = ROOT / "tools" / "dataset" / args.label
    out.mkdir(parents=True, exist_ok=True)
    url = f"http://{args.ip}/snapshot"
    print(f"Saving {args.count} images to {out}")
    print("Aim camera, then press Enter...")
    input()

    for i in range(args.count):
        path = out / f"{args.label}_{int(time.time() * 1000)}_{i:03d}.jpg"
        with urllib.request.urlopen(url, timeout=5) as r:
            path.write_bytes(r.read())
        print(f"  [{i+1}/{args.count}] {path.name}")
        time.sleep(args.delay)

    print("Done. Train with: python tools/train_bottle_model.py --data tools/dataset")


if __name__ == "__main__":
    main()
