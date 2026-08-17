import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lab.controller import scenarios
from scripts import lab as lab_script


ROOT = Path(__file__).resolve().parents[2]


EXPECTED_DRIVERS = {
    "healthy": "controller-api",
    "category-mismatch": "controller-api",
    "root-folder-mismatch": "controller-api",
    "application-sync-mismatch": "controller-api",
    "path-mapping-mismatch": "controller-api",
    "hardlink-cross-device": "host-compose",
    "service-unavailable": "host-compose",
    "unsupported-api-version": "runner-config",
    "stale-plan": "runner-config",
    "destructive-denial": "runner-config",
}


class ScenarioContractTests(unittest.TestCase):
    class StatefulClient:
        def __init__(self, service):
            self.service = service
            self.mutations = 0
            self.categories = {"arr-lab": {"savePath": "/data/downloads/arr-lab"}}
            self.roots = [{"id": 1, "path": "/data/media/tv", "accessible": True}]
            self.mappings = []
            self.applications = [{"id": 7, "implementation": "Sonarr", "syncLevel": "addOnly", "fields": []}]

        def request(self, method, path, *, payload=None, form=None, expected=(200,), headers=None):
            if method == "GET" and path == "/api/v2/torrents/categories":
                return self.categories
            if method == "POST" and path == "/api/v2/torrents/editCategory":
                self.categories[form["category"]] = {"savePath": form["savePath"]}
                self.mutations += 1
                return ""
            if method == "POST" and path == "/api/v2/torrents/createCategory":
                self.categories[form["category"]] = {"savePath": form["savePath"]}
                self.mutations += 1
                return ""
            if method == "GET" and path == "/api/v3/rootfolder":
                return self.roots
            if method == "GET" and path == "/api/v3/series":
                return []
            if method == "POST" and path == "/api/v3/rootfolder":
                self.roots.append({"id": max([item["id"] for item in self.roots] + [0]) + 1, "path": payload["path"], "accessible": True})
                self.mutations += 1
                return self.roots[-1]
            if method == "DELETE" and path.startswith("/api/v3/rootfolder/"):
                target = int(path.rsplit("/", 1)[1])
                self.roots = [item for item in self.roots if item["id"] != target]
                self.mutations += 1
                return ""
            if method == "GET" and path == "/api/v3/remotepathmapping":
                return self.mappings
            if method == "POST" and path == "/api/v3/remotepathmapping":
                self.mappings.append({"id": 11, **payload})
                self.mutations += 1
                return self.mappings[-1]
            if method == "DELETE" and path.startswith("/api/v3/remotepathmapping/"):
                self.mappings = []
                self.mutations += 1
                return ""
            if method == "GET" and path == "/api/v1/applications":
                return self.applications
            if method == "GET" and path.startswith("/api/v1/applications/"):
                return self.applications[0]
            if method == "PUT" and path.startswith("/api/v1/applications/"):
                self.applications = [dict(payload)]
                self.mutations += 1
                return payload
            raise AssertionError((method, path, payload, form, expected, headers))

    def test_registry_declares_exact_allowlisted_scenarios_and_one_driver_each(self):
        registry = scenarios.load_registry(ROOT / "lab" / "scenarios" / "registry.json")
        self.assertEqual(EXPECTED_DRIVERS, {item["name"]: item["driver"] for item in registry})
        manifest_schema = json.loads((ROOT / "schemas" / "lab-manifest.schema.json").read_text())
        self.assertEqual(
            set(EXPECTED_DRIVERS),
            set(manifest_schema["properties"]["scenarios"]["items"]["enum"]),
        )
        for item in registry:
            self.assertEqual({"name", "driver", "description", "action"}, set(item))
            self.assertNotIn("command", json.dumps(item).lower())
            self.assertNotIn("shell", json.dumps(item).lower())
            if item["driver"] == "runner-config":
                template = ROOT / item["action"]["template"]
                self.assertTrue(template.is_file())
                self.assertTrue(template.is_relative_to(ROOT / "lab" / "scenarios" / "runner-config"))

    def test_topology_scenarios_reference_committed_renderable_overrides(self):
        registry = scenarios.load_registry(ROOT / "lab" / "scenarios" / "registry.json")
        topology = [item for item in registry if item["driver"] == "host-compose"]
        self.assertEqual(2, len(topology))
        for item in topology:
            override = ROOT / item["action"]["override"]
            self.assertTrue(override.is_file(), override)
            self.assertTrue(override.is_relative_to(ROOT / "lab" / "scenarios"))
            rendered = lab_script.render_scenario_override(item["name"], Path("/tmp/lab-scenario-contract"))
            self.assertEqual(item["name"], rendered["scenario"])
            self.assertEqual(0, rendered["published_ports"])
            self.assertTrue(rendered["internal_network"])
            self.assertFalse(rendered["docker_socket"])
        hardlink = lab_script.render_scenario_override(
            "hardlink-cross-device", Path("/tmp/lab-scenario-contract")
        )
        self.assertEqual("tmpfs", hardlink["mount_types"]["radarr-hardlink"]["/data/media/movies"])
        self.assertEqual("bind", hardlink["mount_types"]["radarr-hardlink"]["/data/downloads"])
        self.assertEqual(["radarr-hardlink"], hardlink["scenario_services"])
        unavailable = lab_script.render_scenario_override(
            "service-unavailable", Path("/tmp/lab-scenario-contract")
        )
        self.assertEqual(["prowlarr"], unavailable["scenario_services"])

    def test_healthy_arr_services_use_one_data_mount_for_real_hardlinks(self):
        rendered = json.loads(
            lab_script.run(
                lab_script.compose_command("--profile", "services", "config", "--format", "json"),
                env=lab_script.lab_env(
                    "lab-scenario-contract",
                    "arr-orchestrator-lab-scenario-contract",
                    Path("/tmp/lab-scenario-contract"),
                ),
            ).stdout
        )
        for service_name in ("sonarr", "radarr"):
            mounts = {
                item["target"]: item["type"]
                for item in rendered["services"][service_name]["volumes"]
            }
            self.assertEqual("bind", mounts["/data"])
            self.assertNotIn("/data/downloads", mounts)
            self.assertNotIn("/data/media/tv", mounts)
            self.assertNotIn("/data/media/movies", mounts)

    def test_runner_config_apply_is_idempotent_and_healthy_removes_only_scenario_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab-safe"
            root.mkdir(mode=0o700)
            marker = root / lab_script.MARKER
            marker.write_text(json.dumps({"compose_project": "arr-orchestrator-lab-safe", "lab_id": "lab-safe"}))
            first = scenarios.apply_runner_config(root, "stale-plan")
            first_bytes = first.read_bytes()
            second = scenarios.apply_runner_config(root, "stale-plan")
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second.read_bytes())
            state = json.loads(second.read_text())
            self.assertEqual("stale-plan", state["scenario"])
            self.assertEqual("inventory-new", state["fault"]["current_inventory_revision"])
            self.assertEqual("plan-old", state["fault"]["plan_inventory_revision"])
            scenarios.apply_runner_config(root, "healthy")
            self.assertFalse(first.exists())
            self.assertTrue(marker.exists())

    def test_runner_config_rejects_unknown_scenario_and_symlink_root(self):
        with tempfile.TemporaryDirectory() as directory:
            trusted = Path(directory)
            root = trusted / "lab-safe"
            root.mkdir()
            (root / lab_script.MARKER).write_text(json.dumps({"compose_project": "arr-orchestrator-lab-safe", "lab_id": "lab-safe"}))
            with self.assertRaises(scenarios.ScenarioError):
                scenarios.apply_runner_config(root, "arbitrary-shell")
            redirected = trusted / "lab-redirected"
            redirected.symlink_to(root, target_is_directory=True)
            with self.assertRaises(scenarios.ScenarioError):
                scenarios.apply_runner_config(redirected, "stale-plan")

    def test_healthy_restore_refuses_symlinked_scenario_directory_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            trusted = Path(directory)
            root = trusted / "lab-safe"
            root.mkdir()
            (root / lab_script.MARKER).write_text(
                json.dumps({"compose_project": "arr-orchestrator-lab-safe", "lab_id": "lab-safe"})
            )
            outside = trusted / "outside"
            outside.mkdir()
            target = outside / "current.json"
            target.write_text('{"outside":true}\n')
            (root / "scenarios").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(scenarios.ScenarioError):
                scenarios.apply_runner_config(root, "healthy")
            self.assertEqual('{"outside":true}\n', target.read_text())

    def test_runner_config_rejects_symlinked_runtime_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            trusted = Path(directory)
            real_parent = trusted / "real-parent"
            real_parent.mkdir()
            root = real_parent / "lab-safe"
            root.mkdir()
            (root / lab_script.MARKER).write_text(
                json.dumps({"compose_project": "arr-orchestrator-lab-safe", "lab_id": "lab-safe"})
            )
            linked_parent = trusted / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaises(scenarios.ScenarioError):
                scenarios.apply_runner_config(linked_parent / "lab-safe", "stale-plan")
            self.assertFalse((root / "scenarios" / "current.json").exists())

    def test_host_authority_rejects_project_or_label_mismatch(self):
        expected = {
            "project": "arr-orchestrator-lab-safe",
            "lab_id": "lab-safe",
            "services": {"prowlarr": {"project": "arr-orchestrator-lab-safe", "lab_id": "lab-safe"}},
        }
        scenarios.verify_host_authority(expected, "arr-orchestrator-lab-safe", "lab-safe", {"prowlarr"})
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.verify_host_authority(expected, "arr-orchestrator-other", "lab-safe", {"prowlarr"})
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.verify_host_authority(expected, "arr-orchestrator-lab-safe", "lab-other", {"prowlarr"})

    def test_host_compose_mutation_verifies_authority_before_running_command(self):
        events = []
        completed = __import__("subprocess").CompletedProcess(["docker"], 0, "", "")

        def verify(*_args, **_kwargs):
            events.append("verify")

        def execute(*_args, **_kwargs):
            events.append("run")
            return completed

        with patch.object(lab_script, "verify_scenario_project_authority", side_effect=verify), patch.object(
            lab_script, "run", side_effect=execute
        ):
            result = lab_script.run_authorized_compose_mutation(
                "arr-orchestrator-lab-safe",
                "lab-safe",
                Path("/tmp/lab-safe"),
                {"prowlarr"},
                ["docker", "compose", "stop", "prowlarr"],
                {},
            )
        self.assertEqual(["verify", "run"], events)
        self.assertIs(completed, result)

    def test_controller_api_scenarios_are_idempotent_and_healthy_restores_baseline(self):
        for name in (
            "category-mismatch",
            "root-folder-mismatch",
            "application-sync-mismatch",
            "path-mapping-mismatch",
        ):
            with self.subTest(name=name):
                clients = {
                    "qbittorrent": self.StatefulClient("qbittorrent"),
                    "sonarr": self.StatefulClient("sonarr"),
                    "prowlarr": self.StatefulClient("prowlarr"),
                }
                first = scenarios.apply_controller_scenario(name, clients)
                if name == "category-mismatch":
                    self.assertEqual("/data/downloads", first["category_path"])
                elif name == "root-folder-mismatch":
                    self.assertEqual(["/data/downloads"], first["root_paths"])
                elif name == "application-sync-mismatch":
                    self.assertEqual("disabled", first["application_sync"])
                elif name == "path-mapping-mismatch":
                    self.assertEqual(
                        {
                            "host": "qbittorrent",
                            "remotePath": "/data/downloads/",
                            "localPath": "/data/media/tv/",
                        },
                        first["path_mapping"],
                    )
                mutations = sum(client.mutations for client in clients.values())
                second = scenarios.apply_controller_scenario(name, clients)
                self.assertEqual(first, second)
                self.assertEqual(mutations, sum(client.mutations for client in clients.values()))
                healthy = scenarios.apply_controller_scenario("healthy", clients)
                self.assertEqual("healthy", healthy["scenario"])
                self.assertEqual("/data/downloads/arr-lab", clients["qbittorrent"].categories["arr-lab"]["savePath"])
                self.assertEqual(["/data/media/tv"], [item["path"] for item in clients["sonarr"].roots])
                self.assertEqual([], clients["sonarr"].mappings)
                self.assertEqual("addOnly", clients["prowlarr"].applications[0]["syncLevel"])

    def test_healthy_controller_target_creates_missing_qbittorrent_category(self):
        clients = {
            "qbittorrent": self.StatefulClient("qbittorrent"),
            "sonarr": self.StatefulClient("sonarr"),
            "prowlarr": self.StatefulClient("prowlarr"),
        }
        clients["qbittorrent"].categories = {}
        result = scenarios.apply_controller_scenario("healthy", clients)
        self.assertEqual("/data/downloads/arr-lab", result["category_path"])

    def test_controller_refuses_unknown_root_or_conflicting_path_mapping(self):
        clients = {
            "qbittorrent": self.StatefulClient("qbittorrent"),
            "sonarr": self.StatefulClient("sonarr"),
            "prowlarr": self.StatefulClient("prowlarr"),
        }
        clients["sonarr"].roots.append({"id": 9, "path": "/foreign", "accessible": True})
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.apply_controller_scenario("healthy", clients)
        clients["sonarr"].roots = [{"id": 1, "path": "/data/media/tv", "accessible": True}]
        clients["sonarr"].mappings = [
            {"id": 12, "host": "QBITTORRENT", "remotePath": "/data/downloads/", "localPath": "/other/"}
        ]
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.apply_controller_scenario("path-mapping-mismatch", clients)

    def test_controller_preflights_all_conflicts_before_any_mutation(self):
        unknown_mapping = {
            "id": 12,
            "host": "other-client",
            "remotePath": "/foreign/",
            "localPath": "/other/",
        }
        clients = {
            "qbittorrent": self.StatefulClient("qbittorrent"),
            "sonarr": self.StatefulClient("sonarr"),
            "prowlarr": self.StatefulClient("prowlarr"),
        }
        clients["sonarr"].mappings = [unknown_mapping]
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.apply_controller_scenario("healthy", clients)
        self.assertEqual(0, sum(client.mutations for client in clients.values()))

        clients = {
            "qbittorrent": self.StatefulClient("qbittorrent"),
            "sonarr": self.StatefulClient("sonarr"),
            "prowlarr": self.StatefulClient("prowlarr"),
        }
        clients["prowlarr"].applications.append(
            {"id": 8, "implementation": "Sonarr", "name": "duplicate", "syncLevel": "addOnly"}
        )
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.apply_controller_scenario("healthy", clients)
        self.assertEqual(0, sum(client.mutations for client in clients.values()))

        clients = {
            "qbittorrent": self.StatefulClient("qbittorrent"),
            "sonarr": self.StatefulClient("sonarr"),
            "prowlarr": self.StatefulClient("prowlarr"),
        }
        clients["sonarr"].mappings = [unknown_mapping]
        original_category = clients["qbittorrent"].categories["arr-lab"]["savePath"]
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.apply_controller_scenario("category-mismatch", clients)
        self.assertEqual(original_category, clients["qbittorrent"].categories["arr-lab"]["savePath"])
        self.assertEqual(0, sum(client.mutations for client in clients.values()))

    def test_lab_cli_exposes_scenarios_suite(self):
        parsed = lab_script.build_parser().parse_args(["test", "scenarios"])
        self.assertEqual("scenarios", parsed.suite)

    def test_direct_script_execution_prefers_repository_lab_package_over_scripts_lab_module(self):
        original = list(sys.path)
        with patch.object(sys, "path", [str(ROOT / "scripts"), *original]):
            lab_script.ensure_repository_import_path()
            self.assertEqual(str(ROOT), sys.path[0])

    def test_controller_image_contains_only_the_allowlisted_scenario_contract(self):
        dockerfile = (ROOT / "lab" / "Dockerfile.controller").read_text()
        dockerignore = (ROOT / ".dockerignore").read_text()
        self.assertIn("COPY --chown=65532:65532 lab/scenarios/registry.json /app/lab/scenarios/registry.json", dockerfile)
        self.assertIn("!lab/scenarios/registry.json", dockerignore)
        self.assertNotIn("COPY --chown=65532:65532 lab/scenarios /app/lab/scenarios", dockerfile)


if __name__ == "__main__":
    unittest.main()
