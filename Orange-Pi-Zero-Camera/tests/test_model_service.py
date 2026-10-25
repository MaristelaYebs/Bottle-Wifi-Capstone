"""Phase 14 unit tests — gated deploy + rollback (temp dirs, in-memory DB).

No NCNN needed: model files are stubs and the health check is injected.
Run:  python tests/test_model_service.py   (or pytest)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.db import Database  # noqa: E402
from repositories.model_repository import ModelRepository  # noqa: E402
from services.model_service import ModelService  # noqa: E402


class FakeCfg:
    def get(self, key, default=None):
        return {
            "validation.max_false_acceptance_rate": 0.02,
            "validation.min_recall": 0.90,
            "validation.min_map50": 0.80,
        }.get(key, default)


def _write_model(dirpath: Path, name: str):
    d = dirpath / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.ncnn.param").write_text("x", encoding="utf-8")
    (d / "model.ncnn.bin").write_text("y", encoding="utf-8")
    return d


def _write_validation(version_dir: Path, *, passed=True, far=0.01, recall=0.95, map50=0.88):
    (version_dir / "validation.json").write_text(json.dumps({
        "passed": passed, "far": far, "recall": recall, "map50": map50,
        "precision": 0.94, "frr": 0.03, "reasons": [] if passed else ["forced fail"],
    }), encoding="utf-8")


def _setup(tmp):
    tmp = Path(tmp)
    prod, cand = tmp / "production", tmp / "candidate"
    rb, arch = tmp / "rollback", tmp / "archive"
    db = Database(":memory:")
    db.connect()
    repo = ModelRepository(db)
    svc = ModelService(FakeCfg(), repo, production_dir=prod, candidate_dir=cand,
                       rollback_dir=rb, archive_dir=arch)
    return svc, repo, prod, cand, rb, arch


def test_deploy_success_backs_up_and_activates():
    with tempfile.TemporaryDirectory() as tmp:
        svc, repo, prod, cand, rb, arch = _setup(tmp)
        _write_model(prod, "bottle-v0")            # current production
        repo.upsert("bottle-v0", "production"); repo.set_active("bottle-v0")
        c = _write_model(cand, "bottle-v1"); _write_validation(c)

        ok, msg = svc.deploy("bottle-v1", health_check=lambda: True)
        assert ok, msg
        assert (prod / "bottle-v1").is_dir()       # promoted
        assert (rb / "bottle-v0").is_dir()         # old backed up
        assert not (cand / "bottle-v1").exists()   # moved out of candidate
        assert repo.get_active()["Version"] == "bottle-v1"
    print("ok: deploy promotes, backs up, activates")


def test_deploy_refuses_without_validation():
    with tempfile.TemporaryDirectory() as tmp:
        svc, repo, prod, cand, rb, arch = _setup(tmp)
        _write_model(cand, "bottle-v1")            # no validation.json
        ok, msg = svc.deploy("bottle-v1")
        assert not ok and "unvalidated" in msg
        assert not (prod / "bottle-v1").exists()
    print("ok: refuses unvalidated candidate")


def test_deploy_refuses_failed_validation():
    with tempfile.TemporaryDirectory() as tmp:
        svc, repo, prod, cand, rb, arch = _setup(tmp)
        c = _write_model(cand, "bottle-v1"); _write_validation(c, passed=False)
        ok, msg = svc.deploy("bottle-v1")
        assert not ok and "did not pass" in msg
    print("ok: refuses failed validation")


def test_deploy_refuses_high_far():
    with tempfile.TemporaryDirectory() as tmp:
        svc, repo, prod, cand, rb, arch = _setup(tmp)
        c = _write_model(cand, "bottle-v1"); _write_validation(c, passed=True, far=0.10)
        ok, msg = svc.deploy("bottle-v1")
        assert not ok and "FAR" in msg
    print("ok: refuses high FAR even if 'passed' flag set")


def test_deploy_health_fail_autorollback():
    with tempfile.TemporaryDirectory() as tmp:
        svc, repo, prod, cand, rb, arch = _setup(tmp)
        _write_model(prod, "bottle-v0")
        repo.upsert("bottle-v0", "production"); repo.set_active("bottle-v0")
        c = _write_model(cand, "bottle-v1"); _write_validation(c)

        ok, msg = svc.deploy("bottle-v1", health_check=lambda: False)
        assert not ok and "rollback" in msg
        # production restored to v0, bad v1 archived, v0 active again
        assert (prod / "bottle-v0").is_dir()
        assert (arch / "bottle-v1").is_dir()
        assert repo.get_active()["Version"] == "bottle-v0"
    print("ok: failed health check -> auto rollback")


def test_manual_rollback():
    with tempfile.TemporaryDirectory() as tmp:
        svc, repo, prod, cand, rb, arch = _setup(tmp)
        _write_model(prod, "bottle-v0")
        repo.upsert("bottle-v0", "production"); repo.set_active("bottle-v0")
        c = _write_model(cand, "bottle-v1"); _write_validation(c)
        assert svc.deploy("bottle-v1", health_check=lambda: True)[0]

        ok, msg = svc.rollback()
        assert ok, msg
        assert (prod / "bottle-v0").is_dir()
        assert repo.get_active()["Version"] == "bottle-v0"
    print("ok: manual rollback restores previous")


def _run_all():
    test_deploy_success_backs_up_and_activates()
    test_deploy_refuses_without_validation()
    test_deploy_refuses_failed_validation()
    test_deploy_refuses_high_far()
    test_deploy_health_fail_autorollback()
    test_manual_rollback()
    print("\nALL PHASE 14 DEPLOY/ROLLBACK TESTS PASSED")


if __name__ == "__main__":
    _run_all()
