import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker
from scripts import lab as lab_script


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "lab"
SCHEMAS = ROOT / "schemas"
FOUNDATION_SERVICES = {
    "sonarr",
    "radarr",
    "prowlarr",
    "qbittorrent",
    "jellyfin",
    "lab-controller",
    "arrctl-runner",
}
APP_SERVICES = {"sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin"}
BASE_REF_BY_ROLE = {
    "lab-controller": "controller-base",
    "arrctl-runner": "controller-base",
}
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(schema_name, instance):
    schema = load_json(SCHEMAS / schema_name)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return sorted(validator.iter_errors(instance), key=lambda error: list(error.path))


class ComposeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.runtime_root = Path(cls.tempdir.name) / "lab-a1b2c3"
        cls.runtime_root.mkdir(mode=0o700)
        env = os.environ.copy()
        env.update(
            {
                "COMPOSE_PROJECT_NAME": "arr-orchestrator-lab-a1b2c3",
                "ARR_LAB_ROOT": str(cls.runtime_root),
            }
        )
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(LAB / "compose.yaml"),
                "--profile",
                "services",
                "--profile",
                "isolation",
                "--profile",
                "runner",
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
        cls.rendered = json.loads(result.stdout)
        cls.image_lock = load_json(LAB / "images.lock.json")
        cls.readiness = load_json(LAB / "readiness.json")
        cls.security = load_json(LAB / "security-matrix.json")

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_foundation_has_exact_service_boundaries(self):
        self.assertEqual(FOUNDATION_SERVICES, set(self.rendered["services"]))
        for service in APP_SERVICES:
            self.assertIn("services", self.rendered["services"][service]["profiles"])
        self.assertIn("isolation", self.rendered["services"]["lab-controller"]["profiles"])
        self.assertIn("runner", self.rendered["services"]["arrctl-runner"]["profiles"])

    def test_network_is_internal_isolated_and_project_scoped(self):
        self.assertEqual({"private"}, set(self.rendered["networks"]))
        network = self.rendered["networks"]["private"]
        self.assertTrue(network["internal"])
        self.assertEqual("bridge", network["driver"])
        self.assertEqual(
            "isolated",
            network["driver_opts"]["com.docker.network.bridge.gateway_mode_ipv4"],
        )
        self.assertEqual("arr-orchestrator-lab-a1b2c3_private", network["name"])
        for service in self.rendered["services"].values():
            self.assertEqual({"private"}, set(service["networks"]))
            self.assertFalse(service.get("ports"))
            self.assertFalse(service.get("expose"))
            self.assertNotIn("network_mode", service)

    def test_images_and_dockerfile_base_are_digest_pinned(self):
        by_service = {entry["service"]: entry for entry in self.image_lock["images"]}
        for service in APP_SERVICES:
            image = self.rendered["services"][service]["image"]
            self.assertRegex(image, DIGEST_RE)
            entry = by_service[service]
            expected = f"{entry['registry']}/{entry['repository']}:{entry['tag']}@{entry['manifest_digest']}"
            self.assertEqual(expected, image)
        dockerfile = (LAB / "Dockerfile.controller").read_text(encoding="utf-8")
        controller = by_service["controller-base"]
        expected_from = (
            f"FROM {controller['registry']}/{controller['repository']}:{controller['tag']}"
            f"@{controller['manifest_digest']}"
        )
        self.assertIn(expected_from, dockerfile)
        for role in ("lab-controller", "arrctl-runner"):
            self.assertIn("build", self.rendered["services"][role])
            self.assertNotIn("image", self.rendered["services"][role])

    def test_no_service_has_docker_authority_or_host_escape(self):
        for name, service in self.rendered["services"].items():
            self.assertFalse(service.get("privileged", False), name)
            self.assertEqual(["ALL"], service["cap_drop"], name)
            self.assertIn("no-new-privileges:true", service["security_opt"], name)
            for volume in service.get("volumes", []):
                source = volume.get("source", "") if isinstance(volume, dict) else str(volume).split(":", 1)[0]
                target = volume.get("target", "") if isinstance(volume, dict) else str(volume)
                self.assertNotIn("docker.sock", source)
                self.assertNotIn("docker.sock", target)
                if source.startswith("/"):
                    self.assertTrue(Path(source).is_relative_to(self.runtime_root), (name, source))
        for role in ("lab-controller", "arrctl-runner"):
            service = self.rendered["services"][role]
            self.assertEqual("65532:65532", service["user"])
            self.assertTrue(service["read_only"])
            self.assertEqual([], service.get("cap_add", []))

    def test_contract_instances_validate_and_references_resolve(self):
        self.assertEqual([], validate("lab-images-lock.schema.json", self.image_lock))
        self.assertEqual([], validate("lab-readiness.schema.json", self.readiness))
        self.assertEqual([], validate("lab-security-matrix.schema.json", self.security))
        locked = {f"lock:{entry['service']}" for entry in self.image_lock["images"]}
        readiness_refs = {item["image_ref"] for item in self.readiness["services"].values()}
        security_refs = {item["image_lock_ref"] for item in self.security["services"]}
        self.assertEqual(set(), readiness_refs - locked)
        self.assertEqual(set(), security_refs - locked)

    def test_compose_security_matches_the_declared_matrix(self):
        matrix = {item["service"]: item for item in self.security["services"]}
        for name in FOUNDATION_SERVICES:
            service = self.rendered["services"][name]
            declared = matrix[name]
            self.assertEqual(set(declared["cap_drop"]), set(service.get("cap_drop", [])), name)
            self.assertEqual(set(declared["cap_add"]), set(service.get("cap_add", [])), name)
            self.assertEqual(declared["read_only_rootfs"], service["read_only"], name)
            configured_user = service.get("user")
            startup_uid = int(configured_user.split(":", 1)[0]) if configured_user else 0
            self.assertEqual(declared["startup_uid"], startup_uid, name)
            writable_targets = {
                volume["target"]
                for volume in service.get("volumes", [])
                if isinstance(volume, dict) and not volume.get("read_only", False)
            }
            declared_targets = {mount["target"] for mount in declared["writable_mounts"]}
            self.assertEqual(declared_targets, writable_targets, name)
            self.assertEqual(declared["pids_limit"], service["pids_limit"], name)
            self.assertEqual(declared["memory_limit_mib"] * 1024 * 1024, int(service["mem_limit"]), name)
            self.assertEqual(declared["cpu_limit"], service["cpus"], name)

            native_health = service["healthcheck"]
            api_live = self.readiness["services"][name]["states"]["api_live"]
            self.assertEqual(f"{api_live['interval_seconds']}s", native_health["interval"], name)
            self.assertEqual(f"{api_live['start_period_seconds']}s", native_health["start_period"], name)
            self.assertEqual(api_live["max_attempts"], native_health["retries"], name)

    def test_build_context_excludes_private_and_generated_state(self):
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [
                "*",
                "!src/",
                "!src/**",
                "**/__pycache__/",
                "**/__pycache__/**",
                "**/*.py[cod]",
                "!scripts/",
                "scripts/*",
                "!scripts/lab.py",
                "!lab/",
                "lab/*",
                "!lab/Dockerfile.controller",
                "!lab/__init__.py",
                "!lab/host/",
                "!lab/host/**",
                "!lab/controller/",
                "!lab/controller/**",
                "lab/**/__pycache__/",
                "lab/**/__pycache__/**",
                "lab/**/*.py[cod]",
            ],
            dockerignore,
        )

    def test_runtime_base_rejects_symlinked_data_home_or_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            data_home_link = root / "data-home-link"
            data_home_link.symlink_to(outside, target_is_directory=True)
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home_link)}):
                with self.assertRaises(lab_script.LabError):
                    lab_script.runtime_base()

            data_home = root / "data-home"
            data_home.mkdir()
            (data_home / "arr-orchestrator").symlink_to(outside, target_is_directory=True)
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
                with self.assertRaises(lab_script.LabError):
                    lab_script.runtime_base()

    def test_runtime_finalization_preserves_failure_and_removes_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_DATA_HOME": directory}):
                failed_id, failed_project, failed_root = lab_script.create_runtime()
                lab_script.finalize_runtime(failed_root, failed_id, failed_project, success=False)
                self.assertTrue((failed_root / lab_script.MARKER).is_file())
                self.assertEqual(
                    {"lab_id": failed_id, "status": "quarantined"},
                    json.loads((failed_root / ".failed").read_text(encoding="utf-8")),
                )

                success_id, success_project, success_root = lab_script.create_runtime()
                lab_script.finalize_runtime(success_root, success_id, success_project, success=True)
                self.assertFalse(success_root.exists())


if __name__ == "__main__":
    unittest.main()
