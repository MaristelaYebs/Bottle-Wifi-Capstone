"""Phase 11 unit tests — DatasetService selection, disk guard, sidecar metadata.

Injects a fake JPEG encoder + temp dir, so no OpenCV/webcam is needed.
Run:  python tests/test_dataset_service.py   (or pytest)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.types import Decision  # noqa: E402
from services.dataset_service import DatasetService  # noqa: E402


class FakeCfg:
    def __init__(self, **over):
        self._d = {"dataset.dir": "./dataset", "dataset.min_free_mb": 500}
        self._d.update(over)

    def get(self, key, default=None):
        return self._d.get(key, default)


def _svc(tmp, min_free_mb=0):
    # min_free_mb=0 -> never blocked by disk in normal tests
    return DatasetService(FakeCfg(**{"dataset.min_free_mb": min_free_mb}),
                          incoming_dir=tmp, encoder=lambda f: b"FAKEJPEG")


def _accept():
    return Decision("ACCEPT", "BOTTLE", "plastic_bottle", 0.95, 5, 5, ["5/5"], collect_sample=False)


def _reject():
    return Decision("REJECT", "EMPTY", "none", 0.0, 0, 5, ["no valid bottle detected"],
                    collect_sample=True)


def test_skips_when_not_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        svc = _svc(tmp)
        assert svc.maybe_save([object()], _accept(), "v1", "TXN-1") is None
        assert os.listdir(tmp) == []
    print("ok: not flagged -> nothing saved")


def test_saves_image_and_sidecar_when_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        svc = _svc(tmp)
        path = svc.maybe_save([object(), object()], _reject(), "bottle-v2", "TXN-20260918-000007")
        assert path.endswith("TXN-20260918-000007.jpg")
        assert os.path.exists(os.path.join(tmp, "TXN-20260918-000007.jpg"))
        sidecar = os.path.join(tmp, "TXN-20260918-000007.json")
        assert os.path.exists(sidecar)
        meta = json.load(open(sidecar, encoding="utf-8"))
        assert meta["transaction_id"] == "TXN-20260918-000007"
        assert meta["decision"] == "REJECT"
        assert meta["model_version"] == "bottle-v2"
        assert meta["image_path"].endswith(".jpg")
    print("ok: flagged -> image + sidecar JSON with metadata")


def test_no_frames_writes_sidecar_only():
    with tempfile.TemporaryDirectory() as tmp:
        svc = _svc(tmp)
        path = svc.maybe_save([], _reject(), "v1", "TXN-8")  # empty-after-trigger
        assert path.endswith("TXN-8.json")
        assert not os.path.exists(os.path.join(tmp, "TXN-8.jpg"))
        meta = json.load(open(os.path.join(tmp, "TXN-8.json"), encoding="utf-8"))
        assert meta["image_path"] is None
    print("ok: no frames -> sidecar only (image_path null)")


def test_disk_guard_skips():
    with tempfile.TemporaryDirectory() as tmp:
        # require an absurd amount of free space -> capture skipped
        svc = _svc(tmp, min_free_mb=10 ** 12)
        assert svc.maybe_save([object()], _reject(), "v1", "TXN-9") is None
        assert os.listdir(tmp) == []
    print("ok: low disk -> capture skipped (SD protected)")


def _run_all():
    test_skips_when_not_flagged()
    test_saves_image_and_sidecar_when_flagged()
    test_no_frames_writes_sidecar_only()
    test_disk_guard_skips()
    print("\nALL PHASE 11 DATASET TESTS PASSED")


if __name__ == "__main__":
    _run_all()
