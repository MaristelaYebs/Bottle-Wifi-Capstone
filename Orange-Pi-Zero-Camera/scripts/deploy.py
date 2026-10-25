"""Deploy Model — run ON THE ORANGE PI.

Validation-gated deploy: refuses a candidate without a passing validation.json,
backs up the current production model to models/rollback/, promotes the candidate,
records it active, optionally restarts the service, and health-checks by loading
the deployed NCNN model — auto-rolling-back if that fails.

    python scripts/deploy.py --version bottle-v1 --restart
    python scripts/deploy.py --version bottle-v1 --no-health   # skip model-load check

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
from services.detector_service import NcnnDetector  # noqa: E402
from services.model_service import ModelService  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent
SERVICE = "bottle-vendo"


def _restart():
    subprocess.run(["systemctl", "restart", SERVICE], check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy a validated model")
    ap.add_argument("--version", required=True)
    ap.add_argument("--restart", action="store_true", help="systemctl restart the service")
    ap.add_argument("--no-health", action="store_true", help="skip the model-load health check")
    args = ap.parse_args()

    cfg = load_config(APP_ROOT)
    db = Database(str(cfg.resolve_path(cfg.get("paths.db"))))
    db.connect()
    svc = ModelService(cfg, ModelRepository(db))

    def health() -> bool:
        det = NcnnDetector(cfg)  # reads models/production after the swap
        return det.load()

    ok, msg = svc.deploy(
        args.version,
        health_check=None if args.no_health else health,
        restart=_restart if args.restart else None,
    )
    print(("OK: " if ok else "FAILED: ") + msg)
    db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
