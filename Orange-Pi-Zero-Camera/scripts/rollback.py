"""Rollback Model — run ON THE ORANGE PI.

Restores the previous production model from models/rollback/ and marks it active.
Use when a freshly deployed model misbehaves in the field.

    python scripts/rollback.py --restart

Implemented in Phase 14.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config  # noqa: E402
from repositories.db import Database  # noqa: E402
from repositories.model_repository import ModelRepository  # noqa: E402
from services.model_service import ModelService  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent
SERVICE = "bottle-vendo"


def _restart():
    subprocess.run(["systemctl", "restart", SERVICE], check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Roll back to the previous model")
    ap.add_argument("--restart", action="store_true")
    args = ap.parse_args()

    cfg = load_config(APP_ROOT)
    db = Database(str(cfg.resolve_path(cfg.get("paths.db"))))
    db.connect()
    svc = ModelService(cfg, ModelRepository(db))
    ok, msg = svc.rollback(restart=_restart if args.restart else None)
    print(("OK: " if ok else "FAILED: ") + msg)
    db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
