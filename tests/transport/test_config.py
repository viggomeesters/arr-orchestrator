import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.config import EndpointConfigError, ServiceEndpoint, load_service_endpoints
import arr_orchestrator.config as config_module


class EndpointConfigTests(unittest.TestCase):
    def test_schema_and_loader_accept_only_refs_not_values(self):
        schema = json.loads((ROOT / "schemas/runtime/service-endpoints.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        payload = {
            "schema": "arr-orchestrator.runtime-service-endpoints.v1",
            "services": {
                "fault-api": {"base_url": "http://fault-api:8080", "secret_ref": "file:fault-api-token"}
            },
        }
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(payload)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "endpoints.json"
            path.write_text(json.dumps(payload))
            endpoints = load_service_endpoints(path)
        self.assertEqual("fault-api", endpoints["fault-api"].service_id)
        self.assertEqual("file:fault-api-token", endpoints["fault-api"].secret_ref)
        self.assertNotIn("secret", repr(endpoints["fault-api"]).lower().replace("secret_ref", ""))

    def test_loader_rejects_embedded_credentials_and_non_origin_urls(self):
        invalid = (
            {"base_url": "https://user:pass@example.test", "secret_ref": "file:key"},
            {"base_url": "https://example.test/path", "secret_ref": "file:key"},
            {"base_url": "https://example.test?x=1", "secret_ref": "file:key"},
            {"base_url": "ftp://example.test", "secret_ref": "file:key"},
            {"base_url": "https://example.test", "secret_ref": "file:../key"},
            {"base_url": "https://example.test", "secret_ref": "file:key", "api_key": "value"},
        )
        for service in invalid:
            with self.subTest(service=sorted(service)):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "endpoints.json"
                    path.write_text(json.dumps({"schema": "arr-orchestrator.runtime-service-endpoints.v1", "services": {"svc": service}}))
                    with self.assertRaises(EndpointConfigError):
                        load_service_endpoints(path)

    def test_direct_endpoint_construction_cannot_bypass_origin_validation(self):
        invalid_origins = (
            "https://user:pass@example.test/private",
            "https://exa_mple.test",
            "https://example.test:00080",
            "https://example.test\t",
            "http://[bad",
            "http://[" + ":" + ":1]",
            "https://" + ("a." * 127) + "a",
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin), self.assertRaises(EndpointConfigError):
                ServiceEndpoint("svc", origin, "file:key")
        schema = json.loads((ROOT / "schemas/runtime/service-endpoints.schema.json").read_text())
        validator = Draft202012Validator(schema)
        for origin in (*invalid_origins, "https://example.test:65536"):
            payload = {
                "schema": "arr-orchestrator.runtime-service-endpoints.v1",
                "services": {"svc": {"base_url": origin, "secret_ref": "file:key"}},
            }
            with self.subTest(schema_origin=origin):
                self.assertTrue(list(validator.iter_errors(payload)))

    def test_loader_rejects_duplicate_json_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "endpoints.json"
            path.write_text(
                '{"schema":"arr-orchestrator.runtime-service-endpoints.v1",'
                '"services":{"svc":{"base_url":"https://one.test","base_url":"https://two.test",'
                '"secret_ref":"file:key"}}}'
            )
            with self.assertRaises(EndpointConfigError):
                load_service_endpoints(path)

    def test_loader_rejects_repository_and_symlinked_paths(self):
        with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".json", delete=False) as stream:
            repository_path = Path(stream.name)
            json.dump({"schema": "arr-orchestrator.runtime-service-endpoints.v1", "services": {"svc": {"base_url": "https://example.test", "secret_ref": "file:key"}}}, stream)
        try:
            with self.assertRaises(EndpointConfigError):
                load_service_endpoints(repository_path)
        finally:
            repository_path.unlink()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real = base / "real"
            real.mkdir()
            config = real / "endpoints.json"
            config.write_text(json.dumps({"schema": "arr-orchestrator.runtime-service-endpoints.v1", "services": {"svc": {"base_url": "https://example.test", "secret_ref": "file:key"}}}))
            linked = base / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaises(EndpointConfigError):
                load_service_endpoints(linked / "endpoints.json")

    def test_loader_cannot_follow_file_swap_between_validation_and_open(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / "endpoints.json"
            alternate = base / "alternate.json"
            safe_payload = {
                "schema": "arr-orchestrator.runtime-service-endpoints.v1",
                "services": {"svc": {"base_url": "https://safe.test", "secret_ref": "file:key"}},
            }
            alternate_payload = {
                "schema": "arr-orchestrator.runtime-service-endpoints.v1",
                "services": {"svc": {"base_url": "https://alternate.test", "secret_ref": "file:key"}},
            }
            path.write_text(json.dumps(safe_payload))
            alternate.write_text(json.dumps(alternate_payload))
            original_open = os.open
            swapped = False

            def racing_open(name, flags, *args, **kwargs):
                nonlocal swapped
                if name == "endpoints.json" and kwargs.get("dir_fd") is not None and not swapped:
                    swapped = True
                    path.rename(base / "original.json")
                    path.symlink_to(alternate)
                return original_open(name, flags, *args, **kwargs)

            config_module.os.open = racing_open
            try:
                with self.assertRaises(EndpointConfigError):
                    load_service_endpoints(path)
            finally:
                config_module.os.open = original_open


if __name__ == "__main__":
    unittest.main()
