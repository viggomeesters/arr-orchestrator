import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas"


def load_schema(name):
    path = SCHEMA_DIR / name
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def errors(schema, instance):
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return list(validator.iter_errors(instance))


SERVICES = [
    "sonarr",
    "radarr",
    "prowlarr",
    "qbittorrent",
    "jellyfin",
    "lab-controller",
    "arrctl-runner",
    "mock-indexer",
    "fault-api",
]

IMAGE_LOCK_REFS = {
    "lab-controller": "lock:controller-base",
    "arrctl-runner": "lock:controller-base",
    "mock-indexer": "lock:mock-indexer-base",
    "fault-api": "lock:fault-api-base",
}


class LabContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest_schema = load_schema("lab-manifest.schema.json")
        cls.images_schema = load_schema("lab-images-lock.schema.json")
        cls.readiness_schema = load_schema("lab-readiness.schema.json")
        cls.security_schema = load_schema("lab-security-matrix.schema.json")

    def manifest(self):
        return {
            "contract_version": "1.0.0",
            "artifact_kind": "lab_manifest",
            "lab_id": "lab-a1b2c3",
            "compose_project": "arr-orchestrator-lab-a1b2c3",
            "runtime": {
                "root_ref": "xdg-data:arr-orchestrator/lab/lab-a1b2c3",
                "marker_file": ".arr-orchestrator-lab",
                "root_mode": "0700",
                "secret_directory_mode": "0700",
                "allowed_secret_file_modes": ["0400", "0600"],
            },
            "network": {
                "name_template": "${COMPOSE_PROJECT_NAME}_private",
                "internal": True,
                "gateway_mode_ipv4": "isolated",
                "application_network_count": 1,
                "published_ports": [],
            },
            "services": SERVICES,
            "reset": {
                "direct_child_only": True,
                "marker_required": True,
                "lab_id_must_match": True,
                "compose_project_must_match": True,
                "resource_label": "com.viggomeesters.arr-orchestrator.lab=true",
                "no_follow": True,
                "global_prune_allowed": False,
            },
            "scenarios": [
                "healthy",
                "category-mismatch",
                "root-folder-mismatch",
                "application-sync-mismatch",
                "path-mapping-mismatch",
                "hardlink-cross-device",
                "service-unavailable",
                "unsupported-api-version",
                "stale-plan",
                "destructive-denial",
            ],
        }

    def image_lock(self):
        image_services = [
            "sonarr",
            "radarr",
            "prowlarr",
            "qbittorrent",
            "jellyfin",
            "controller-base",
            "mock-indexer-base",
            "fault-api-base",
        ]
        return {
            "contract_version": "1.0.0",
            "artifact_kind": "lab_images_lock",
            "platform": "linux/amd64",
            "images": [
                {
                    "service": service,
                    "registry": "docker.io",
                    "repository": f"synthetic/{service}",
                    "tag": "1.2.3",
                    "index_digest": "sha256:" + "1" * 64,
                    "manifest_digest": "sha256:" + "2" * 64,
                    "config_digest": "sha256:" + "3" * 64,
                    "application_version": "1.2.3",
                    "provenance_url": "https://example.invalid/source",
                    "license_url": "https://example.invalid/license",
                    "verified_at": "2026-08-17T00:00:00Z",
                    "attestation": {"status": "unavailable", "limitation": "Synthetic contract fixture."},
                }
                for service in image_services
            ],
        }

    def readiness(self):
        def states():
            return {
                "process_alive": {
                    "owner": "docker",
                    "probe_location": "in_container",
                    "probe": {"kind": "process", "target": "main"},
                    "start_period_seconds": 1,
                    "interval_seconds": 1,
                    "max_attempts": 5,
                    "hard_deadline_seconds": 10,
                },
                "api_live": {
                    "owner": "docker",
                    "probe_location": "in_container",
                    "probe": {"kind": "http", "method": "GET", "path": "/ping", "expected_status": [200]},
                    "start_period_seconds": 1,
                    "interval_seconds": 1,
                    "max_attempts": 5,
                    "hard_deadline_seconds": 10,
                },
                "api_ready": {
                    "owner": "controller",
                    "probe_location": "lab_controller",
                    "credential_ref": "file:/run/secrets/service-api-key",
                    "probe": {"kind": "http", "method": "GET", "path": "/api/status", "expected_status": [200]},
                    "expected_schema_ref": "schema:service-status",
                    "start_period_seconds": 1,
                    "interval_seconds": 1,
                    "max_attempts": 10,
                    "hard_deadline_seconds": 20,
                },
                "baseline_verified": {
                    "owner": "controller",
                    "probe_location": "lab_controller",
                    "credential_ref": "file:/run/secrets/service-api-key",
                    "probe": {"kind": "http", "method": "GET", "path": "/api/baseline", "expected_status": [200]},
                    "expected_schema_ref": "schema:baseline",
                    "expected_baseline_digest": "sha256:" + "4" * 64,
                    "start_period_seconds": 1,
                    "interval_seconds": 1,
                    "max_attempts": 10,
                    "hard_deadline_seconds": 20,
                },
            }

        return {
            "contract_version": "1.0.0",
            "artifact_kind": "lab_readiness",
            "services": {
                service: {"image_ref": IMAGE_LOCK_REFS.get(service, f"lock:{service}"), "states": states()}
                for service in SERVICES
            },
        }

    def security_matrix(self):
        return {
            "contract_version": "1.0.0",
            "artifact_kind": "lab_security_matrix",
            "platform": "linux/amd64",
            "services": [
                {
                    "service": service,
                    "image_lock_ref": IMAGE_LOCK_REFS.get(service, f"lock:{service}"),
                    "startup_uid": 1000,
                    "long_running_uid": 1000,
                    "cap_drop": ["ALL"],
                    "cap_add": [],
                    "no_new_privileges": True,
                    "read_only_rootfs": True,
                    "writable_mounts": [],
                    "tmpfs_mounts": ["/tmp"],
                    "seccomp_mode": "runtime-default",
                    "pids_limit": 128,
                    "memory_limit_mib": 512,
                    "cpu_limit": 1.0,
                    "exception_rationale": None,
                }
                for service in SERVICES
            ],
        }

    def assert_rejected(self, schema, instance):
        self.assertTrue(errors(schema, instance), instance)

    def test_positive_contracts_validate(self):
        self.assertEqual([], errors(self.manifest_schema, self.manifest()))
        self.assertEqual([], errors(self.images_schema, self.image_lock()))
        self.assertEqual([], errors(self.readiness_schema, self.readiness()))
        self.assertEqual([], errors(self.security_schema, self.security_matrix()))

    def test_manifest_rejects_runtime_and_network_escape(self):
        for mutation in (
            lambda item: item["runtime"].update(root_ref="/mnt/c/private"),
            lambda item: item["runtime"].update(root_ref="xdg-data:arr-orchestrator/lab/../escape"),
            lambda item: item["network"].update(internal=False),
            lambda item: item["network"].update(gateway_mode_ipv4="nat"),
            lambda item: item["network"].update(application_network_count=2),
            lambda item: item["network"].update(published_ports=[8989]),
        ):
            item = self.manifest()
            mutation(item)
            self.assert_rejected(self.manifest_schema, item)

    def test_manifest_rejects_unsafe_reset_or_unknown_scenario(self):
        for mutation in (
            lambda item: item["reset"].update(marker_required=False),
            lambda item: item["reset"].update(global_prune_allowed=True),
            lambda item: item["reset"].update(no_follow=False),
            lambda item: item["scenarios"].append("arbitrary-shell"),
        ):
            item = self.manifest()
            mutation(item)
            self.assert_rejected(self.manifest_schema, item)

    def test_image_lock_rejects_floating_or_incomplete_images(self):
        for mutation in (
            lambda item: item["images"][0].update(tag="latest"),
            lambda item: item["images"][0].update(manifest_digest="sha256:bad"),
            lambda item: item["images"][0].pop("config_digest"),
            lambda item: item["images"].pop(),
        ):
            item = self.image_lock()
            mutation(item)
            self.assert_rejected(self.images_schema, item)

    def test_verified_image_attestation_requires_evidence_url(self):
        item = self.image_lock()
        item["images"][0]["attestation"] = {"status": "verified"}
        self.assert_rejected(self.images_schema, item)
        item["images"][0]["attestation"]["evidence_url"] = "https://example.invalid/attestation"
        self.assertEqual([], errors(self.images_schema, item))

    def test_readiness_and_security_image_refs_resolve_in_image_lock(self):
        locked = {f"lock:{image['service']}" for image in self.image_lock()["images"]}
        readiness_refs = {service["image_ref"] for service in self.readiness()["services"].values()}
        security_refs = {service["image_lock_ref"] for service in self.security_matrix()["services"]}
        self.assertEqual(set(), readiness_refs - locked)
        self.assertEqual(set(), security_refs - locked)

    def test_readiness_rejects_collapsed_or_secret_bearing_probes(self):
        for mutation in (
            lambda item: item["services"]["sonarr"]["states"].pop("api_ready"),
            lambda item: item["services"]["sonarr"]["states"]["api_ready"].update(owner="docker"),
            lambda item: item["services"]["sonarr"]["states"]["api_ready"].update(credential_ref="env:RAW_API_KEY"),
            lambda item: item["services"]["sonarr"]["states"]["api_live"]["probe"].update(path="http://192.168.1.10/ping"),
            lambda item: item["services"]["sonarr"]["states"]["api_live"].update(fixed_sleep_seconds=5),
        ):
            item = self.readiness()
            mutation(item)
            self.assert_rejected(self.readiness_schema, item)

    def test_security_matrix_requires_documented_exceptions(self):
        for mutation in (
            lambda item: item["services"][0].update(cap_drop=[]),
            lambda item: item["services"][0].update(no_new_privileges=False),
            lambda item: item["services"][0].update(read_only_rootfs=False),
            lambda item: item["services"][0].update(startup_uid=0),
            lambda item: item["services"][0].update(cap_add=["CHOWN"]),
        ):
            item = self.security_matrix()
            mutation(item)
            self.assert_rejected(self.security_schema, item)

    def test_security_matrix_rejects_docker_socket_targets(self):
        for target in (
            "/var/run/docker.sock",
            "/run/docker.sock",
            "/var/run/docker.sock/alias",
            "/run/./docker.sock",
            "/run//docker.sock",
            "/tmp/../run/docker.sock",
            "/var/run/../run/docker.sock",
        ):
            for field in ("writable_mounts", "tmpfs_mounts"):
                item = self.security_matrix()
                if field == "writable_mounts":
                    item["services"][0][field] = [{"target": target, "reason": "Synthetic negative fixture."}]
                else:
                    item["services"][0][field] = [target]
                with self.subTest(target=target, field=field):
                    self.assert_rejected(self.security_schema, item)

    def test_security_matrix_accepts_explicit_upstream_init_exception(self):
        item = self.security_matrix()
        item["services"][0].update(
            startup_uid=0,
            read_only_rootfs=False,
            cap_add=["CHOWN"],
            exception_rationale="Pinned upstream init requires root before dropping to the declared non-root application UID.",
        )
        self.assertEqual([], errors(self.security_schema, item))

    def test_all_contracts_reject_unknown_fields(self):
        for schema, instance in (
            (self.manifest_schema, self.manifest()),
            (self.images_schema, self.image_lock()),
            (self.readiness_schema, self.readiness()),
            (self.security_schema, self.security_matrix()),
        ):
            item = copy.deepcopy(instance)
            item["unexpected"] = True
            self.assert_rejected(schema, item)


if __name__ == "__main__":
    unittest.main()
