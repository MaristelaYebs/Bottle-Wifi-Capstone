"""Health Service

Periodic health snapshot: camera, serial, model, database, MikroTik, plus RAM,
swap, CPU temperature and disk (spec §27). Subsystem checks are injected as
callables so the service is testable and cheap; MikroTik (a network round trip) is
polled less often and cached.

Implemented in Phase 17. Interface: docs/architecture/02-low-level-architecture.md §10
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

from core.types import HealthReport

log = logging.getLogger("health")

try:
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore


def _cpu_temp_c() -> float:
    # psutil first, then Linux sysfs, else 0 (unknown, e.g. on Windows dev box)
    if psutil is not None and hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            for key in ("cpu_thermal", "coretemp", "cpu-thermal", "soc_thermal"):
                if key in temps and temps[key]:
                    return float(temps[key][0].current)
            for entries in temps.values():
                if entries:
                    return float(entries[0].current)
        except Exception:  # noqa: BLE001
            pass
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return int(raw) / 1000.0
    except Exception:  # noqa: BLE001
        return 0.0


class HealthService:
    def __init__(self, cfg, *, serial_ok: Callable[[], bool], model_ok: Callable[[], bool],
                 db_ok: Callable[[], bool], camera_ok: Callable[[], bool],
                 mikrotik_ok: Callable[[], bool], disk_path: str = ".") -> None:
        self.cfg = cfg
        self._serial_ok = serial_ok
        self._model_ok = model_ok
        self._db_ok = db_ok
        self._camera_ok = camera_ok
        self._mikrotik_ok = mikrotik_ok
        self._disk_path = disk_path
        self.disk_warn_mb = int(cfg.get("health.disk_warn_mb", 500))
        self.ram_warn_pct = float(cfg.get("health.ram_warn_pct", 85))
        self.cpu_temp_warn_c = float(cfg.get("health.cpu_temp_warn_c", 75))
        # MikroTik is a network call — poll every Nth snapshot, cache in between.
        self._mikrotik_every = int(cfg.get("health.mikrotik_every", 6))
        self._tick = 0
        self._mikrotik_cached = False

    @staticmethod
    def _safe(fn: Callable[[], bool]) -> bool:
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    def _disk_free_mb(self) -> float:
        try:
            return shutil.disk_usage(self._disk_path).free / (1024 * 1024)
        except Exception:  # noqa: BLE001
            return 0.0

    def snapshot(self) -> HealthReport:
        hr = HealthReport()
        hr.serial = self._safe(self._serial_ok)
        hr.model = self._safe(self._model_ok)
        hr.database = self._safe(self._db_ok)
        hr.camera = self._safe(self._camera_ok)

        if self._tick % self._mikrotik_every == 0:
            self._mikrotik_cached = self._safe(self._mikrotik_ok)
        self._tick += 1
        hr.mikrotik = self._mikrotik_cached

        if psutil is not None:
            try:
                hr.ram_pct = float(psutil.virtual_memory().percent)
                hr.swap_pct = float(psutil.swap_memory().percent)
            except Exception:  # noqa: BLE001
                pass
        hr.cpu_temp_c = _cpu_temp_c()
        hr.disk_free_mb = self._disk_free_mb()
        return hr

    def warnings(self, hr: HealthReport) -> list[str]:
        w = []
        if not hr.camera:
            w.append("camera down")
        if not hr.serial:
            w.append("serial down")
        if not hr.model:
            w.append("model not loaded")
        if not hr.database:
            w.append("database down")
        if not hr.mikrotik:
            w.append("mikrotik unreachable")
        if hr.disk_free_mb and hr.disk_free_mb < self.disk_warn_mb:
            w.append(f"low disk {hr.disk_free_mb:.0f}MB")
        if hr.ram_pct and hr.ram_pct > self.ram_warn_pct:
            w.append(f"high RAM {hr.ram_pct:.0f}%")
        if hr.cpu_temp_c and hr.cpu_temp_c > self.cpu_temp_warn_c:
            w.append(f"hot CPU {hr.cpu_temp_c:.0f}C")
        return w
