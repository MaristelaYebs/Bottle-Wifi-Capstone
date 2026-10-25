"""Model Service

On-device model registry / deploy / rollback. No training on device.
Deploy is GATED: a candidate is refused unless it carries a passing
validation.json (Phase 13), and a failed post-deploy health check auto-rolls-back.

Implemented in Phase 14. Interface: docs/architecture/02-low-level-architecture.md §16
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("model")


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))


class ModelService:
    def __init__(self, cfg, model_repo, *, production_dir=None, candidate_dir=None,
                 rollback_dir=None, archive_dir=None) -> None:
        self.cfg = cfg
        self.repo = model_repo
        prod = Path(production_dir) if production_dir else \
            Path(cfg.resolve_path(cfg.get("paths.models_production", "./models/production")))
        cand = Path(candidate_dir) if candidate_dir else \
            Path(cfg.resolve_path(cfg.get("paths.models_candidate", "./models/candidate")))
        root = prod.parent
        self.production_dir = prod
        self.candidate_dir = cand
        self.rollback_dir = Path(rollback_dir) if rollback_dir else root / "rollback"
        self.archive_dir = Path(archive_dir) if archive_dir else root / "archive"
        for d in (self.production_dir, self.candidate_dir, self.rollback_dir, self.archive_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.max_far = float(cfg.get("validation.max_false_acceptance_rate", 0.02))
        self.min_recall = float(cfg.get("validation.min_recall", 0.90))
        self.min_map50 = float(cfg.get("validation.min_map50", 0.80))

    # ---------- queries ----------
    @staticmethod
    def _subdirs(path: Path) -> List[Path]:
        return sorted([p for p in path.iterdir() if p.is_dir()]) if path.exists() else []

    def list_versions(self) -> list:
        return self.repo.all()

    def active_version(self) -> Optional[str]:
        row = self.repo.get_active()
        return row["Version"] if row else None

    def _read_validation(self, version_dir: Path) -> Optional[dict]:
        f = version_dir / "validation.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.error("bad validation.json: %s", exc)
            return None

    def _gate(self, report: Optional[dict]) -> Tuple[bool, str]:
        if not report:
            return False, "no validation.json — refusing to deploy an unvalidated model"
        if not report.get("passed"):
            return False, f"validation did not pass: {report.get('reasons')}"
        far = float(report.get("far", 1.0))
        recall = float(report.get("recall", 0.0))
        map50 = float(report.get("map50", 0.0))
        if far > self.max_far:
            return False, f"FAR {far} > max {self.max_far} (CRITICAL)"
        if recall < self.min_recall:
            return False, f"recall {recall} < min {self.min_recall}"
        if map50 < self.min_map50:
            return False, f"mAP50 {map50} < min {self.min_map50}"
        return True, "ok"

    # ---------- deploy / rollback ----------
    def deploy(self, version: str, *, health_check: Optional[Callable[[], bool]] = None,
               restart: Optional[Callable[[], None]] = None) -> Tuple[bool, str]:
        cand = self.candidate_dir / version
        if not cand.is_dir():
            return False, f"candidate not found: {cand}"

        ok, why = self._gate(self._read_validation(cand))
        if not ok:
            return False, why

        report = self._read_validation(cand) or {}

        # 1) back up current production -> rollback (and shuffle old rollback -> archive)
        current = self._subdirs(self.production_dir)
        if current:
            for old in self._subdirs(self.rollback_dir):
                _move(old, self.archive_dir / old.name)
            for cur in current:
                _move(cur, self.rollback_dir / cur.name)

        # 2) promote candidate -> production
        _move(cand, self.production_dir / version)

        # 3) registry
        self.repo.upsert(version, "production", metrics={
            "precision": report.get("precision"), "recall": report.get("recall"),
            "map50": report.get("map50"), "far": report.get("far"), "frr": report.get("frr")})
        self.repo.set_active(version)
        log.info("deployed %s to production", version)

        # 4) restart the service so it loads the new model
        if restart:
            try:
                restart()
            except Exception as exc:  # noqa: BLE001
                log.error("restart failed: %s", exc)

        # 5) health check -> auto rollback on failure
        if health_check is not None:
            try:
                healthy = bool(health_check())
            except Exception as exc:  # noqa: BLE001
                log.error("health check raised: %s", exc)
                healthy = False
            if not healthy:
                log.error("post-deploy health check FAILED — rolling back")
                rb_ok, rb_msg = self.rollback(restart=restart)
                return False, f"health check failed; rollback: {rb_msg}"

        return True, f"deployed {version}"

    def rollback(self, *, health_check: Optional[Callable[[], bool]] = None,
                 restart: Optional[Callable[[], None]] = None) -> Tuple[bool, str]:
        prev = self._subdirs(self.rollback_dir)
        if not prev:
            return False, "nothing to roll back to"
        prev_dir = prev[0]

        # archive whatever is currently in production (the bad model)
        for cur in self._subdirs(self.production_dir):
            _move(cur, self.archive_dir / cur.name)
        # restore previous
        restored = self.production_dir / prev_dir.name
        _move(prev_dir, restored)
        self.repo.set_active(prev_dir.name)
        log.info("rolled back to %s", prev_dir.name)

        if restart:
            try:
                restart()
            except Exception as exc:  # noqa: BLE001
                log.error("restart failed: %s", exc)
        if health_check is not None and not health_check():
            return False, f"rolled back to {prev_dir.name} but health still failing"
        return True, f"rolled back to {prev_dir.name}"
