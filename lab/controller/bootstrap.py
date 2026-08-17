from __future__ import annotations

import hashlib
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class BootstrapError(RuntimeError):
    pass


def validation_codes(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    items = payload if isinstance(payload, list) else [payload]
    codes = sorted(
        f"{item.get('propertyName')}:{item.get('errorCode')}"
        for item in items
        if isinstance(item, dict) and item.get("propertyName") and item.get("errorCode")
    )
    return ",".join(codes)


def normalized_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def redacted_result(states: dict[str, str], baselines: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "arr-orchestrator.lab-bootstrap-result.v1",
        "ok": all(state == "baseline_verified" for state in states.values()),
        "states": dict(sorted(states.items())),
        "baseline_digests": {
            service: normalized_digest(value) for service, value in sorted(baselines.items())
        },
    }


class HttpClient:
    def __init__(self, service: str, base_url: str, headers: dict[str, str] | None = None):
        self.service = service
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Any | None = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        timeout: float = 30.0,
    ) -> Any:
        request_headers = dict(self.headers)
        request_headers.update(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            request_headers["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode()
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(self.base_url + path, data=body, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            reason = type(getattr(error, "reason", error)).__name__
            raise BootstrapError(f"{self.service} request failed at {path} ({reason})") from error
        if status not in expected:
            codes = validation_codes(raw)
            suffix = f" ({codes})" if codes else ""
            raise BootstrapError(f"{self.service} returned status {status} at {path}{suffix}")
        if not raw:
            return None
        content_type = ""
        try:
            content_type = response.headers.get("Content-Type", "")  # type: ignore[possibly-undefined]
        except UnboundLocalError:
            pass
        if "json" in content_type or raw[:1] in (b"{", b"["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as error:
                raise BootstrapError(f"{self.service} returned malformed JSON at {path}") from error
        return raw.decode(errors="replace")

    def wait_json(self, path: str, attempts: int = 35) -> Any:
        for _ in range(attempts):
            try:
                return self.request("GET", path)
            except BootstrapError:
                time.sleep(2)
        raise BootstrapError(f"{self.service} did not become ready at {path}")


def read_credential(service: str) -> dict[str, str]:
    path = Path(f"/run/secrets/{service}-credential")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BootstrapError(f"{service} credential file is unavailable") from error
    if not isinstance(payload, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in payload.items()):
        raise BootstrapError(f"{service} credential file is invalid")
    return payload


def ensure_root_folder(client: HttpClient, path: str) -> dict[str, Any]:
    folders = client.request("GET", "/api/v3/rootfolder")
    if not any(item.get("path") == path for item in folders):
        client.request("POST", "/api/v3/rootfolder", payload={"path": path}, expected=(201,))
        folders = client.request("GET", "/api/v3/rootfolder")
    match = [item for item in folders if item.get("path") == path]
    if len(match) != 1 or match[0].get("accessible") is not True:
        raise BootstrapError(f"{client.service} root folder readback failed")
    return {"root_paths": [path]}


def set_schema_field(schema: dict[str, Any], name: str, value: Any) -> None:
    matches = [field for field in schema.get("fields", []) if field.get("name") == name]
    if len(matches) != 1:
        raise BootstrapError(f"prowlarr schema field is unavailable: {name}")
    matches[0]["value"] = value


def schema_by_implementation(
    items: list[dict[str, Any]], implementation: str, *, name: str | None = None
) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in items
        if item.get("implementation") == implementation and (name is None or item.get("name") == name)
    ]
    if len(matches) != 1:
        raise BootstrapError(f"prowlarr implementation schema is unavailable: {implementation}")
    matches[0]["fields"] = [dict(field) for field in matches[0].get("fields", [])]
    return matches[0]


def ensure_prowlarr_indexer(client: HttpClient, mock_value: str) -> None:
    current = client.request("GET", "/api/v1/indexer")
    if not any(item.get("name") == "Synthetic Mock Indexer" for item in current):
        profiles = client.request("GET", "/api/v1/appprofile")
        profile_ids = sorted(
            item.get("id") for item in profiles if isinstance(item.get("id"), int) and item.get("id") > 0
        )
        if not profile_ids:
            raise BootstrapError("prowlarr default app profile is unavailable")
        schema = schema_by_implementation(
            client.request("GET", "/api/v1/indexer/schema"),
            "Newznab",
            name="Generic Newznab",
        )
        schema.update(
            {
                "name": "Synthetic Mock Indexer",
                "enable": True,
                "enableRss": True,
                "enableAutomaticSearch": True,
                "enableInteractiveSearch": True,
                "priority": 25,
                "appProfileId": profile_ids[0],
                "tags": [],
            }
        )
        for field_name, field_value in (
            ("baseUrl", "http://mock-indexer:8080"),
            ("apiPath", "/api"),
            ("apiKey", mock_value),
        ):
            set_schema_field(schema, field_name, field_value)
        client.request("POST", "/api/v1/indexer", payload=schema, expected=(201,))


def ensure_prowlarr_application(
    client: HttpClient,
    implementation: str,
    destination_url: str,
    destination_value: str,
) -> None:
    current = client.request("GET", "/api/v1/applications")
    if any(item.get("implementation") == implementation for item in current):
        return
    schema = schema_by_implementation(client.request("GET", "/api/v1/applications/schema"), implementation)
    schema.update({"name": implementation, "syncLevel": "addOnly", "tags": []})
    for field_name, field_value in (
        ("prowlarrUrl", "http://prowlarr:9696"),
        ("baseUrl", destination_url),
        ("apiKey", destination_value),
    ):
        set_schema_field(schema, field_name, field_value)
    client.request("POST", "/api/v1/applications", payload=schema, expected=(201,))


def bootstrap_arr() -> tuple[dict[str, str], dict[str, Any]]:
    states: dict[str, str] = {}
    baselines: dict[str, Any] = {}
    clients: dict[str, HttpClient] = {}
    for service, port in (("sonarr", 8989), ("radarr", 7878), ("prowlarr", 9696)):
        values = read_credential(service)
        client = HttpClient(service, f"http://{service}:{port}", {"X-Api-Key": values["api_key"]})
        version_path = "/api/v1/system/status" if service == "prowlarr" else "/api/v3/system/status"
        client.wait_json(version_path)
        clients[service] = client
        states[service] = "api_ready"
    baselines["sonarr"] = ensure_root_folder(clients["sonarr"], "/data/media/tv")
    baselines["radarr"] = ensure_root_folder(clients["radarr"], "/data/media/movies")
    mock_value = Path("/run/secrets/mock-indexer-token").read_text(encoding="utf-8").strip()
    ensure_prowlarr_indexer(clients["prowlarr"], mock_value)
    ensure_prowlarr_application(
        clients["prowlarr"], "Sonarr", "http://sonarr:8989", read_credential("sonarr")["api_key"]
    )
    ensure_prowlarr_application(
        clients["prowlarr"], "Radarr", "http://radarr:7878", read_credential("radarr")["api_key"]
    )
    indexers = clients["prowlarr"].request("GET", "/api/v1/indexer")
    applications = clients["prowlarr"].request("GET", "/api/v1/applications")
    baselines["prowlarr"] = {
        "applications": sorted(item.get("implementation") for item in applications),
        "indexers": sorted(item.get("name") for item in indexers),
    }
    for service in ("sonarr", "radarr", "prowlarr"):
        states[service] = "baseline_verified"
    return states, baselines


def bootstrap_qbittorrent() -> tuple[str, dict[str, Any]]:
    values = read_credential("qbittorrent")
    client = HttpClient("qbittorrent", "http://qbittorrent:8080", {"Authorization": f"Bearer {values['api_key']}"})
    client.wait_json("/api/v2/app/version")
    categories = client.request("GET", "/api/v2/torrents/categories")
    expected_path = "/data/downloads/arr-lab"
    existing = categories.get("arr-lab")
    if existing is None:
        client.request(
            "POST",
            "/api/v2/torrents/createCategory",
            form={"category": "arr-lab", "savePath": expected_path},
            expected=(200,),
        )
        categories = client.request("GET", "/api/v2/torrents/categories")
        existing = categories.get("arr-lab")
    if not isinstance(existing, dict) or existing.get("savePath") != expected_path:
        raise BootstrapError("qbittorrent category readback failed")
    return "baseline_verified", {"categories": {"arr-lab": {"savePath": expected_path}}}


def jellyfin_authorization() -> str:
    return 'MediaBrowser Client="arr-orchestrator-lab", Device="controller", DeviceId="arr-lab", Version="1.0"'


def authenticate_jellyfin(client: HttpClient, values: dict[str, str]) -> str:
    response = client.request(
        "POST",
        "/Users/AuthenticateByName",
        payload={"Username": values["username"], "Pw": values["password"]},
        headers={"Authorization": jellyfin_authorization()},
    )
    access_value = response.get("AccessToken") if isinstance(response, dict) else None
    if not isinstance(access_value, str) or not access_value:
        raise BootstrapError("jellyfin authentication did not return a token")
    return access_value


def bootstrap_jellyfin() -> tuple[str, dict[str, Any]]:
    values = read_credential("jellyfin")
    client = HttpClient("jellyfin", "http://jellyfin:8096")
    public = client.wait_json("/System/Info/Public", attempts=60)
    if not public.get("StartupWizardCompleted"):
        client.request(
            "POST",
            "/Startup/Configuration",
            payload={
                "ServerName": "arr-orchestrator-lab",
                "UICulture": "en-US",
                "MetadataCountryCode": "US",
                "PreferredMetadataLanguage": "en",
            },
            expected=(204,),
        )
        client.request("GET", "/Startup/User")
        client.request(
            "POST",
            "/Startup/User",
            payload={"Name": values["username"], "Password": values["password"]},
            expected=(204,),
        )
        client.request(
            "POST",
            "/Startup/RemoteAccess",
            payload={"EnableRemoteAccess": False, "EnableAutomaticPortMapping": False},
            expected=(204,),
        )
        client.request("POST", "/Startup/Complete", expected=(204,))
        for _ in range(30):
            public = client.request("GET", "/System/Info/Public")
            if public.get("StartupWizardCompleted"):
                break
            time.sleep(1)
    access_value = authenticate_jellyfin(client, values)
    authenticated = HttpClient("jellyfin", "http://jellyfin:8096", {"X-Emby-Token": access_value})
    authenticated.request("GET", "/System/Info")
    folders = authenticated.request("GET", "/Library/VirtualFolders")
    if folders != []:
        raise BootstrapError("jellyfin virtual-folder baseline is not empty")
    return "baseline_verified", {"virtual_folders": []}


def bootstrap() -> dict[str, Any]:
    states, baselines = bootstrap_arr()
    states["qbittorrent"], baselines["qbittorrent"] = bootstrap_qbittorrent()
    states["jellyfin"], baselines["jellyfin"] = bootstrap_jellyfin()
    return redacted_result(states, baselines)


def main() -> int:
    try:
        result = bootstrap()
    except (BootstrapError, KeyError, OSError, ValueError) as error:
        print(json.dumps({"schema": "arr-orchestrator.lab-bootstrap-error.v1", "ok": False, "error": type(error).__name__, "detail": str(error)}, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
