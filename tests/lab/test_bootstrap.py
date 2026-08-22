import json
import os
import stat
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from scripts import lab as lab_script


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "lab" / "compose.yaml"


class BootstrapContractTests(unittest.TestCase):
    def render_compose(self, runtime_root: Path):
        env = os.environ.copy()
        env.update(
            {
                "ARR_LAB_ID": "lab-bootstrap-contract",
                "ARR_LAB_ROOT": str(runtime_root),
                "COMPOSE_PROJECT_NAME": "arr-orchestrator-lab-bootstrap-contract",
            }
        )
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE),
                "--profile",
                "services",
                "--profile",
                "isolation",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)

    def test_bootstrap_sources_are_repository_owned(self):
        for relative in (
            "lab/host/__init__.py",
            "lab/host/runtime.py",
            "lab/host/secrets.py",
            "lab/controller/__init__.py",
            "lab/controller/bootstrap.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_runtime_layout_is_private_contained_and_symlink_safe(self):
        from lab.host import runtime

        with tempfile.TemporaryDirectory() as directory:
            trusted = Path(directory)
            root = trusted / "lab-safe"
            layout = runtime.prepare_runtime_tree(root, trusted)
            self.assertEqual(root.resolve(), layout.root)
            for path in layout.private_directories():
                self.assertEqual(0o700, stat.S_IMODE(path.stat().st_mode))
                self.assertTrue(path.resolve().is_relative_to(root.resolve()))
            redirected = trusted / "lab-redirected"
            redirected.symlink_to(root, target_is_directory=True)
            with self.assertRaises(runtime.RuntimeSafetyError):
                runtime.prepare_runtime_tree(redirected, trusted)

    def test_credentials_are_per_run_private_and_redacted(self):
        from lab.host import secrets

        ownership = []

        def owner_setter(path, uid, gid):
            ownership.append((path.name, uid, gid))

        with tempfile.TemporaryDirectory() as directory:
            first = secrets.provision_credentials(Path(directory) / "one", owner_setter)
            second = secrets.provision_credentials(Path(directory) / "two", owner_setter)
            self.assertNotEqual(first.fingerprint(), second.fingerprint())
            self.assertNotIn(first.value("sonarr", "api_key"), repr(first))
            self.assertNotIn(first.value("jellyfin", "password"), first.redacted_summary())
            self.assertEqual(
                {"sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin"},
                set(first.files),
            )
            for path in first.files.values():
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertTrue(all(uid == 65532 and gid == 65532 for _, uid, gid in ownership))

    def test_first_start_configs_do_not_embed_raw_passwords(self):
        from lab.host import secrets

        credential_value = "a" * 32
        arr_xml = secrets.build_arr_config(credential_value, port=8989)
        root = ET.fromstring(arr_xml)
        self.assertEqual(credential_value, root.findtext("ApiKey"))
        self.assertEqual("8989", root.findtext("Port"))
        self.assertEqual("External", root.findtext("AuthenticationMethod"))

        credential_value = "synthetic-password-value"
        qbit = secrets.build_qbittorrent_config("labadmin", credential_value, "qbt_" + "x" * 28)
        self.assertIn("WebUI\\Username=labadmin", qbit)
        self.assertNotIn(credential_value, qbit)
        self.assertIn("WebUI\\Password_PBKDF2=", qbit)
        self.assertIn("WebUI\\API" + "Key=qbt_", qbit)

    def test_controller_image_contains_bootstrap_packages_without_static_secrets(self):
        dockerfile = (ROOT / "lab" / "Dockerfile.controller").read_text()
        self.assertIn("COPY --chown=65532:65532 lab/host /app/lab/host", dockerfile)
        self.assertIn("COPY --chown=65532:65532 lab/controller /app/lab/controller", dockerfile)
        with tempfile.TemporaryDirectory() as directory:
            config = self.render_compose(Path(directory) / "runtime")
        controller = config["services"]["lab-controller"]
        self.assertEqual([], controller.get("volumes", []))
        rendered = json.dumps(controller, sort_keys=True)
        self.assertNotIn("credential", rendered)
        self.assertNotIn("password", rendered.lower())

    def test_lab_cli_exposes_bootstrap_integration_suite(self):
        parsed = lab_script.build_parser().parse_args(["test", "bootstrap"])
        self.assertEqual("bootstrap", parsed.suite)

    def test_prowlarr_schema_selection_requires_explicit_name_when_implementation_repeats(self):
        from lab.controller import bootstrap

        schemas = [
            {"implementation": "Newznab", "name": "Preset A", "fields": []},
            {"implementation": "Newznab", "name": "Generic Newznab", "fields": []},
        ]
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.schema_by_implementation(schemas, "Newznab")
        selected = bootstrap.schema_by_implementation(schemas, "Newznab", name="Generic Newznab")
        self.assertEqual("Generic Newznab", selected["name"])

    def test_validation_diagnostics_expose_only_property_and_code(self):
        from lab.controller import bootstrap

        raw = json.dumps(
            [
                {
                    "propertyName": "AppProfileId",
                    "errorCode": "GreaterThanValidator",
                    "errorMessage": "must be greater than zero and must not leak",
                    "attemptedValue": "sensitive-value",
                }
            ]
        ).encode()
        diagnostic = bootstrap.validation_codes(raw)
        self.assertEqual("AppProfileId:GreaterThanValidator", diagnostic)
        self.assertNotIn("sensitive", diagnostic)

    def test_access_output_is_exclusive_private_and_not_overwritten(self):
        from lab.controller.bootstrap import BootstrapError, write_access_output

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jellyfin-access"
            write_access_output(path, "opaque-session-value")
            self.assertEqual("opaque-session-value", path.read_text())
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            with self.assertRaises(BootstrapError):
                write_access_output(path, "replacement")
            self.assertEqual("opaque-session-value", path.read_text())

    def test_arr_download_client_bootstrap_is_idempotent_and_redacted(self):
        from lab.controller.bootstrap import ensure_arr_download_client

        class Client:
            def __init__(self, service):
                self.service = service
                self.items = []
                self.posts = []

            def request(self, method, path, *, payload=None, expected=(200,), **_kwargs):
                if method == "GET" and path == "/api/v3/downloadclient":
                    return list(self.items)
                if method == "GET" and path == "/api/v3/downloadclient/schema":
                    category = "tvCategory" if self.service == "sonarr" else "movieCategory"
                    return [
                        {
                            "implementation": "QBittorrent",
                            "configContract": "QBittorrentSettings",
                            "fields": [
                                {"name": name, "value": None}
                                for name in ("host", "port", "useSsl", "username", "password", category)
                            ],
                        }
                    ]
                if method == "POST" and path == "/api/v3/downloadclient":
                    self.posts.append(payload)
                    self.items = [dict(payload)]
                    return payload
                raise AssertionError((method, path, expected))

        opaque_value = "synthetic-private-password"
        for service in ("sonarr", "radarr"):
            with self.subTest(service=service):
                client = Client(service)
                baseline = ensure_arr_download_client(
                    client,
                    service,
                    {"username": "labadmin", "password": opaque_value},
                )
                second = ensure_arr_download_client(
                    client,
                    service,
                    {"username": "labadmin", "password": opaque_value},
                )
                self.assertEqual(baseline, second)
                self.assertEqual(1, len(client.posts))
                self.assertEqual(
                    {"download_clients": [{"implementation": "QBittorrent", "enabled": True}]},
                    baseline,
                )
                self.assertNotIn(opaque_value, json.dumps(baseline, sort_keys=True))

    def test_qbittorrent_bootstrap_repairs_owned_category_path_mismatch(self):
        from lab.controller import bootstrap

        class Client:
            def __init__(self):
                self.category = {"savePath": ""}
                self.edits = []

            def wait_json(self, path):
                self.path = path

            def request(self, method, path, *, form=None, expected=(200,), **_kwargs):
                if method == "GET" and path == "/api/v2/torrents/categories":
                    return {"arr-lab": dict(self.category)}
                if method == "POST" and path == "/api/v2/torrents/editCategory":
                    self.edits.append(dict(form or {}))
                    self.category = {"savePath": form["savePath"]}
                    return None
                raise AssertionError((method, path, form, expected))

        client = Client()
        with (
            mock.patch.object(bootstrap, "read_credential", return_value={"api_key": "opaque"}),
            mock.patch.object(bootstrap, "HttpClient", return_value=client),
        ):
            state, baseline = bootstrap.bootstrap_qbittorrent()

        self.assertEqual("baseline_verified", state)
        self.assertEqual(
            [{"category": "arr-lab", "savePath": "/data/downloads/arr-lab"}],
            client.edits,
        )
        self.assertEqual(
            {"categories": {"arr-lab": {"savePath": "/data/downloads/arr-lab"}}},
            baseline,
        )

    def test_last_json_object_ignores_buildkit_output(self):
        output = "#1 building\n#2 exporting\n{\"ok\":true,\"schema\":\"example.v1\"}\n"
        self.assertEqual({"ok": True, "schema": "example.v1"}, lab_script.last_json_object(output))

    def test_secret_variant_gate_excludes_non_generated_username_identifier(self):
        class Bundle:
            values = {
                ("sonarr", "api_key"): "sonarr-value",
                ("radarr", "api_key"): "radarr-value",
                ("prowlarr", "api_key"): "prowlarr-value",
                ("qbittorrent", "password"): "qbit-value",
                ("qbittorrent", "api_key"): "qbt_value",
                ("jellyfin", "password"): "jelly-value",
            }

            def value(self, service, field):
                return self.values[(service, field)]

        variants = lab_script.credential_variants(Bundle(), "mock-value")
        self.assertNotIn("labadmin", variants)
        self.assertTrue(any(label == "jellyfin.credential:raw" for label in variants.values()))

    def test_live_baseline_hashes_match_readiness_contract(self):
        from lab.controller import bootstrap

        baselines = {
            "sonarr": {
                "root_paths": ["/data/media/tv"],
                "download_clients": [{"implementation": "QBittorrent", "enabled": True}],
            },
            "radarr": {
                "root_paths": ["/data/media/movies"],
                "download_clients": [{"implementation": "QBittorrent", "enabled": True}],
            },
            "prowlarr": {
                "applications": ["Radarr", "Sonarr"],
                "indexers": ["Synthetic Mock Indexer"],
            },
            "qbittorrent": {"categories": {"arr-lab": {"savePath": "/data/downloads/arr-lab"}}},
            "jellyfin": {"virtual_folders": []},
        }
        result = bootstrap.redacted_result(
            {service: "baseline_verified" for service in baselines}, baselines
        )
        readiness = json.loads((ROOT / "lab" / "readiness.json").read_text())
        expected = {
            service: readiness["services"][service]["states"]["baseline_verified"]["expected_baseline_digest"]
            for service in baselines
        }
        self.assertEqual(expected, result["baseline_digests"])


if __name__ == "__main__":
    unittest.main()
