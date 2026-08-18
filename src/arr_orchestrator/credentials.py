from __future__ import annotations

import os
import stat
import unicodedata
from pathlib import Path


class CredentialError(ValueError):
    """A credential reference cannot be resolved inside the trusted boundary."""


class SecretValue:
    __slots__ = ("__value",)

    def __init__(self, value: str):
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "SecretValue('[REDACTED]')"

    __str__ = __repr__


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise CredentialError("credential path is unavailable") from error
        if stat.S_ISLNK(mode):
            raise CredentialError("credential path contains a symlink")


def _open_directory(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise CredentialError("credential root is not canonical")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path.anchor, flags)
    except OSError as error:
        raise CredentialError("credential root cannot be opened safely") from error
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as error:
                raise CredentialError("credential root contains an unsafe component") from error
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class FileCredentialResolver:
    def __init__(self, trusted_root: Path, *, expected_uid: int | None = None, max_bytes: int = 4096):
        self.root = trusted_root.absolute()
        self.expected_uid = os.getuid() if expected_uid is None else expected_uid
        self.max_bytes = max_bytes
        _reject_symlink_components(self.root)
        root_descriptor = _open_directory(self.root)
        try:
            root_stat = os.fstat(root_descriptor)
        finally:
            os.close(root_descriptor)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise CredentialError("credential root is not a directory")
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)

    def resolve(self, secret_ref: str) -> SecretValue:
        if not isinstance(secret_ref, str) or not secret_ref.startswith("file:"):
            raise CredentialError("only file credential references are supported")
        relative = secret_ref.removeprefix("file:")
        if not relative or Path(relative).is_absolute() or len(Path(relative).parts) != 1 or relative in {".", ".."}:
            raise CredentialError("credential reference is not contained")
        _reject_symlink_components(self.root / relative)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        root_descriptor = _open_directory(self.root)
        try:
            current_root = os.fstat(root_descriptor)
            if (current_root.st_dev, current_root.st_ino) != self._root_identity:
                raise CredentialError("credential root identity changed")
            descriptor = os.open(relative, flags, dir_fd=root_descriptor)
        except OSError as error:
            raise CredentialError("credential file cannot be opened safely") from error
        finally:
            os.close(root_descriptor)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise CredentialError("credential file is not regular")
            if metadata.st_uid != self.expected_uid:
                raise CredentialError("credential file owner is invalid")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise CredentialError("credential file mode is invalid")
            if metadata.st_nlink != 1:
                raise CredentialError("credential file has ambiguous hard links")
            raw = os.read(descriptor, self.max_bytes + 1)
        finally:
            os.close(descriptor)
        if len(raw) > self.max_bytes:
            raise CredentialError("credential file is too large")
        try:
            encoded = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CredentialError("credential file is not UTF-8") from error
        if encoded.endswith("\n"):
            encoded = encoded[:-1]
        if not encoded or encoded != encoded.strip() or "\n" in encoded:
            raise CredentialError("credential value is empty or unsafe for headers")
        if any(
            ord(char) == 0x7F or unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"}
            for char in encoded
        ):
            raise CredentialError("credential contains forbidden control characters")
        return SecretValue(encoded)
