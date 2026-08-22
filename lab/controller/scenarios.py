from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any


class ScenarioError(RuntimeError):
    pass


DRIVERS = {"controller-api", "host-compose", "runner-config"}
RUNNER_SCENARIOS = {"unsupported-api-version", "stale-plan", "destructive-denial"}
RUNNER_FAULTS = {
    "unsupported-api-version": (
        {
            "service": "prowlarr",
            "request_path": "/api/v1/system/status",
            "fault_api_scenario": "unsupported-version",
        },
        "API_VERSION_UNSUPPORTED",
    ),
    "stale-plan": (
        {
            "plan_inventory_revision": "plan-old",
            "current_inventory_revision": "inventory-new",
        },
        "stale_plan",
    ),
    "destructive-denial": (
        {"operation": "delete", "approval": "absent", "policy_decision": "deny"},
        "destructive_operation_denied",
    ),
}


def load_registry(path: Path | None = None) -> list[dict[str, Any]]:
    registry_path = path or Path(__file__).resolve().parents[1] / "scenarios" / "registry.json"
    try:
        document = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScenarioError("scenario registry is unavailable") from error
    if set(document) != {"schema", "scenarios"} or document.get("schema") != "arr-orchestrator.lab-scenarios.v1":
        raise ScenarioError("scenario registry contract is invalid")
    items = document.get("scenarios")
    if not isinstance(items, list) or not items:
        raise ScenarioError("scenario registry is empty")
    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "driver", "description", "action"}:
            raise ScenarioError("scenario entry contract is invalid")
        name = item.get("name")
        driver = item.get("driver")
        action = item.get("action")
        if not isinstance(name, str) or name in names or driver not in DRIVERS or not isinstance(action, dict):
            raise ScenarioError("scenario entry is invalid")
        encoded = json.dumps(item, sort_keys=True).lower()
        if "command" in encoded or "shell" in encoded:
            raise ScenarioError("scenario registry may not declare commands")
        if driver == "runner-config":
            template_value = action.get("template")
            repository_root = Path(__file__).resolve().parents[2]
            template_root = repository_root / "lab" / "scenarios" / "runner-config"
            if not isinstance(template_value, str):
                raise ScenarioError("runner scenario template is missing")
            template_path = (repository_root / template_value).resolve()
            if template_path.parent != template_root.resolve():
                raise ScenarioError("runner scenario template is outside the committed allowlist")
        names.add(name)
        normalized.append(item)
    return normalized


def scenario_by_name(name: str) -> dict[str, Any]:
    matches = [item for item in load_registry() if item["name"] == name]
    if len(matches) != 1:
        raise ScenarioError("scenario is not allowlisted")
    return matches[0]


def _validate_marked_root(root: Path) -> dict[str, str]:
    absolute = root.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise ScenarioError("scenario runtime path is unavailable") from error
        if stat.S_ISLNK(mode):
            raise ScenarioError("scenario runtime path contains a symlink")
    if absolute.name.startswith("."):
        raise ScenarioError("scenario runtime root is unsafe")
    marker = absolute / ".arr-orchestrator-lab"
    if marker.is_symlink():
        raise ScenarioError("scenario runtime marker is a symlink")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScenarioError("scenario runtime marker is unavailable") from error
    expected = {"compose_project": f"arr-orchestrator-{absolute.name}", "lab_id": absolute.name}
    if data != expected:
        raise ScenarioError("scenario runtime marker mismatch")
    return expected


def apply_runner_config(root: Path, name: str) -> Path:
    item = scenario_by_name(name)
    _validate_marked_root(root)
    state_dir = root / "scenarios"
    state_path = state_dir / "current.json"
    if state_dir.is_symlink():
        raise ScenarioError("scenario state directory is a symlink")
    if name == "healthy":
        if state_path.exists():
            if state_path.is_symlink():
                raise ScenarioError("scenario state is a symlink")
            state_path.unlink()
        if state_dir.exists() and not any(state_dir.iterdir()):
            state_dir.rmdir()
        return state_path
    if item["driver"] != "runner-config" or name not in RUNNER_SCENARIOS:
        raise ScenarioError("scenario is not runner-config driven")
    state_dir.mkdir(mode=0o700, exist_ok=True)
    if state_dir.is_symlink() or state_path.is_symlink():
        raise ScenarioError("scenario state path is unsafe")
    repository_root = Path(__file__).resolve().parents[2]
    template_path = (repository_root / item["action"]["template"]).resolve()
    template_root = (repository_root / "lab" / "scenarios" / "runner-config").resolve()
    if template_path.parent != template_root or not template_path.is_file():
        raise ScenarioError("runner scenario template is outside the committed allowlist")
    try:
        payload = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScenarioError("runner scenario template is invalid") from error
    if (
        set(payload) != {"schema", "scenario", "fault", "expected_finding"}
        or payload.get("schema") != "arr-orchestrator.lab-runner-fault.v1"
        or payload.get("scenario") != name
        or (payload.get("fault"), payload.get("expected_finding")) != RUNNER_FAULTS[name]
    ):
        raise ScenarioError("runner scenario template contract is invalid")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = state_dir / f".current.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise ScenarioError("runner scenario temporary path is unsafe")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    os.chmod(state_dir, 0o700)
    os.chmod(state_path, 0o600)
    if state_path.read_bytes() != encoded:
        raise ScenarioError("runner scenario readback failed")
    return state_path


def verify_host_authority(
    observed: dict[str, Any], project: str, lab_id: str, required_services: set[str]
) -> None:
    if observed.get("project") != project or observed.get("lab_id") != lab_id:
        raise ScenarioError("host scenario project identity mismatch")
    services = observed.get("services")
    if not isinstance(services, dict) or set(services) != required_services:
        raise ScenarioError("host scenario service set mismatch")
    for service in services.values():
        if service.get("project") != project or service.get("lab_id") != lab_id:
            raise ScenarioError("host scenario resource labels mismatch")


def _set_qbittorrent_category(client: Any, save_path: str) -> None:
    categories = client.request("GET", "/api/v2/torrents/categories")
    current = categories.get("arr-lab") if isinstance(categories, dict) else None
    if current is None:
        client.request(
            "POST",
            "/api/v2/torrents/createCategory",
            form={"category": "arr-lab", "savePath": save_path},
            expected=(200,),
        )
    elif not isinstance(current, dict) or current.get("savePath") != save_path:
        client.request(
            "POST",
            "/api/v2/torrents/editCategory",
            form={"category": "arr-lab", "savePath": save_path},
            expected=(200,),
        )
    readback = client.request("GET", "/api/v2/torrents/categories")
    if readback.get("arr-lab", {}).get("savePath") != save_path:
        raise ScenarioError("qBittorrent category scenario readback failed")


def _converge_root_folder(client: Any, desired: str) -> None:
    allowed = {"/data/media/tv", "/data/downloads"}
    if desired not in allowed:
        raise ScenarioError("root-folder scenario target is not allowlisted")
    folders = client.request("GET", "/api/v3/rootfolder")
    paths = {item.get("path") for item in folders}
    if not paths <= allowed:
        raise ScenarioError("root-folder scenario found an unknown root")
    if desired not in paths:
        client.request("POST", "/api/v3/rootfolder", payload={"path": desired}, expected=(201,))
    folders = client.request("GET", "/api/v3/rootfolder")
    desired_rows = [item for item in folders if item.get("path") == desired]
    if len(desired_rows) != 1 or desired_rows[0].get("accessible") is not True:
        raise ScenarioError("root-folder scenario target is not accessible")
    series = client.request("GET", "/api/v3/series")
    for item in folders:
        path = item.get("path")
        identifier = item.get("id")
        if path == desired:
            continue
        if path not in allowed or not isinstance(identifier, int):
            raise ScenarioError("root-folder scenario cannot identify the alternate root")
        prefix = path.rstrip("/") + "/"
        if any(isinstance(row.get("path"), str) and row["path"].startswith(prefix) for row in series):
            raise ScenarioError("root-folder scenario alternate is referenced by a series")
        client.request("DELETE", f"/api/v3/rootfolder/{identifier}", expected=(200,))
    readback = client.request("GET", "/api/v3/rootfolder")
    if [item.get("path") for item in readback] != [desired] or readback[0].get("accessible") is not True:
        raise ScenarioError("root-folder scenario readback failed")


def _set_application_sync(client: Any, sync_level: str) -> None:
    applications = client.request("GET", "/api/v1/applications")
    matches = [item for item in applications if item.get("implementation") == "Sonarr"]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), int):
        raise ScenarioError("canonical Prowlarr Sonarr application is unavailable")
    current = matches[0]
    if current.get("syncLevel") != sync_level:
        payload = json.loads(json.dumps(current))
        payload["syncLevel"] = sync_level
        client.request(
            "PUT",
            f"/api/v1/applications/{current['id']}",
            payload=payload,
            expected=(202,),
        )
    readback = client.request("GET", f"/api/v1/applications/{current['id']}")
    if readback.get("syncLevel") != sync_level:
        raise ScenarioError("Prowlarr application scenario readback failed")


def _normalized_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.rstrip("/") + "/"


def _target_mapping() -> dict[str, str]:
    return {
        "host": "qbittorrent",
        "remotePath": "/data/downloads/",
        "localPath": "/data/media/tv/",
    }


def _mapping_matches(item: dict[str, Any], target: dict[str, str]) -> bool:
    return (
        str(item.get("host", "")).lower() == target["host"]
        and _normalized_path(item.get("remotePath")) == target["remotePath"]
        and _normalized_path(item.get("localPath")) == target["localPath"]
    )


def _converge_path_mapping(client: Any, enabled: bool) -> None:
    target = _target_mapping()
    mappings = client.request("GET", "/api/v3/remotepathmapping")
    exact = [item for item in mappings if _mapping_matches(item, target)]
    conflicts = [
        item
        for item in mappings
        if str(item.get("host", "")).lower() == target["host"]
        and _normalized_path(item.get("remotePath")) == target["remotePath"]
        and not _mapping_matches(item, target)
    ]
    unknown = [item for item in mappings if item not in exact and item not in conflicts]
    if conflicts or unknown or len(exact) > 1:
        raise ScenarioError("path-mapping scenario found an ambiguously owned mapping")
    if enabled and not exact:
        client.request("POST", "/api/v3/remotepathmapping", payload=target, expected=(201,))
    if not enabled and exact:
        identifier = exact[0].get("id")
        if not isinstance(identifier, int):
            raise ScenarioError("path-mapping scenario cannot identify its mapping")
        client.request("DELETE", f"/api/v3/remotepathmapping/{identifier}", expected=(200,))
    readback = client.request("GET", "/api/v3/remotepathmapping")
    matches = [item for item in readback if _mapping_matches(item, target)]
    if (enabled and len(matches) != 1) or (not enabled and readback):
        raise ScenarioError("path-mapping scenario readback failed")


def _controller_state(name: str, clients: dict[str, Any]) -> dict[str, Any]:
    categories = clients["qbittorrent"].request("GET", "/api/v2/torrents/categories")
    roots = {
        service: clients[service].request("GET", "/api/v3/rootfolder")
        for service in ("sonarr", "radarr")
    }
    mappings = clients["sonarr"].request("GET", "/api/v3/remotepathmapping")
    applications = clients["prowlarr"].request("GET", "/api/v1/applications")
    target = _target_mapping()
    matching = [item for item in mappings if _mapping_matches(item, target)]
    return {
        "schema": "arr-orchestrator.lab-controller-scenario.v1",
        "scenario": name,
        "category_path": categories.get("arr-lab", {}).get("savePath"),
        "root_paths": {
            service: sorted(
                item.get("path") for item in service_roots if isinstance(item.get("path"), str)
            )
            for service, service_roots in sorted(roots.items())
        },
        "application_sync": {
            implementation: next(
                (
                    item.get("syncLevel")
                    for item in applications
                    if item.get("implementation") == implementation
                ),
                None,
            )
            for implementation in ("Sonarr", "Radarr")
        },
        "path_mapping": target if len(matching) == 1 and len(mappings) == 1 else None,
    }


def _preflight_controller_state(
    name: str, clients: dict[str, Any], target_state: dict[str, Any]
) -> dict[str, Any]:
    categories = clients["qbittorrent"].request("GET", "/api/v2/torrents/categories")
    if not isinstance(categories, dict):
        raise ScenarioError("qBittorrent category readback is invalid")
    category = categories.get("arr-lab")
    if category is not None and (
        not isinstance(category, dict) or not isinstance(category.get("savePath"), str)
    ):
        raise ScenarioError("qBittorrent category ownership is ambiguous")

    root_paths_by_service: dict[str, list[str]] = {}
    for service, allowed_roots in (
        ("sonarr", {"/data/media/tv", "/data/downloads"}),
        ("radarr", {"/data/media/movies"}),
    ):
        roots = clients[service].request("GET", "/api/v3/rootfolder")
        if not isinstance(roots, list):
            raise ScenarioError(f"{service} root-folder readback is invalid")
        root_paths: list[str] = []
        root_ids: set[int] = set()
        for item in roots:
            if not isinstance(item, dict):
                raise ScenarioError(f"{service} root-folder ownership is ambiguous")
            path = item.get("path")
            identifier = item.get("id")
            if path not in allowed_roots or not isinstance(identifier, int):
                raise ScenarioError("root-folder scenario found an unknown root")
            if path in root_paths or identifier in root_ids:
                raise ScenarioError(f"{service} root-folder ownership is ambiguous")
            root_paths.append(path)
            root_ids.add(identifier)
        root_paths_by_service[service] = sorted(root_paths)

    mappings = clients["sonarr"].request("GET", "/api/v3/remotepathmapping")
    if not isinstance(mappings, list):
        raise ScenarioError("Sonarr path-mapping readback is invalid")
    mapping_target = _target_mapping()
    exact_mappings = [
        item for item in mappings
        if isinstance(item, dict) and _mapping_matches(item, mapping_target)
    ]
    if len(exact_mappings) > 1 or any(item not in exact_mappings for item in mappings):
        raise ScenarioError("path-mapping scenario found an ambiguously owned mapping")
    if exact_mappings and not isinstance(exact_mappings[0].get("id"), int):
        raise ScenarioError("path-mapping scenario cannot identify its mapping")

    applications = clients["prowlarr"].request("GET", "/api/v1/applications")
    if not isinstance(applications, list):
        raise ScenarioError("Prowlarr application readback is invalid")
    applications_by_name: dict[str, dict[str, Any]] = {}
    for implementation in ("Sonarr", "Radarr"):
        matches = [
            item for item in applications
            if isinstance(item, dict) and item.get("implementation") == implementation
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), int):
            raise ScenarioError(
                f"canonical Prowlarr {implementation} application ownership is ambiguous"
            )
        applications_by_name[implementation] = matches[0]

    desired_root = target_state["root_paths"]["sonarr"][0]
    alternate_roots = [path for path in root_paths_by_service["sonarr"] if path != desired_root]
    if alternate_roots:
        series = clients["sonarr"].request("GET", "/api/v3/series")
        if not isinstance(series, list):
            raise ScenarioError("Sonarr series readback is invalid")
        for alternate in alternate_roots:
            prefix = alternate.rstrip("/") + "/"
            if any(
                isinstance(row, dict)
                and isinstance(row.get("path"), str)
                and row["path"].startswith(prefix)
                for row in series
            ):
                raise ScenarioError("root-folder scenario alternate is referenced by a series")

    return {
        "schema": "arr-orchestrator.lab-controller-scenario.v1",
        "scenario": name,
        "category_path": category.get("savePath") if category else None,
        "root_paths": dict(sorted(root_paths_by_service.items())),
        "application_sync": {
            implementation: applications_by_name[implementation].get("syncLevel")
            for implementation in ("Sonarr", "Radarr")
        },
        "path_mapping": mapping_target if len(exact_mappings) == 1 else None,
    }


def _controller_target(name: str) -> dict[str, Any]:
    return {
        "category_path": "/data/downloads" if name == "category-mismatch" else "/data/downloads/arr-lab",
        "root_paths": {
            "sonarr": ["/data/downloads"] if name == "root-folder-mismatch" else ["/data/media/tv"],
            "radarr": ["/data/media/movies"],
        },
        "application_sync": {
            "Sonarr": "disabled" if name == "application-sync-mismatch" else "addOnly",
            "Radarr": "addOnly",
        },
        "path_mapping": _target_mapping() if name == "path-mapping-mismatch" else None,
    }


def _state_matches_target(state: dict[str, Any], target: dict[str, Any]) -> bool:
    return all(state.get(key) == value for key, value in target.items())


def apply_controller_scenario(name: str, clients: dict[str, Any]) -> dict[str, Any]:
    item = scenario_by_name(name)
    if item["driver"] != "controller-api":
        raise ScenarioError("scenario is not controller-api driven")
    required = {"qbittorrent", "sonarr", "radarr", "prowlarr"}
    if set(clients) != required:
        raise ScenarioError("controller scenario client set mismatch")
    target = _controller_target(name)
    current = _preflight_controller_state(name, clients, target)
    if _state_matches_target(current, target):
        return current

    _converge_root_folder(clients["sonarr"], target["root_paths"]["sonarr"][0])
    _set_qbittorrent_category(clients["qbittorrent"], target["category_path"])
    _set_application_sync(clients["prowlarr"], target["application_sync"]["Sonarr"])
    _converge_path_mapping(clients["sonarr"], target["path_mapping"] is not None)

    final = _controller_state(name, clients)
    if not _state_matches_target(final, target):
        raise ScenarioError("controller scenario final readback did not match its target")
    return final


def controller_clients() -> dict[str, Any]:
    from lab.controller.bootstrap import HttpClient, read_credential

    sonarr = read_credential("sonarr")
    radarr = read_credential("radarr")
    prowlarr = read_credential("prowlarr")
    qbittorrent = read_credential("qbittorrent")
    return {
        "sonarr": HttpClient(
            "sonarr", "http://sonarr:8989", {"X-Api-Key": sonarr["api_key"]}
        ),
        "radarr": HttpClient(
            "radarr", "http://radarr:7878", {"X-Api-Key": radarr["api_key"]}
        ),
        "prowlarr": HttpClient(
            "prowlarr", "http://prowlarr:9696", {"X-Api-Key": prowlarr["api_key"]}
        ),
        "qbittorrent": HttpClient(
            "qbittorrent",
            "http://qbittorrent:8080",
            {"Authorization": f"Bearer {qbittorrent['api_key']}"},
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m lab.controller.scenarios")
    parser.add_argument("scenario")
    args = parser.parse_args(argv)
    try:
        result = apply_controller_scenario(args.scenario, controller_clients())
    except (ScenarioError, KeyError, OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "schema": "arr-orchestrator.lab-controller-scenario-error.v1",
                    "ok": False,
                    "error": type(error).__name__,
                    "detail": str(error),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps({**result, "ok": True}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
