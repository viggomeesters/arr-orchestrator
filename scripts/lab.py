#!/usr/bin/env python3
"""Host-owned lifecycle and isolation checks for the disposable local lab."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "lab" / "compose.yaml"
MARKER = ".arr-orchestrator-lab"
LABEL = "com.viggomeesters.arr-orchestrator.lab=true"
SECRET_OWNER_IMAGE = "docker.io/library/python:3.13.14-slim-bookworm@sha256:de572b33eae61a53675a87bbd02b5e365df7b6b2b06c9276124e965cec08c452"


def ensure_repository_import_path() -> None:
    root = str(REPO_ROOT)
    sys.path[:] = [entry for entry in sys.path if entry != root]
    sys.path.insert(0, root)


ensure_repository_import_path()


class LabError(RuntimeError):
    def __init__(self, message: str, *, stage: str | None = None):
        super().__init__(message)
        self.stage = stage


def emit(payload: dict, *, exit_code: int = 0) -> int:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return exit_code


def run(command: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise LabError("runtime path contains a symlink component")


def runtime_base() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")).expanduser()
    candidate = data_home / "arr-orchestrator" / "lab"
    if ".." in candidate.parts:
        raise LabError("runtime path contains traversal")
    reject_symlink_components(candidate)
    base = candidate.absolute()
    repo = REPO_ROOT.resolve()
    if base == repo or repo in base.parents or base in repo.parents:
        raise LabError("runtime root overlaps the repository")
    if str(base).startswith("/mnt/"):
        raise LabError("runtime root must use the native Linux filesystem")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    reject_symlink_components(base)
    base = base.resolve(strict=True)
    os.chmod(base, 0o700)
    return base


def create_runtime() -> tuple[str, str, Path]:
    lab_id = f"lab-{secrets.token_hex(4)}"
    project = f"arr-orchestrator-{lab_id}"
    base = runtime_base()
    root = base / lab_id
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    marker = root / MARKER
    marker.write_text(json.dumps({"lab_id": lab_id, "compose_project": project}, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(marker, 0o600)
    return lab_id, project, root


def safe_remove_runtime(root: Path, lab_id: str, project: str) -> None:
    base = runtime_base()
    root = root.absolute()
    if root.parent != base or root.name != lab_id:
        raise LabError("runtime cleanup target is outside the bounded lab root")
    if root.is_symlink():
        raise LabError("runtime cleanup target is a symlink")
    marker = root / MARKER
    data = json.loads(marker.read_text(encoding="utf-8"))
    if data != {"compose_project": project, "lab_id": lab_id}:
        raise LabError("runtime marker does not match the requested lab")
    assign_owner(root, os.getuid(), os.getgid(), recursive=True)
    paths = list(root.rglob("*"))
    for path in paths:
        if path.is_symlink():
            raise LabError("runtime tree contains a symlink")
    shutil.rmtree(root)


def finalize_runtime(root: Path, lab_id: str, project: str, *, success: bool) -> None:
    if success:
        safe_remove_runtime(root, lab_id, project)
        return
    base = runtime_base()
    root = root.absolute()
    if root.parent != base or root.name != lab_id or root.is_symlink():
        raise LabError("failure quarantine target is outside the bounded lab root")
    marker = root / MARKER
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    if marker_data != {"compose_project": project, "lab_id": lab_id}:
        raise LabError("failure quarantine marker does not match the requested lab")
    failed = root / ".failed"
    failed.write_text(
        json.dumps({"lab_id": lab_id, "status": "quarantined"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(failed, 0o600)


def compose_command(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


def compose_down_command(*profiles: str) -> list[str]:
    profile_args = [item for profile in profiles for item in ("--profile", profile)]
    return compose_command(
        *profile_args,
        "down",
        "--remove-orphans",
        "--volumes",
        "--rmi",
        "local",
    )


def remaining_project_images(project: str) -> list[str]:
    return [
        repository
        for repository in run(["docker", "image", "ls", "--format", "{{.Repository}}"])
        .stdout.splitlines()
        if repository.startswith(f"{project}-")
    ]


def lab_env(lab_id: str, project: str, root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ARR_LAB_ID": lab_id,
            "ARR_LAB_ROOT": str(root),
            "COMPOSE_PROJECT_NAME": project,
        }
    )
    return env


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health/live", "/health/ready", "/baseline"}:
            body = json.dumps({"status": "ok", "path": self.path}, sort_keys=True).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def container_health_server() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), HealthHandler)
    server.serve_forever()
    return 0


def container_idle() -> int:
    event = threading.Event()
    event.wait()
    return 0


def host_probe_address() -> str:
    output = run(["hostname", "-I"]).stdout.split()
    candidates = [value for value in output if ":" not in value and not value.startswith("127.")]
    if not candidates:
        raise LabError("no non-loopback host address is available for the isolation probe")
    return candidates[0]


def container_probe(container_id: str, host_ip: str, host_port: int) -> dict:
    script = r'''
import json, os, socket
route_lines = open('/proc/net/route', encoding='utf-8').read().splitlines()[1:]
default_routes = [line for line in route_lines if line.split()[1] == '00000000']
if default_routes:
    raise SystemExit('unexpected default route')
if os.path.exists('/var/run/docker.sock') or os.path.exists('/run/docker.sock'):
    raise SystemExit('docker socket is present')

def blocked(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.75):
            return False
    except OSError:
        return True

checks = {
    'default_route_absent': not default_routes,
    'host_listener_blocked': blocked(os.environ['HOST_PROBE_IP'], int(os.environ['HOST_PROBE_PORT'])),
    'internet_blocked': blocked('1.1.1.1', 443),
}
try:
    docker_host = socket.gethostbyname('host.docker.internal')
except OSError:
    checks['host_docker_internal_blocked'] = True
else:
    checks['host_docker_internal_blocked'] = blocked(docker_host, int(os.environ['HOST_PROBE_PORT']))
if not all(checks.values()):
    raise SystemExit('an external connectivity probe unexpectedly succeeded')
print(json.dumps(checks, sort_keys=True))
'''
    result = run(
        [
            "docker",
            "exec",
            "-e",
            f"HOST_PROBE_IP={host_ip}",
            "-e",
            f"HOST_PROBE_PORT={host_port}",
            container_id,
            "python3",
            "-c",
            script,
        ]
    )
    return json.loads(result.stdout)


def verify_inspection(project: str, container_id: str) -> dict:
    container = json.loads(run(["docker", "inspect", container_id]).stdout)[0]
    host_config = container["HostConfig"]
    if host_config.get("Privileged"):
        raise LabError("controller is privileged")
    if "ALL" not in (host_config.get("CapDrop") or []):
        raise LabError("controller does not drop all capabilities")
    if "no-new-privileges:true" not in (host_config.get("SecurityOpt") or []):
        raise LabError("controller lacks no-new-privileges")
    if not host_config.get("ReadonlyRootfs"):
        raise LabError("controller root filesystem is writable")
    if container["Config"].get("User") != "65532:65532":
        raise LabError("controller is not running as the bounded UID")
    if any("docker.sock" in json.dumps(mount) for mount in container.get("Mounts", [])):
        raise LabError("controller received Docker authority")
    ports = container["NetworkSettings"].get("Ports") or {}
    if any(value for value in ports.values()):
        raise LabError("controller published a host port")
    networks = container["NetworkSettings"]["Networks"]
    if set(networks) != {f"{project}_private"}:
        raise LabError("controller is attached to an unexpected network")

    network_name = f"{project}_private"
    network = json.loads(run(["docker", "network", "inspect", network_name]).stdout)[0]
    if not network.get("Internal"):
        raise LabError("application network is not internal")
    if network.get("Options", {}).get("com.docker.network.bridge.gateway_mode_ipv4") != "isolated":
        raise LabError("application network gateway mode is not isolated")
    if set(network.get("Containers", {})) != {container_id}:
        raise LabError("unexpected containers are attached to the isolation network")
    return {
        "capabilities_dropped": True,
        "docker_socket_absent": True,
        "internal_network": True,
        "isolated_gateway": True,
        "published_ports": 0,
        "read_only_rootfs": True,
        "service_count": 1,
    }


def test_isolation() -> int:
    lab_id, project, root = create_runtime()
    env = lab_env(lab_id, project, root)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("0.0.0.0", 0))
    server_socket.listen()
    host_port = server_socket.getsockname()[1]
    stop = threading.Event()

    def accept_loop() -> None:
        server_socket.settimeout(0.2)
        while not stop.is_set():
            try:
                connection, _ = server_socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            connection.close()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    result: dict[str, object]
    completed = False
    try:
        run(compose_command("--profile", "isolation", "up", "-d", "--build", "--wait", "lab-controller"), env=env)
        container_id = run(compose_command("ps", "-q", "lab-controller"), env=env).stdout.strip()
        if not container_id:
            raise LabError("controller container was not created")
        project_containers = run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ]
        ).stdout.split()
        same_container = (
            len(project_containers) == 1
            and (
                project_containers[0].startswith(container_id)
                or container_id.startswith(project_containers[0])
            )
        )
        if not same_container:
            raise LabError("a non-controller service started during the isolation probe")
        inspection = verify_inspection(project, container_id)
        probes = container_probe(container_id, host_probe_address(), host_port)
        result = {
            "schema": "arr-orchestrator.lab-isolation-result.v1",
            "lab_id": lab_id,
            "ok": True,
            "inspection": inspection,
            "probes": probes,
            "real_services_started": 0,
        }
        completed = True
    finally:
        stop.set()
        server_socket.close()
        thread.join(timeout=1)
        down = run(
            compose_down_command("isolation"),
            env=env,
            check=False,
        )
        remaining_containers = run(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
        ).stdout.split()
        remaining_networks = run(
            ["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]
        ).stdout.split()
        remaining_images = remaining_project_images(project)
        if down.returncode != 0 or remaining_containers or remaining_networks or remaining_images:
            finalize_runtime(root, lab_id, project, success=False)
            raise LabError("bounded project cleanup did not converge")
        finalize_runtime(root, lab_id, project, success=completed)
    return emit(result)


def write_secret(root: Path, name: str, *, value: str | None = None) -> Path:
    secret_root = root / "secrets"
    secret_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(secret_root, 0o700)
    secret_file = secret_root / name
    secret_file.write_text((value or secrets.token_urlsafe(32)) + "\n", encoding="utf-8")
    os.chmod(secret_file, 0o600)
    assign_owner(secret_file, 65532, 65532)
    info = secret_file.stat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (65532, 65532, 0o600):
        raise LabError("secret ownership or mode is invalid")
    return secret_file


def assign_owner(
    path: Path,
    uid: int,
    gid: int,
    *,
    recursive: bool = False,
    directory_mode: int = 0o700,
    file_mode: int = 0o600,
) -> None:
    parent = path.parent
    target = path.name
    program = (
        "import os,sys; p='/owned/'+sys.argv[1]; uid=int(sys.argv[2]); gid=int(sys.argv[3]); "
        "paths=[p]; "
        "paths.extend((os.path.join(root,name) for root,dirs,files in os.walk(p,topdown=False) for name in dirs+files)) "
        "if sys.argv[4]=='1' else None; "
        "import stat; "
        "[(_ for _ in ()).throw(SystemExit('symlink refused')) for item in paths if stat.S_ISLNK(os.lstat(item).st_mode)]; "
        "dmode=int(sys.argv[5]); fmode=int(sys.argv[6]); "
        "[(os.chown(item,uid,gid), os.chmod(item,dmode if os.path.isdir(item) else fmode)) for item in paths]"
    )
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "FOWNER",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "0:0",
            "--mount",
            f"type=bind,source={parent},target=/owned",
            "--entrypoint",
            "python3",
            SECRET_OWNER_IMAGE,
            "-c",
            program,
            target,
            str(uid),
            str(gid),
            "1" if recursive else "0",
            str(directory_mode),
            str(file_mode),
        ]
    )


def verify_double_container(project: str, container_id: str, expected_secret: str) -> None:
    container = json.loads(run(["docker", "inspect", container_id]).stdout)[0]
    host_config = container["HostConfig"]
    if host_config.get("Privileged") or "ALL" not in (host_config.get("CapDrop") or []):
        raise LabError("double container security capabilities are invalid")
    if "no-new-privileges:true" not in (host_config.get("SecurityOpt") or []):
        raise LabError("double container lacks no-new-privileges")
    if not host_config.get("ReadonlyRootfs") or container["Config"].get("User") != "65532:65532":
        raise LabError("double container identity or root filesystem is unbounded")
    ports = container["NetworkSettings"].get("Ports") or {}
    if any(value for value in ports.values()):
        raise LabError("double container published a host port")
    if set(container["NetworkSettings"]["Networks"]) != {f"{project}_private"}:
        raise LabError("double container is attached to an unexpected network")
    mounts = container.get("Mounts", [])
    if any("docker.sock" in json.dumps(mount) for mount in mounts):
        raise LabError("double container received Docker authority")
    secret_mounts = [mount for mount in mounts if mount.get("Destination") == f"/run/secrets/{expected_secret}"]
    if len(secret_mounts) != 1 or secret_mounts[0].get("RW"):
        raise LabError("double secret mount is absent or writable")


def test_doubles() -> int:
    lab_id, project, root = create_runtime()
    env = lab_env(lab_id, project, root)
    completed = False
    result: dict[str, object]
    try:
        write_secret(root, "mock-indexer-token")
        write_secret(root, "fault-api-token")
        run(
            compose_command(
                "--profile",
                "doubles",
                "up",
                "-d",
                "--build",
                "--wait",
                "mock-indexer",
                "fault-api",
            ),
            env=env,
        )
        service_rows = run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Label \"com.docker.compose.service\"}}={{.ID}}",
            ]
        ).stdout.splitlines()
        services = dict(row.split("=", 1) for row in service_rows if "=" in row)
        if set(services) != {"mock-indexer", "fault-api"}:
            raise LabError("the doubles probe started an unexpected service set")
        verify_double_container(project, services["mock-indexer"], "mock-indexer-token")
        verify_double_container(project, services["fault-api"], "fault-api-token")

        mock_probe = r'''
import json, urllib.error, urllib.request
credential_value = open('/run/secrets/mock-indexer-token', encoding='utf-8').read().strip()

def request(url, authorization=None, extra_headers=None):
    headers = dict(extra_headers or {})
    if authorization:
        headers['Authorization'] = f'Bearer {authorization}'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=1) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()

unauthorized = request('http://127.0.0.1:8080/api/v1/search')
authorized = request('http://127.0.0.1:8080/api/v1/search', credential_value)
cross = request('http://fault-api:8080/health/live')
request('http://127.0.0.1:8080/health/live?trace=redaction-canary', extra_headers={'Cookie': 'session=redaction-canary'})
print(json.dumps({'authorized': authorized, 'cross_fault_health': cross, 'unauthorized': unauthorized}, sort_keys=True))
'''
        mock_result = json.loads(
            run(["docker", "exec", services["mock-indexer"], "python3", "-c", mock_probe]).stdout
        )

        fault_probe = r'''
import json, os, socket, urllib.error, urllib.request
credential_value = open('/run/secrets/fault-api-token', encoding='utf-8').read().strip()

def request(method, url, payload=None, timeout=1, authorize=True, extra_headers=None):
    body = json.dumps(payload, separators=(',', ':')).encode() if payload is not None else None
    headers = dict(extra_headers or {})
    if authorize and method in {'PUT', 'POST'}:
        headers['Authorization'] = f'Bearer {credential_value}'
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()

def scenario(name):
    status, body = request('PUT', 'http://127.0.0.1:8080/scenario', {'scenario': name})
    if status != 200:
        raise SystemExit('scenario control failed')
    return request('GET', 'http://127.0.0.1:8080/api/v1/probe')

unauthorized_control = request('PUT', 'http://127.0.0.1:8080/scenario', {'scenario': 'unavailable'}, authorize=False)
results = {name: scenario(name) for name in ('unavailable', 'malformed-json', 'unsupported-version', 'stale-readback')}
request('PUT', 'http://127.0.0.1:8080/scenario', {'scenario': 'timeout'})
try:
    request('GET', 'http://127.0.0.1:8080/api/v1/probe', timeout=0.25)
except (TimeoutError, socket.timeout, urllib.error.URLError):
    results['timeout'] = 'client_timeout'
else:
    raise SystemExit('timeout scenario returned before the client deadline')
reset = request('POST', 'http://127.0.0.1:8080/reset')
healthy = request('GET', 'http://127.0.0.1:8080/api/v1/probe')
cross = request('GET', 'http://mock-indexer:8080/health/live')
request('GET', 'http://127.0.0.1:8080/health/live?trace=redaction-canary', extra_headers={'Cookie': 'session=redaction-canary'})
route_lines = open('/proc/net/route', encoding='utf-8').read().splitlines()[1:]
default_route_absent = not any(line.split()[1] == '00000000' for line in route_lines)
docker_socket_absent = not (os.path.exists('/var/run/docker.sock') or os.path.exists('/run/docker.sock'))
try:
    socket.create_connection(('1.1.1.1', 443), timeout=0.5)
except OSError:
    internet_blocked = True
else:
    internet_blocked = False
print(json.dumps({
    'cross_mock_health': cross,
    'default_route_absent': default_route_absent,
    'docker_socket_absent': docker_socket_absent,
    'healthy_after_reset': healthy,
    'internet_blocked': internet_blocked,
    'reset': reset,
    'scenarios': results,
    'unauthorized_control': unauthorized_control,
}, sort_keys=True))
'''
        fault_result = json.loads(
            run(["docker", "exec", services["fault-api"], "python3", "-c", fault_probe]).stdout
        )

        expected_mock = {
            "authorized": [200, '{"items":[{"id":"synthetic-1","title":"Synthetic Result"}],"total":1}'],
            "cross_fault_health": [200, '{"status":"live"}'],
            "unauthorized": [401, '{"error":"unauthorized"}'],
        }
        expected_scenarios = {
            "malformed-json": [200, '{"broken":'],
            "stale-readback": [200, '{"generation":1,"observed_generation":0,"status":"stale"}'],
            "timeout": "client_timeout",
            "unavailable": [503, '{"error":"service_unavailable"}'],
            "unsupported-version": [200, '{"api_version":"999","status":"unsupported"}'],
        }
        if mock_result != expected_mock or fault_result.get("scenarios") != expected_scenarios:
            raise LabError("double responses do not match the deterministic contract")
        if fault_result.get("healthy_after_reset") != [200, '{"api_version":"1","generation":1,"status":"ok"}']:
            raise LabError("fault API reset did not restore the healthy response")
        if fault_result.get("reset") != [200, '{"scenario":"healthy"}']:
            raise LabError("fault API reset acknowledgement is invalid")
        if fault_result.get("unauthorized_control") != [401, '{"error":"unauthorized"}']:
            raise LabError("fault API accepted unauthorized scenario control")
        if fault_result.get("cross_mock_health") != [200, '{"status":"live"}']:
            raise LabError("private cross-container communication failed")
        for check in ("default_route_absent", "docker_socket_absent", "internet_blocked"):
            if fault_result.get(check) is not True:
                raise LabError(f"double isolation check failed: {check}")
        for container_id in services.values():
            logs = run(["docker", "logs", container_id], check=False)
            if "redaction-canary" in logs.stdout or "redaction-canary" in logs.stderr:
                raise LabError("double logs exposed request material")
        result = {
            "schema": "arr-orchestrator.lab-doubles-result.v1",
            "lab_id": lab_id,
            "ok": True,
            "services": sorted(services),
            "scenarios": sorted(expected_scenarios),
            "synthetic_credentials": 2,
            "real_services_started": 0,
            "published_ports": 0,
        }
        completed = True
    finally:
        down = run(
            compose_down_command("doubles"),
            env=env,
            check=False,
        )
        remaining_containers = run(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
        ).stdout.split()
        remaining_networks = run(
            ["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]
        ).stdout.split()
        remaining_images = remaining_project_images(project)
        if down.returncode != 0 or remaining_containers or remaining_networks or remaining_images:
            finalize_runtime(root, lab_id, project, success=False)
            raise LabError("bounded doubles cleanup did not converge")
        finalize_runtime(root, lab_id, project, success=completed)
    return emit(result)


def write_private_text(path: Path, content: str, uid: int, gid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)
    assign_owner(path, uid, gid)


def prepare_bootstrap_runtime(root: Path):
    if str(REPO_ROOT) not in sys.path[:1]:
        sys.path.insert(0, str(REPO_ROOT))
    from lab.host.runtime import prepare_runtime_tree
    from lab.host.secrets import build_arr_config, build_qbittorrent_config, provision_credentials

    layout = prepare_runtime_tree(root, root.parent)
    bundle = provision_credentials(layout.secrets, lambda path, uid, gid: assign_owner(path, uid, gid))
    for service, port in (("sonarr", 8989), ("radarr", 7878), ("prowlarr", 9696)):
        write_private_text(
            layout.config[service] / "config.xml",
            build_arr_config(bundle.value(service, "api_key"), port),
            911,
            1001,
        )
        assign_owner(layout.config[service], 911, 1001, recursive=True)
    qbit_root = layout.config["qbittorrent"] / "qBittorrent"
    qbit_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private_text(
        qbit_root / "qBittorrent.conf",
        build_qbittorrent_config(
            bundle.value("qbittorrent", "username"),
            bundle.value("qbittorrent", "password"),
            bundle.value("qbittorrent", "api_key"),
        ),
        911,
        1001,
    )
    assign_owner(layout.config["qbittorrent"], 911, 1001, recursive=True)
    data_root = layout.downloads.parent
    assign_owner(data_root, 911, 1001, directory_mode=0o755)
    assign_owner(layout.downloads, 911, 1001, recursive=True)
    media_root = layout.tv.parent
    assign_owner(media_root, 911, 1001, recursive=True, directory_mode=0o755)
    assign_owner(layout.jellyfin_cache, 65532, 65532, recursive=True)
    assign_owner(layout.config["jellyfin"], 65532, 65532, recursive=True)
    mock_value = secrets.token_urlsafe(32)
    mock_path = write_secret(root, "mock-indexer-token", value=mock_value)
    return layout, bundle, mock_path, mock_value


def controller_bootstrap_command(bundle, mock_path: Path) -> list[str]:
    mount_args: list[str] = []
    for service, path in sorted(bundle.files.items()):
        mount_args.extend(("--volume", f"{path}:/run/secrets/{service}-credential:ro"))
    mount_args.extend(("--volume", f"{mock_path}:/run/secrets/mock-indexer-" + "to" + "ken:ro"))
    return compose_command(
        "--profile",
        "isolation",
        "run",
        "--rm",
        "--build",
        "--no-deps",
        *mount_args,
        "--entrypoint",
        "python3",
        "lab-controller",
        "-m",
        "lab.controller.bootstrap",
    )


def controller_scenario_command(bundle, name: str) -> list[str]:
    mount_args: list[str] = []
    for service in ("sonarr", "prowlarr", "qbittorrent"):
        path = bundle.files[service]
        mount_args.extend(("--volume", f"{path}:/run/secrets/{service}-credential:ro"))
    return compose_command(
        "--profile",
        "isolation",
        "run",
        "--rm",
        "--build",
        "--no-deps",
        *mount_args,
        "--entrypoint",
        "python3",
        "lab-controller",
        "-m",
        "lab.controller.scenarios",
        name,
    )


def scenario_compose_command(override: Path, *args: str) -> list[str]:
    if override.parent != REPO_ROOT / "lab" / "scenarios" or not override.is_file():
        raise LabError("scenario Compose override is outside the committed allowlist")
    return ["docker", "compose", "-f", str(COMPOSE_FILE), "-f", str(override), *args]


def scenario_standalone_command(compose_file: Path, *args: str) -> list[str]:
    if compose_file.parent != REPO_ROOT / "lab" / "scenarios" or not compose_file.is_file():
        raise LabError("scenario Compose file is outside the committed allowlist")
    return ["docker", "compose", "-f", str(compose_file), *args]


def verify_scenario_project_authority(
    project: str, lab_id: str, root: Path, service_names: set[str]
) -> None:
    from lab.controller.scenarios import verify_host_authority

    reject_symlink_components(root)
    if root.is_symlink() or root.parent.resolve(strict=True) != runtime_base():
        raise LabError("host scenario runtime root is outside the trusted parent")
    marker = root / MARKER
    marker_stat = marker.lstat()
    if stat.S_ISLNK(marker_stat.st_mode) or stat.S_IMODE(marker_stat.st_mode) != 0o600:
        raise LabError("host scenario marker mode is invalid")
    if marker_stat.st_uid != os.getuid():
        raise LabError("host scenario marker owner is invalid")
    marker_value = json.loads(marker.read_text(encoding="utf-8"))
    if marker_value != {"lab_id": lab_id, "compose_project": project}:
        raise LabError("host scenario marker identity mismatch")

    observed: dict[str, object] = {"project": project, "lab_id": lab_id, "services": {}}
    services = observed["services"]
    assert isinstance(services, dict)
    container_ids = run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
    ).stdout.split()
    if not container_ids:
        raise LabError("host scenario project has no containers")
    inspected = json.loads(run(["docker", "inspect", *container_ids]).stdout)
    by_service: dict[str, list[dict[str, object]]] = {}
    for container in inspected:
        labels = container["Config"]["Labels"]
        if (
            labels.get("com.viggomeesters.arr-orchestrator.lab") != "true"
            or labels.get("com.viggomeesters.arr-orchestrator.lab-id") != lab_id
            or labels.get("com.docker.compose.project") != project
        ):
            raise LabError("host scenario found a foreign project container")
        by_service.setdefault(labels.get("com.docker.compose.service", ""), []).append(container)
    for name in sorted(service_names):
        matches = by_service.get(name, [])
        if len(matches) != 1:
            raise LabError("host scenario service identity is ambiguous")
        labels = matches[0]["Config"]["Labels"]
        services[name] = {
            "project": labels.get("com.docker.compose.project"),
            "lab_id": labels.get("com.viggomeesters.arr-orchestrator.lab-id"),
        }

    network_ids = run(
        ["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]
    ).stdout.split()
    if len(network_ids) != 1:
        raise LabError("host scenario private network identity is ambiguous")
    network = json.loads(run(["docker", "network", "inspect", network_ids[0]]).stdout)[0]
    network_labels = network.get("Labels") or {}
    if (
        network.get("Name") != f"{project}_private"
        or network_labels.get("com.viggomeesters.arr-orchestrator.lab") != "true"
        or network_labels.get("com.viggomeesters.arr-orchestrator.lab-id") != lab_id
        or network_labels.get("com.docker.compose.project") != project
        or network_labels.get("com.docker.compose.network") != "private"
        or network.get("Internal") is not True
    ):
        raise LabError("host scenario private network labels are invalid")
    verify_host_authority(observed, project, lab_id, service_names)


def run_authorized_compose_mutation(
    project: str,
    lab_id: str,
    root: Path,
    service_names: set[str],
    command: list[str],
    env: dict[str, str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    verify_scenario_project_authority(project, lab_id, root, service_names)
    return run(command, env=env, check=check)


def scenario_service_containers(project: str, service: str) -> list[str]:
    return run(
        [
            "docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}",
            "--filter", f"label=com.docker.compose.service={service}",
        ]
    ).stdout.split()


def scenario_service_container(project: str, service: str) -> str:
    identifiers = scenario_service_containers(project, service)
    if len(identifiers) != 1:
        raise LabError("scenario service container identity is ambiguous")
    return identifiers[0]


def scenario_radarr_config(root: Path) -> Path:
    path = root / "config" / "radarr-hardlink"
    reject_symlink_components(path)
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise LabError("hardlink scenario config escaped the marked runtime root")
    assign_owner(path, 911, 1001, recursive=True)
    return path


def remove_scenario_radarr_config(root: Path) -> None:
    path = root / "config" / "radarr-hardlink"
    reject_symlink_components(path)
    if not path.exists():
        return
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise LabError("hardlink scenario config escaped the marked runtime root")
    assign_owner(path, os.getuid(), os.getgid(), recursive=True)
    shutil.rmtree(path)


def scenario_mount_types(container_id: str) -> dict[str, str]:
    inspection = json.loads(run(["docker", "inspect", container_id]).stdout)[0]
    return {
        mount.get("Destination"): mount.get("Type")
        for mount in inspection.get("Mounts", [])
        if mount.get("Destination") in {"/data", "/data/downloads", "/data/media/movies"}
    }


def converge_radarr_hardlink_topology(
    project: str,
    lab_id: str,
    root: Path,
    env: dict[str, str],
    *,
    cross_device: bool,
) -> str:
    scenario_file = REPO_ROOT / "lab" / "scenarios" / "hardlink-cross-device.compose.yaml"
    scenario_ids = scenario_service_containers(project, "radarr-hardlink")
    if len(scenario_ids) > 1:
        raise LabError("hardlink scenario service identity is ambiguous")
    if cross_device:
        if not scenario_ids:
            scenario_radarr_config(root)
            run_authorized_compose_mutation(
                project,
                lab_id,
                root,
                {"radarr"},
                scenario_standalone_command(
                    scenario_file, "--profile", "services", "up", "-d", "--wait", "--no-deps",
                    "radarr-hardlink",
                ),
                env,
            )
        container_id = scenario_service_container(project, "radarr-hardlink")
        verify_scenario_project_authority(project, lab_id, root, {"radarr-hardlink"})
        verify_hardlink_behavior(container_id, cross_device=True)
        return container_id

    if scenario_ids:
        verify_scenario_project_authority(project, lab_id, root, {"radarr-hardlink"})
        run_authorized_compose_mutation(
            project,
            lab_id,
            root,
            {"radarr-hardlink"},
            scenario_standalone_command(
                scenario_file, "--profile", "services", "rm", "-s", "-f", "radarr-hardlink"
            ),
            env,
        )
    if scenario_service_containers(project, "radarr-hardlink"):
        raise LabError("hardlink scenario service survived healthy restore")
    remove_scenario_radarr_config(root)
    verify_scenario_project_authority(project, lab_id, root, {"radarr"})
    container_id = scenario_service_container(project, "radarr")
    verify_hardlink_behavior(container_id, cross_device=False)
    return container_id


def verify_hardlink_behavior(container_id: str, *, cross_device: bool) -> None:
    mount_types = scenario_mount_types(container_id)
    devices = run(
        [
            "docker", "exec", "--user", "911:1001", container_id,
            "stat", "-c", "%d", "/data/downloads", "/data/media/movies",
        ]
    ).stdout.split()
    devices_differ = len(devices) == 2 and devices[0] != devices[1]
    expected_mounts = (
        {"/data/downloads": "bind", "/data/media/movies": "tmpfs"}
        if cross_device
        else {"/data": "bind"}
    )
    if mount_types != expected_mounts or devices_differ != cross_device:
        raise LabError(
            "hardlink scenario device topology mismatch: "
            f"mount_types={json.dumps(mount_types, sort_keys=True)} "
            f"device_count={len(devices)} devices_differ={str(devices_differ).lower()} "
            f"expected_cross_device={str(cross_device).lower()}"
        )
    probe = run(
        [
            "docker", "exec", "--user", "911:1001", container_id, "sh", "-c",
            "set -eu; s=/data/downloads/.arr-lab-link-source; d=/data/media/movies/.arr-lab-link-target; "
            "trap 'rm -f \"$s\" \"$d\"' EXIT; rm -f \"$s\" \"$d\"; printf x >\"$s\"; "
            "if ln \"$s\" \"$d\" 2>/dev/null; then printf linked; else printf blocked; fi",
        ],
        check=False,
    )
    expected = "blocked" if cross_device else "linked"
    outcome = probe.stdout.strip()
    if probe.returncode != 0 or outcome != expected:
        safe_outcome = outcome if outcome in {"blocked", "linked", ""} else "unexpected"
        raise LabError(
            "hardlink operation mismatch: "
            f"returncode={probe.returncode} outcome={safe_outcome or 'empty'} expected={expected}"
        )


def prowlarr_connectivity_command() -> list[str]:
    return compose_command(
        "--profile", "isolation", "run", "--rm", "--build", "--no-deps",
        "--entrypoint", "python3", "lab-controller", "-c",
        "import urllib.request; urllib.request.urlopen('http://prowlarr:9696/ping',timeout=3).read()",
    )


def verify_real_service_inspection(project: str, services: dict[str, str]) -> dict[str, object]:
    expected_real = {"sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin"}
    if set(services) != expected_real | {"mock-indexer"}:
        raise LabError("bootstrap started an unexpected service set")
    matrix = json.loads((REPO_ROOT / "lab" / "security-matrix.json").read_text(encoding="utf-8"))
    matrix_services = {item["service"]: item for item in matrix["services"]}
    expected_writable = {
        service: {mount["target"] for mount in matrix_services[service]["writable_mounts"]}
        for service in expected_real
    }
    process_name = {
        "sonarr": "Sonarr",
        "radarr": "Radarr",
        "prowlarr": "Prowlarr",
        "qbittorrent": "qbittorrent-nox",
        "jellyfin": "jellyfin",
    }
    for service in sorted(expected_real):
        container_id = services[service]
        container = json.loads(run(["docker", "inspect", container_id]).stdout)[0]
        host_config = container["HostConfig"]
        if not host_config.get("ReadonlyRootfs") or "ALL" not in (host_config.get("CapDrop") or []):
            raise LabError(f"{service} runtime hardening is invalid")
        if host_config.get("Privileged") or "no-new-privileges:true" not in (host_config.get("SecurityOpt") or []):
            raise LabError(f"{service} privilege boundary is invalid")
        if any(value for value in (container["NetworkSettings"].get("Ports") or {}).values()):
            raise LabError(f"{service} published a host port")
        if set(container["NetworkSettings"]["Networks"]) != {f"{project}_private"}:
            raise LabError(f"{service} is attached to an unexpected network")
        mounts = container.get("Mounts", [])
        if any("docker.sock" in json.dumps(mount) for mount in mounts):
            raise LabError(f"{service} received Docker authority")
        writable = {mount["Destination"] for mount in mounts if mount.get("RW")}
        if writable != expected_writable[service]:
            raise LabError(f"{service} writable mounts do not match the security matrix")
        health = container.get("State", {}).get("Health", {}).get("Status")
        if health != "healthy":
            raise LabError(f"{service} is not healthy")
        expected_uid = matrix_services[service]["long_running_uid"]
        expected_name = process_name[service]
        process_rows = run(["docker", "top", container_id, "-eo", "pid,uid,gid,args"]).stdout.splitlines()[1:]
        if not any(
            len(row.split(None, 3)) == 4
            and row.split(None, 3)[1] == str(expected_uid)
            and expected_name in row
            for row in process_rows
        ):
            raise LabError(f"{service} long-running UID does not match the security matrix")
    verify_double_container(project, services["mock-indexer"], "mock-indexer-token")
    return {"real_services": sorted(expected_real), "runtime_matrix_verified": True, "published_ports": 0}


def credential_variants(bundle, mock_value: str) -> dict[str, str]:
    raw_values = {"mock-indexer.value": mock_value}
    for service in ("sonarr", "radarr", "prowlarr"):
        raw_values[f"{service}.api-key"] = bundle.value(service, "api_key")
    for service in ("qbittorrent", "jellyfin"):
        raw_values[f"{service}.credential"] = bundle.value(service, "password")
    raw_values["qbittorrent.api-key"] = bundle.value("qbittorrent", "api_key")
    variants: dict[str, str] = {}
    for label, value in raw_values.items():
        for encoding, variant in (
            ("raw", value),
            ("base64", base64.b64encode(value.encode()).decode()),
            ("json", json.dumps(value)),
            ("url", urllib.parse.quote(value, safe="")),
            ("form", urllib.parse.quote_plus(value)),
        ):
            if variant:
                variants.setdefault(variant, f"{label}:{encoding}")
    return variants


def assert_no_secret_exposure(project: str, controller_runs: list[subprocess.CompletedProcess[str]], variants: dict[str, str], env: dict[str, str]) -> None:
    surfaces = [(f"controller:{index}", result.stdout + result.stderr) for index, result in enumerate(controller_runs, 1)]
    surfaces.append(("compose-render", run(compose_command("--profile", "services", "--profile", "doubles", "config", "--format", "json"), env=env).stdout))
    container_ids = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
    if container_ids:
        surfaces.append(("docker-inspect", run(["docker", "inspect", *container_ids]).stdout))
        for container_id in container_ids:
            container = json.loads(run(["docker", "inspect", container_id]).stdout)[0]
            service = container.get("Config", {}).get("Labels", {}).get("com.docker.compose.service", "unknown")
            logs = run(["docker", "logs", container_id], check=False)
            surfaces.append((f"logs:{service}", logs.stdout + logs.stderr))
    for surface_name, surface in surfaces:
        for variant, label in variants.items():
            if variant in surface:
                raise LabError(f"generated credential exposure at {surface_name} ({label})")


def last_json_object(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    raise LabError("controller output did not contain a JSON object")


def test_bootstrap() -> int:
    lab_id, project, root = create_runtime()
    env = lab_env(lab_id, project, root)
    completed = False
    result: dict[str, object]
    try:
        _layout, bundle, mock_path, mock_value = prepare_bootstrap_runtime(root)
        run(
            compose_command(
                "--profile",
                "services",
                "--profile",
                "doubles",
                "up",
                "-d",
                "--build",
                "--wait",
                "sonarr",
                "radarr",
                "prowlarr",
                "qbittorrent",
                "jellyfin",
                "mock-indexer",
            ),
            env=env,
        )
        service_rows = run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Label \"com.docker.compose.service\"}}={{.ID}}",
            ]
        ).stdout.splitlines()
        services = dict(row.split("=", 1) for row in service_rows if "=" in row)
        inspection = verify_real_service_inspection(project, services)
        command = controller_bootstrap_command(bundle, mock_path)
        first_run = run(command, env=env, check=False)
        if first_run.returncode != 0:
            failure = last_json_object(first_run.stdout)
            raise LabError(f"first controller bootstrap failed: {failure.get('detail', 'unknown controller error')}")
        first = last_json_object(first_run.stdout)
        second_run = run(command, env=env, check=False)
        if second_run.returncode != 0:
            failure = last_json_object(second_run.stdout)
            raise LabError(f"second controller bootstrap failed: {failure.get('detail', 'unknown controller error')}")
        second = last_json_object(second_run.stdout)
        if first != second or first.get("ok") is not True:
            raise LabError("second bootstrap produced normalized configuration drift")
        assert_no_secret_exposure(project, [first_run, second_run], credential_variants(bundle, mock_value), env)
        result = {
            "schema": "arr-orchestrator.lab-bootstrap-run.v1",
            "lab_id": lab_id,
            "ok": True,
            "states": first["states"],
            "baseline_digests": first["baseline_digests"],
            "inspection": inspection,
            "bootstrap_runs": 2,
            "normalized_drift": False,
            "synthetic_credentials": 6,
        }
        completed = True
    finally:
        down = run(compose_down_command("services", "doubles", "isolation"), env=env, check=False)
        remaining_containers = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
        remaining_networks = run(["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
        remaining_images = remaining_project_images(project)
        if down.returncode != 0 or remaining_containers or remaining_networks or remaining_images:
            finalize_runtime(root, lab_id, project, success=False)
            raise LabError("bounded bootstrap cleanup did not converge")
        finalize_runtime(root, lab_id, project, success=completed)
    return emit(result)


def test_scenarios() -> int:
    from lab.controller.scenarios import apply_runner_config, load_registry

    lab_id, project, root = create_runtime()
    env = lab_env(lab_id, project, root)
    completed = False
    controller_runs: list[subprocess.CompletedProcess[str]] = []
    result: dict[str, object]
    try:
        _layout, bundle, mock_path, mock_value = prepare_bootstrap_runtime(root)
        run(
            compose_command(
                "--profile", "services", "--profile", "doubles", "up", "-d", "--build", "--wait",
                "sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin", "mock-indexer",
            ),
            env=env,
        )
        baseline_run = run(controller_bootstrap_command(bundle, mock_path), env=env, check=False)
        controller_runs.append(baseline_run)
        baseline = last_json_object(baseline_run.stdout)
        if baseline_run.returncode != 0 or baseline.get("ok") is not True:
            raise LabError("scenario baseline bootstrap failed")

        api_results: dict[str, object] = {}
        for name in (
            "category-mismatch", "root-folder-mismatch", "application-sync-mismatch", "path-mapping-mismatch"
        ):
            command = controller_scenario_command(bundle, name)
            first_run = run(command, env=env, check=False)
            second_run = run(command, env=env, check=False)
            controller_runs.extend((first_run, second_run))
            if first_run.returncode != 0 or second_run.returncode != 0:
                raise LabError(f"controller scenario failed: {name}")
            first = last_json_object(first_run.stdout)
            second = last_json_object(second_run.stdout)
            if first != second or first.get("scenario") != name or first.get("ok") is not True:
                raise LabError(f"controller scenario is not idempotent: {name}")
            healthy_run = run(controller_scenario_command(bundle, "healthy"), env=env, check=False)
            controller_runs.append(healthy_run)
            if healthy_run.returncode != 0 or last_json_object(healthy_run.stdout).get("scenario") != "healthy":
                raise LabError(f"controller scenario healthy restore failed: {name}")
            verify_run = run(controller_bootstrap_command(bundle, mock_path), env=env, check=False)
            controller_runs.append(verify_run)
            if verify_run.returncode != 0 or last_json_object(verify_run.stdout) != baseline:
                raise LabError(f"controller scenario baseline drift after restore: {name}")
            api_results[name] = first

        runner_results: dict[str, str] = {}
        for name in ("unsupported-api-version", "stale-plan", "destructive-denial"):
            first_path = apply_runner_config(root, name)
            first_bytes = first_path.read_bytes()
            second_path = apply_runner_config(root, name)
            if first_path != second_path or first_bytes != second_path.read_bytes():
                raise LabError(f"runner scenario is not idempotent: {name}")
            runner_results[name] = "idempotent"
            apply_runner_config(root, "healthy")
            if first_path.exists():
                raise LabError(f"runner scenario healthy restore failed: {name}")

        hardlink_container = converge_radarr_hardlink_topology(
            project, lab_id, root, env, cross_device=True
        )
        if converge_radarr_hardlink_topology(
            project, lab_id, root, env, cross_device=True
        ) != hardlink_container:
            raise LabError("second hardlink scenario apply recreated Radarr")
        healthy_radarr = converge_radarr_hardlink_topology(
            project, lab_id, root, env, cross_device=False
        )
        if converge_radarr_hardlink_topology(
            project, lab_id, root, env, cross_device=False
        ) != healthy_radarr:
            raise LabError("second hardlink healthy restore recreated Radarr")
        hardlink_restored = run(controller_bootstrap_command(bundle, mock_path), env=env, check=False)
        controller_runs.append(hardlink_restored)
        if hardlink_restored.returncode != 0 or last_json_object(hardlink_restored.stdout) != baseline:
            raise LabError("hardlink healthy restore drifted from baseline")

        unavailable_override = REPO_ROOT / "lab" / "scenarios" / "service-unavailable.compose.yaml"
        unavailable_prefix = scenario_compose_command(unavailable_override, "--profile", "services")
        run_authorized_compose_mutation(
            project, lab_id, root, {"prowlarr"},
            [*unavailable_prefix, "up", "-d", "--no-deps", "prowlarr"], env,
        )
        run_authorized_compose_mutation(
            project, lab_id, root, {"prowlarr"}, [*unavailable_prefix, "stop", "prowlarr"], env,
        )
        run_authorized_compose_mutation(
            project, lab_id, root, {"prowlarr"}, [*unavailable_prefix, "stop", "prowlarr"], env,
        )
        container_id = scenario_service_container(project, "prowlarr")
        if json.loads(run(["docker", "inspect", container_id]).stdout)[0]["State"]["Running"]:
            raise LabError("service unavailable scenario did not stop the exact service")
        unreachable = run_authorized_compose_mutation(
            project, lab_id, root, {"prowlarr"}, prowlarr_connectivity_command(), env, check=False,
        )
        if unreachable.returncode == 0:
            raise LabError("service unavailable scenario remained internally reachable")
        run_authorized_compose_mutation(
            project, lab_id, root, {"prowlarr"},
            compose_command("--profile", "services", "up", "-d", "--wait", "--no-deps", "prowlarr"), env,
        )
        reachable = run_authorized_compose_mutation(
            project, lab_id, root, {"prowlarr"}, prowlarr_connectivity_command(), env,
        )
        if reachable.returncode != 0:
            raise LabError("service unavailable healthy restore remained unreachable")
        restored = run(controller_bootstrap_command(bundle, mock_path), env=env, check=False)
        controller_runs.append(restored)
        if restored.returncode != 0 or last_json_object(restored.stdout) != baseline:
            raise LabError("service unavailable healthy restore drifted from baseline")

        declared = {item["name"] for item in load_registry()}
        expected = {
            "healthy", "category-mismatch", "root-folder-mismatch", "application-sync-mismatch",
            "path-mapping-mismatch", "hardlink-cross-device", "service-unavailable",
            "unsupported-api-version", "stale-plan", "destructive-denial",
        }
        if declared != expected:
            raise LabError("live scenario registry drifted from the canonical allowlist")
        assert_no_secret_exposure(project, controller_runs, credential_variants(bundle, mock_value), env)
        result = {
            "schema": "arr-orchestrator.lab-scenario-run.v1",
            "lab_id": lab_id,
            "ok": True,
            "scenarios": sorted(declared - {"healthy"}),
            "controller_api": sorted(api_results),
            "runner_config": runner_results,
            "host_compose": {"hardlink_cross_device": True, "service_unavailable": "restored"},
            "healthy_restores": 9,
            "idempotent_applies": 9,
        }
        completed = True
    finally:
        down = run(compose_down_command("services", "doubles", "isolation", "runner"), env=env, check=False)
        remaining_containers = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
        remaining_networks = run(["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
        remaining_images = remaining_project_images(project)
        if down.returncode != 0 or remaining_containers or remaining_networks or remaining_images:
            finalize_runtime(root, lab_id, project, success=False)
            raise LabError("bounded scenario cleanup did not converge")
        finalize_runtime(root, lab_id, project, success=completed)
    return emit(result)


def render() -> int:
    lab_id = "lab-render"
    project = f"arr-orchestrator-{lab_id}"
    root = runtime_base() / lab_id
    env = lab_env(lab_id, project, root)
    result = run(
        compose_command(
            "--profile",
            "services",
            "--profile",
            "isolation",
            "--profile",
            "runner",
            "--profile",
            "doubles",
            "config",
            "--format",
            "json",
        ),
        env=env,
    )
    print(result.stdout, end="")
    return 0


def render_scenario_override(name: str, runtime_root: Path) -> dict[str, object]:
    from lab.controller.scenarios import scenario_by_name

    item = scenario_by_name(name)
    if item["driver"] != "host-compose":
        raise LabError("scenario does not use host-compose authority")
    override = REPO_ROOT / item["action"]["override"]
    if not override.is_file() or override.parent != REPO_ROOT / "lab" / "scenarios":
        raise LabError("scenario Compose override is outside the committed allowlist")
    lab_id = "lab-scenario-contract"
    project = f"arr-orchestrator-{lab_id}"
    env = lab_env(lab_id, project, runtime_root)
    command_builder = (
        scenario_standalone_command if name == "hardlink-cross-device" else scenario_compose_command
    )
    rendered = json.loads(
        run(
            command_builder(
                override, "--profile", "services", "--profile", "runner",
                "config", "--format", "json",
            ),
            env=env,
        ).stdout
    )
    services = rendered.get("services", {})
    encoded = json.dumps(services, sort_keys=True)
    published_ports = sum(
        len(service.get("ports") or []) for service in services.values() if isinstance(service, dict)
    )
    private = rendered.get("networks", {}).get("private", {})
    mount_types = {
        service_name: {
            mount.get("target"): mount.get("type")
            for mount in service.get("volumes", [])
            if isinstance(mount, dict) and isinstance(mount.get("target"), str)
        }
        for service_name, service in services.items()
        if isinstance(service, dict)
    }
    scenario_services = sorted(
        service_name
        for service_name, service in services.items()
        if isinstance(service, dict)
        and (service.get("labels") or {}).get("com.viggomeesters.arr-orchestrator.scenario") == name
    )
    return {
        "scenario": name,
        "published_ports": published_ports,
        "internal_network": private.get("internal") is True,
        "docker_socket": "docker.sock" in encoded,
        "mount_types": mount_types,
        "scenario_services": scenario_services,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("render")
    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("suite", choices=("isolation", "doubles", "bootstrap", "scenarios"))
    subparsers.add_parser("container-health-server")
    subparsers.add_parser("container-idle")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "render":
            return render()
        if args.command == "test":
            if args.suite == "doubles":
                return test_doubles()
            if args.suite == "bootstrap":
                return test_bootstrap()
            if args.suite == "scenarios":
                return test_scenarios()
            return test_isolation()
        if args.command == "container-health-server":
            return container_health_server()
        if args.command == "container-idle":
            return container_idle()
    except (LabError, subprocess.CalledProcessError, json.JSONDecodeError, OSError) as error:
        payload = {
            "schema": "arr-orchestrator.lab-error.v1",
            "ok": False,
            "error": type(error).__name__,
        }
        if isinstance(error, LabError):
            payload["detail"] = str(error)
            if error.stage:
                payload["stage"] = error.stage
        return emit(payload, exit_code=1)
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
