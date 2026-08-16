from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """A runtime path is unsafe or cannot be interpreted deterministically."""


@dataclass(frozen=True)
class RuntimePaths:
    config_dir: Path
    data_dir: Path


def _absolute_path(raw: str, variable: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ConfigurationError(f"{variable} must be an absolute path")
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigurationError(f"{variable} cannot be resolved") from exc


def _reject_repository_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            raise ConfigurationError(
                "runtime directories must resolve outside repository checkouts"
            )


def resolve_runtime_paths(environ: dict[str, str] | None = None) -> RuntimePaths:
    env = os.environ if environ is None else environ

    def home() -> Path:
        return _absolute_path(env.get("HOME") or str(Path.home()), "HOME")

    config_override = env.get("ARR_ORCHESTRATOR_CONFIG_DIR")
    if config_override:
        config_dir = _absolute_path(config_override, "ARR_ORCHESTRATOR_CONFIG_DIR")
    else:
        config_home_raw = env.get("XDG_CONFIG_HOME")
        config_home = (
            _absolute_path(config_home_raw, "XDG_CONFIG_HOME")
            if config_home_raw
            else home() / ".config"
        )
        config_dir = _absolute_path(
            str(config_home / "arr-orchestrator"),
            "resolved configuration directory",
        )

    data_override = env.get("ARR_ORCHESTRATOR_DATA_DIR")
    if data_override:
        data_dir = _absolute_path(data_override, "ARR_ORCHESTRATOR_DATA_DIR")
    else:
        data_home_raw = env.get("XDG_DATA_HOME")
        data_home = (
            _absolute_path(data_home_raw, "XDG_DATA_HOME")
            if data_home_raw
            else home() / ".local" / "share"
        )
        data_dir = _absolute_path(
            str(data_home / "arr-orchestrator"),
            "resolved data directory",
        )

    _reject_repository_path(config_dir)
    _reject_repository_path(data_dir)
    return RuntimePaths(config_dir=config_dir, data_dir=data_dir)
