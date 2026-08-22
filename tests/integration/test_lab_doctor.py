from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.lab as lab


class LabDoctorContractTests(unittest.TestCase):
    def healthy_observation(self):
        return {
            "schema": "arr-orchestrator.lab-controller-scenario.v1",
            "scenario": "healthy",
            "category_path": "/data/downloads/arr-lab",
            "root_paths": {
                "sonarr": ["/data/media/tv"],
                "radarr": ["/data/media/movies"],
            },
            "application_sync": {"Sonarr": "addOnly", "Radarr": "addOnly"},
            "path_mapping": None,
        }

    def test_parser_accepts_doctor_suite(self):
        args = lab.build_parser().parse_args(["test", "doctor"])
        self.assertEqual("doctor", args.suite)

    def test_main_routes_doctor_suite(self):
        with mock.patch.object(lab, "test_doctor", return_value=0) as doctor:
            self.assertEqual(0, lab.main(["test", "doctor"]))
        doctor.assert_called_once_with()

    def test_doctor_lane_is_bounded_and_privacy_safe_by_construction(self):
        source = (ROOT / "scripts" / "lab.py").read_text(encoding="utf-8")
        start = source.index("def test_doctor()")
        end = source.index("\ndef render()", start)
        lane = source[start:end]

        self.assertIn("DoctorEngine", lane)
        self.assertIn("converge_radarr_hardlink_topology", lane)
        self.assertIn("controller_scenario_command", lane)
        self.assertIn("compose_down_command", lane)
        self.assertIn("assert_no_secret_exposure", lane)
        self.assertNotIn("docker system prune", lane)
        self.assertNotIn("reveal()", lane)

    def test_controller_readback_is_the_authority_for_cross_service_statuses(self):
        healthy = lab.doctor_statuses_from_observation(
            self.healthy_observation(), shared_mounts_verified=True, hardlink_status="verified"
        )
        self.assertEqual({"verified"}, set(healthy.values()))

        cases = (
            ("category_path", "/data/downloads", "category.arr-to-qbittorrent"),
            ("root_paths", {"sonarr": ["/data/downloads"], "radarr": ["/data/media/movies"]}, "root-folder.sonarr"),
            ("application_sync", {"Sonarr": "disabled", "Radarr": "addOnly"}, "application.prowlarr-to-sonarr"),
            ("path_mapping", {"host": "qbittorrent", "remotePath": "/data/downloads/", "localPath": "/data/media/tv/"}, "container-path.shared-data"),
        )
        for field, value, check_id in cases:
            with self.subTest(field=field):
                observed = self.healthy_observation()
                observed[field] = value
                statuses = lab.doctor_statuses_from_observation(
                    observed, shared_mounts_verified=True, hardlink_status="verified"
                )
                self.assertEqual("mismatch", statuses[check_id])

    def test_controller_readback_rejects_open_or_malformed_shapes(self):
        observed = self.healthy_observation()
        observed["private_provider"] = "canary"
        with self.assertRaisesRegex(lab.LabError, "doctor observation contract"):
            lab.doctor_statuses_from_observation(
                observed, shared_mounts_verified=True, hardlink_status="verified"
            )
        with self.assertRaisesRegex(lab.LabError, "doctor hardlink status"):
            lab.doctor_statuses_from_observation(
                self.healthy_observation(), shared_mounts_verified=True, hardlink_status="private"
            )

    def test_shared_data_mount_identity_is_exact(self):
        sources = {
            "sonarr": {"/data": "/runtime/data"},
            "radarr": {"/data": "/runtime/data"},
            "qbittorrent": {"/data/downloads": "/runtime/data/downloads"},
            "jellyfin": {
                "/data/media/tv": "/runtime/data/media/tv",
                "/data/media/movies": "/runtime/data/media/movies",
            },
        }
        self.assertTrue(lab.shared_data_mount_identity(sources))
        drifted = {service: dict(mounts) for service, mounts in sources.items()}
        drifted["radarr"]["/data"] = "/other/data"
        self.assertFalse(lab.shared_data_mount_identity(drifted))
        drifted = {service: dict(mounts) for service, mounts in sources.items()}
        drifted["qbittorrent"]["/data/downloads"] = "/runtime/downloads"
        self.assertFalse(lab.shared_data_mount_identity(drifted))


class LiveDoctorTests(unittest.TestCase):
    def test_live_doctor_lane(self):
        if "--live" not in sys.argv:
            self.skipTest("pass --live after unittest arguments to run the isolated Docker lane")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "lab.py"), "test", "doctor"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('"schema":"arr-orchestrator.lab-doctor-run.v1"', result.stdout)
        self.assertIn('"ok":true', result.stdout)


if __name__ == "__main__":
    unittest.main()
