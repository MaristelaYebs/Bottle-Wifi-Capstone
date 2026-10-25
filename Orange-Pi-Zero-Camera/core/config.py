"""Configuration Loader

Load and validate config/config.yaml and .env. Secrets are never logged.

Implemented in Phase 5. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DotDict(dict):
    """dict with attribute access and dotted-key .get(), for config trees."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node


def _wrap(obj: Any) -> Any:
    if isinstance(obj, dict):
        return DotDict({k: _wrap(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_wrap(v) for v in obj]
    return obj


@dataclass
class MikroTikSecrets:
    host: str = ""
    port: int = 8728
    username: str = ""
    password: str = ""
    use_ssl: bool = False
    hotspot_profile: str = "default"
    username_only: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username)


class Config:
    """Parsed application configuration.

    `data` is the config.yaml tree (dot-accessible); `mikrotik` holds .env
    secrets; `app_root` anchors relative paths so the app works regardless of cwd.
    """

    def __init__(self, data: DotDict, mikrotik: MikroTikSecrets, app_root: Path):
        self.data = data
        self.mikrotik = mikrotik
        self.app_root = app_root

    # convenience passthroughs
    def __getattr__(self, name: str) -> Any:
        return getattr(self.data, name)

    def get(self, dotted: str, default: Any = None) -> Any:
        return self.data.get_path(dotted, default)

    def resolve_path(self, p: str) -> Path:
        """Resolve a config path relative to the application root."""
        path = Path(p)
        return path if path.is_absolute() else (self.app_root / path).resolve()


def _load_env(env_path: Path) -> MikroTikSecrets:
    # Prefer python-dotenv if present; fall back to a tiny parser + os.environ.
    values: dict[str, str] = {}
    if env_path.exists():
        try:
            from dotenv import dotenv_values  # type: ignore

            values.update({k: v for k, v in dotenv_values(env_path).items() if v is not None})
        except Exception:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    # environment overrides file
    for k in list(values.keys()) + [
        "MIKROTIK_HOST", "MIKROTIK_PORT", "MIKROTIK_USERNAME", "MIKROTIK_PASSWORD",
        "MIKROTIK_USE_SSL", "MIKROTIK_HOTSPOT_PROFILE", "MIKROTIK_USERNAME_ONLY",
    ]:
        if k in os.environ:
            values[k] = os.environ[k]

    def _bool(v: str | None) -> bool:
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    return MikroTikSecrets(
        host=values.get("MIKROTIK_HOST", ""),
        port=int(values.get("MIKROTIK_PORT", "8728") or 8728),
        username=values.get("MIKROTIK_USERNAME", ""),
        password=values.get("MIKROTIK_PASSWORD", ""),
        use_ssl=_bool(values.get("MIKROTIK_USE_SSL")),
        hotspot_profile=values.get("MIKROTIK_HOTSPOT_PROFILE", "default"),
        username_only=_bool(values.get("MIKROTIK_USERNAME_ONLY")),
    )


def load_config(app_root: str | Path, config_rel: str = "config/config.yaml",
                env_rel: str = ".env") -> Config:
    """Load config.yaml + .env anchored at `app_root`."""
    import yaml  # imported here so tests that don't need config don't require PyYAML

    root = Path(app_root).resolve()
    config_path = root / config_rel
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    data = _wrap(raw)
    mikrotik = _load_env(root / env_rel)
    return Config(data, mikrotik, root)
