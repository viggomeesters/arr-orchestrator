#!/usr/bin/env python3
"""Host-owned lifecycle and isolation checks for the disposable local lab."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "lab" / "compose.yaml"
MARKER = ".arr-orchestrator-lab"
LABEL = "com.viggomeesters.arr-orchestrator.lab=true"
SECRET_OWNER_IMAGE = "docker.io/library/python:3.13.14-slim-bookworm@sha256:de572b33eae61a53675a87bbd02b5e365df7b6b2b06c9276124e965cec08c452"


class LabError(RuntimeError):
    pass


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
    for path in root.rglob("*"):
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


def compose_down_command(profile: str) -> list[str]:
    return compose_command(
        "--profile",
        profile,
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


def write_secret(root: Path, name: str) -> Path:
    secret_root = root / "secrets"
    secret_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(secret_root, 0o700)
    secret_file = secret_root / name
    secret_file.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    os.chmod(secret_file, 0o600)
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
            f"type=bind,source={secret_root},target=/secrets",
            "--entrypoint",
            "python3",
            SECRET_OWNER_IMAGE,
            "-c",
            "import os,sys; os.chown('/secrets/'+sys.argv[1],65532,65532); os.chmod('/secrets/'+sys.argv[1],0o600)",
            name,
        ]
    )
    info = secret_file.stat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (65532, 65532, 0o600):
        raise LabError("secret ownership or mode is invalid")
    return secret_file


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("render")
    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("suite", choices=("isolation", "doubles"))
    subparsers.add_parser("container-health-server")
    subparsers.add_parser("container-idle")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "render":
            return render()
        if args.command == "test" and args.suite == "isolation":
            return test_isolation()
        if args.command == "test" and args.suite == "doubles":
            return test_doubles()
        if args.command == "container-health-server":
            return container_health_server()
        if args.command == "container-idle":
            return container_idle()
    except (LabError, subprocess.CalledProcessError, json.JSONDecodeError, OSError) as error:
        return emit(
            {
                "schema": "arr-orchestrator.lab-error.v1",
                "ok": False,
                "error": type(error).__name__,
            },
            exit_code=1,
        )
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
