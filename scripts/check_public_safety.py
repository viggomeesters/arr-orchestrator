#!/usr/bin/env python3
"""Fail closed on public-repository privacy and generated-state leaks."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_EXTENSIONS = {
    ".env",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".crt",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".log",
    ".nzb",
    ".torrent",
}
FORBIDDEN_NAME_FRAGMENTS = {
    ".env",
    "config.xml",
    "credentials.json",
    "docker-inspect.json",
    "docker-ps.json",
    "id_ed25519",
    "id_rsa",
    "qbittorrent.conf",
    "secrets.json",
    ".arr-orchestrator-lab",
}
FORBIDDEN_PATH_RE = re.compile(r"(?:^|/)lab/(?:runtime|state|secrets|generated|evidence|config)(?:/|$)", re.IGNORECASE)
PRIVATE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^\s,'\"}]+"
)
PRIVATE_NETWORK_RE = re.compile(
    r"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2}|169\.254(?:\.[0-9]{1,3}){2})(?![0-9])"
)
PRIVATE_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"
)
PRIVATE_HOSTNAME_RE = re.compile(
    r"(?i)(?:https?://|\b(?:host(?:name)?|base_url)\s*[:=]\s*)(?:localhost|[a-z0-9-]+\.(?:local|lan|home))(?=[:/\s]|$)"
)
PRIVATE_HOST_PATH_RE = re.compile(r"(?m)(?:^|[\s=:\"'`])(?:/mnt/[a-zA-Z]/|/home/[A-Za-z0-9._-]+/)")
SECRET_CANARY_RE = re.compile(r"ARR_ORCHESTRATOR_SECRET_CANARY_[A-Za-z0-9_-]+")
TOKEN_RE = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----")
BASE64_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")
RESTRICTED_CLASSIFICATION_RE = re.compile(
    r"(?i)\b(?:classification|disclosure|visibility)\s*[:=]\s*['\"]?(?:restricted|private|confidential|internal-only|blocked)\b"
)
DISCLOSURE_BLOCK_RE = re.compile(
    r"(?i)\b(?:disclosure_status|public_release|publishable|shareable|export_allowed)\s*[:=]\s*['\"]?(?:blocked|denied|false|no)\b"
)
ALLOWED_LITERAL_PATHS = {
    Path(".go/tasks/done/foundation-public-repository.json"),
    Path("scripts/check_public_safety.py"),
    Path("tests/contracts/test_contract_schemas.py"),
    Path("tests/lab/test_lab_contract.py"),
    Path("tests/security/test_public_safety.py"),
}
SKIP_PARTS = {".git", ".hermes", ".pytest_cache", "__pycache__", "build", "dist"}


def repository_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def iter_paths(root: Path, tracked_paths: Iterable[str] | None) -> Iterable[Path]:
    if tracked_paths is None:
        candidates = (path for path in root.rglob("*") if path.is_file())
    else:
        candidates = (root / name for name in tracked_paths)
    for path in candidates:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if path.is_file():
            yield path

def decoded_secret_canary(text: str) -> bool:
    variants = {text}
    decoded_url = urllib.parse.unquote(text)
    variants.add(decoded_url)
    variants.add(urllib.parse.unquote(decoded_url))
    for variant in variants:
        if SECRET_CANARY_RE.search(variant):
            return True
        for match in BASE64_TOKEN_RE.finditer(variant):
            candidate = match.group(0)
            try:
                decoded = base64.b64decode(candidate, validate=True).decode("utf-8", errors="ignore")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            if SECRET_CANARY_RE.search(decoded):
                return True
    return False


def contains_private_ipv6(text: str) -> bool:
    for match in PRIVATE_IPV6_CANDIDATE_RE.finditer(text):
        candidate = match.group(0)
        if candidate == "::":
            continue
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 6 and (address.is_private or address.is_link_local or address.is_loopback):
            return True
    return False


def scan_tree(root: Path, tracked_paths: Iterable[str] | None = None) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    for path in iter_paths(root, tracked_paths):
        relative = path.relative_to(root)
        normalized = relative.as_posix()
        lowered_name = path.name.lower()
        if FORBIDDEN_PATH_RE.search(normalized):
            errors.append(f"generated lab state must not be committed: {normalized}")
        if path.suffix.lower() in FORBIDDEN_EXTENSIONS or lowered_name in FORBIDDEN_NAME_FRAGMENTS:
            errors.append(f"forbidden generated or secret artifact: {normalized}")
        if relative in ALLOWED_LITERAL_PATHS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PRIVATE_ASSIGNMENT_RE.search(text):
            errors.append(f"possible credential assignment: {normalized}")
        if PRIVATE_NETWORK_RE.search(text) or contains_private_ipv6(text):
            errors.append(f"private network identifier detected: {normalized}")
        if PRIVATE_HOSTNAME_RE.search(text):
            errors.append(f"private hostname detected: {normalized}")
        if PRIVATE_HOST_PATH_RE.search(text):
            errors.append(f"private host path detected: {normalized}")
        if TOKEN_RE.search(text):
            errors.append(f"token-shaped credential detected: {normalized}")
        if PRIVATE_KEY_RE.search(text):
            errors.append(f"private key material detected: {normalized}")
        if SECRET_CANARY_RE.search(text) or decoded_secret_canary(text):
            errors.append(f"secret canary detected: {normalized}")
        if RESTRICTED_CLASSIFICATION_RE.search(text):
            errors.append(f"restricted generated artifact detected: {normalized}")
        if DISCLOSURE_BLOCK_RE.search(text):
            errors.append(f"disclosure-blocked generated artifact detected: {normalized}")
    return sorted(set(errors))


def main() -> int:
    errors = scan_tree(ROOT, repository_paths(ROOT))
    if errors:
        print("public safety check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"public safety check: ok ({len(repository_paths(ROOT))} repository paths inspected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
