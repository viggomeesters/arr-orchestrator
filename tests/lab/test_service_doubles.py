import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import lab as lab_script


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "lab" / "compose.yaml"
MOCK_SOURCE = ROOT / "lab" / "services" / "mock-indexer" / "server.py"
FAULT_SOURCE = ROOT / "lab" / "services" / "fault-api" / "server.py"


def load_module(name: str, path: Path):
    if not path.is_file():
        raise AssertionError(f"missing service source: {path.relative_to(ROOT)}")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ServiceDoubleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env.update(
                {
                    "ARR_LAB_ID": "lab-double-contract",
                    "ARR_LAB_ROOT": directory,
                    "COMPOSE_PROJECT_NAME": "arr-orchestrator-lab-double-contract",
                }
            )
            rendered = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE),
                    "--profile",
                    "doubles",
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        cls.compose = json.loads(rendered.stdout)

    def test_repository_owned_sources_and_dockerfiles_exist(self):
        for relative in (
            "lab/services/mock-indexer/.dockerignore",
            "lab/services/mock-indexer/Dockerfile",
            "lab/services/mock-indexer/server.py",
            "lab/services/fault-api/.dockerignore",
            "lab/services/fault-api/Dockerfile",
            "lab/services/fault-api/server.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_service_build_contexts_are_exact_and_offline(self):
        expected_base = (
            "FROM docker.io/library/python:3.13.14-slim-bookworm@sha256:"
            "de572b33eae61a53675a87bbd02b5e365df7b6b2b06c9276124e965cec08c452"
        )
        for directory in (MOCK_SOURCE.parent, FAULT_SOURCE.parent):
            dockerignore = (directory / ".dockerignore").read_text(encoding="utf-8").splitlines()
            self.assertEqual(["*", "!Dockerfile", "!server.py"], dockerignore)
            dockerfile = (directory / "Dockerfile").read_text(encoding="utf-8")
            self.assertEqual(expected_base, dockerfile.splitlines()[0])
            self.assertIn("USER 65532:65532", dockerfile)
            self.assertNotIn("apt-get", dockerfile)
            self.assertNotIn("pip install", dockerfile)

    def test_compose_declares_only_bounded_private_doubles(self):
        for name in ("mock-indexer", "fault-api"):
            service = self.compose["services"][name]
            self.assertEqual(["doubles"], service["profiles"])
            self.assertEqual({"private"}, set(service["networks"]))
            self.assertEqual("65532:65532", service["user"])
            self.assertFalse(service.get("privileged", False))
            self.assertEqual(["ALL"], service["cap_drop"])
            self.assertEqual([], service.get("cap_add", []))
            self.assertTrue(service["read_only"])
            self.assertIn("no-new-privileges:true", service["security_opt"])
            self.assertNotIn("ports", service)
            self.assertNotIn("expose", service)
            self.assertNotIn("network_mode", service)
            self.assertNotIn("docker.sock", json.dumps(service))
            secret_mounts = [mount for mount in service["volumes"] if mount["target"].startswith("/run/secrets/")]
            self.assertEqual(1, len(secret_mounts), name)
            self.assertTrue(secret_mounts[0]["read_only"], name)

    def test_mock_indexer_responses_are_minimal_and_deterministic(self):
        mock = load_module("mock_indexer_double", MOCK_SOURCE)
        self.assertEqual(
            (401, "application/json", b'{"error":"unauthorized"}'),
            mock.response_for("/api/v1/search", None, "synthetic-token"),
        )
        expected = b'{"items":[{"id":"synthetic-1","title":"Synthetic Result"}],"total":1}'
        first = mock.response_for("/api/v1/search", "synthetic-token", "synthetic-token")
        second = mock.response_for("/api/v1/search", "synthetic-token", "synthetic-token")
        self.assertEqual((200, "application/json", expected), first)
        self.assertEqual(first, second)

    def test_fault_scenarios_are_exact_and_resettable(self):
        fault = load_module("fault_api_double", FAULT_SOURCE)
        expected = {
            "healthy": (0.0, 200, "application/json", b'{"api_version":"1","generation":1,"status":"ok"}'),
            "timeout": (1.5, 200, "application/json", b'{"status":"delayed"}'),
            "unavailable": (0.0, 503, "application/json", b'{"error":"service_unavailable"}'),
            "malformed-json": (0.0, 200, "application/json", b'{"broken":'),
            "unsupported-version": (0.0, 200, "application/json", b'{"api_version":"999","status":"unsupported"}'),
            "stale-readback": (0.0, 200, "application/json", b'{"generation":1,"observed_generation":0,"status":"stale"}'),
        }
        self.assertEqual(expected, {name: fault.response_for(name) for name in expected})
        state = fault.ScenarioState()
        state.set("stale-readback")
        self.assertEqual("stale-readback", state.get())
        state.reset()
        self.assertEqual("healthy", state.get())
        with self.assertRaises(ValueError):
            state.set("unknown")

    def test_service_sources_do_not_log_request_material(self):
        for path in (MOCK_SOURCE, FAULT_SOURCE):
            source = path.read_text(encoding="utf-8")
            self.assertIn("def log_message", source)
            self.assertNotIn("print(", source)
            self.assertNotIn("Cookie", source)
            self.assertNotIn("query=", source)

    def test_lab_cli_exposes_doubles_integration_suite(self):
        parsed = lab_script.build_parser().parse_args(["test", "doubles"])
        self.assertEqual("doubles", parsed.suite)

    def test_successful_probe_cleanup_removes_project_images(self):
        for profile in ("isolation", "doubles"):
            command = lab_script.compose_down_command(profile)
            self.assertIn("--volumes", command)
            self.assertEqual(["--rmi", "local"], command[-2:])

    def test_early_doubles_failure_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"XDG_DATA_HOME": directory}
        ), patch.object(
            lab_script, "write_secret", side_effect=lab_script.LabError("synthetic failure")
        ), patch.object(
            lab_script,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            with self.assertRaises(lab_script.LabError):
                lab_script.test_doubles()
            roots = list(lab_script.runtime_base().glob("lab-*"))
            self.assertEqual(1, len(roots))
            self.assertTrue((roots[0] / lab_script.MARKER).is_file())
            self.assertTrue((roots[0] / ".failed").is_file())


if __name__ == "__main__":
    unittest.main()
