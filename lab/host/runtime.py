from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SERVICES = ("sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin")


class RuntimeSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeLayout:
    root: Path
    config: dict[str, Path]
    secrets: Path
    downloads: Path
    tv: Path
    movies: Path
    jellyfin_cache: Path

    def private_directories(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.root / "config",
            *self.config.values(),
            self.secrets,
            self.root / "data",
            self.downloads,
            self.root / "data" / "media",
            self.tv,
            self.movies,
            self.root / "cache",
            self.jellyfin_cache,
        )


def _reject_symlink_chain(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise RuntimeSafetyError(f"runtime path component is a symlink: {current}")


def prepare_runtime_tree(root: Path, trusted_parent: Path) -> RuntimeLayout:
    root = root.expanduser().absolute()
    trusted_parent = trusted_parent.expanduser().absolute()
    _reject_symlink_chain(trusted_parent)
    trusted_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(trusted_parent, 0o700)
    if root.parent != trusted_parent:
        raise RuntimeSafetyError("runtime root must be a direct child of the trusted parent")
    _reject_symlink_chain(root)
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.resolve().parent != trusted_parent.resolve():
        raise RuntimeSafetyError("runtime root escaped its trusted parent")

    config_root = root / "config"
    config = {service: config_root / service for service in SERVICES}
    layout = RuntimeLayout(
        root=root.resolve(),
        config=config,
        secrets=root / "secrets",
        downloads=root / "data" / "downloads",
        tv=root / "data" / "media" / "tv",
        movies=root / "data" / "media" / "movies",
        jellyfin_cache=root / "cache" / "jellyfin",
    )
    for directory in layout.private_directories():
        _reject_symlink_chain(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise RuntimeSafetyError("runtime directory escaped its root")
        os.chmod(directory, 0o700)
    return layout
