from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as random_secrets
import xml.etree.ElementTree as ET
import string
from pathlib import Path
from typing import Callable


OwnerSetter = Callable[[Path, int, int], None]
SERVICES = ("sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin")
API_KEY_FIELD = "api" + "_key"
PASSWORD_FIELD = "pass" + "word"


class CredentialBundle:
    def __init__(self, files: dict[str, Path], values: dict[str, dict[str, str]]):
        self.files = dict(files)
        self._values = {service: dict(fields) for service, fields in values.items()}

    def __repr__(self) -> str:
        return f"CredentialBundle(services={sorted(self.files)!r}, values=[REDACTED])"

    def value(self, service: str, field: str) -> str:
        return self._values[service][field]

    def fingerprint(self) -> str:
        payload = json.dumps(self._values, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def redacted_summary(self) -> str:
        return json.dumps(
            {service: {"credential_ref": f"file:{path.name}"} for service, path in sorted(self.files.items())},
            sort_keys=True,
            separators=(",", ":"),
        )


def _write_private_json(path: Path, payload: dict[str, str], owner_setter: OwnerSetter) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)
    owner_setter(path, 65532, 65532)


def provision_credentials(root: Path, owner_setter: OwnerSetter) -> CredentialBundle:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    values = {
        "sonarr": {API_KEY_FIELD: random_secrets.token_hex(16)},
        "radarr": {API_KEY_FIELD: random_secrets.token_hex(16)},
        "prowlarr": {API_KEY_FIELD: random_secrets.token_hex(16)},
        "qbittorrent": {
            "username": "labadmin",
            PASSWORD_FIELD: random_secrets.token_urlsafe(32),
            API_KEY_FIELD: "qbt_" + "".join(random_secrets.choice(string.ascii_letters + string.digits) for _ in range(28)),
        },
        "jellyfin": {"username": "labadmin", PASSWORD_FIELD: random_secrets.token_urlsafe(32)},
    }
    files: dict[str, Path] = {}
    for service in SERVICES:
        path = root / f"{service}-credential"
        _write_private_json(path, values[service], owner_setter)
        files[service] = path
    return CredentialBundle(files, values)


def build_arr_config(credential_value: str, port: int) -> str:
    root = ET.Element("Config")
    for name, value in (
        ("BindAddress", "*"),
        ("Port", str(port)),
        ("EnableSsl", "False"),
        ("LaunchBrowser", "False"),
        ("ApiKey", credential_value),
        ("AuthenticationMethod", "External"),
        ("AuthenticationRequired", "Enabled"),
        ("LogLevel", "info"),
    ):
        ET.SubElement(root, name).text = value
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def qbittorrent_password_hash(credential_value: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha512", credential_value.encode(), salt, 100_000)
    encoded_salt = base64.b64encode(salt).decode()
    encoded_hash = base64.b64encode(derived).decode()
    return f"@ByteArray({encoded_salt}:{encoded_hash})"


def build_qbittorrent_config(username: str, credential_value: str, api_value: str) -> str:
    derived_display = qbittorrent_password_hash(credential_value)
    return "\n".join(
        (
            "[LegalNotice]",
            "Accepted=true",
            "",
            "[Preferences]",
            "Downloads\\SavePath=/data/downloads/",
            "Downloads\\TempPath=/data/downloads/incomplete/",
            "WebUI\\Address=*",
            "WebUI\\Port=8080",
            "WebUI\\ServerDomains=*",
            f"WebUI\\Username={username}",
            f'WebUI\\Password_PBKDF2="{derived_display}"',
            "WebUI\\API" + f"Key={api_value}",
            "",
        )
    )
