from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.doctor import DoctorEngine, EvidenceCheck
from arr_orchestrator.inventory import REQUIRED_SERVICES, ServiceInventory, StackInventory


REQUIRED_CHECKS = {
    "category.arr-to-qbittorrent",
    "application.prowlarr-to-sonarr",
    "application.prowlarr-to-radarr",
    "root-folder.sonarr",
    "root-folder.radarr",
    "container-path.shared-data",
    "hardlink.downloads-to-media",
}


def service(
    name: str,
    *,
    state: str = "available",
    api_version: int | None = None,
    evidence: tuple[tuple[str, object], ...] = (),
    failure_code: str | None = None,
) -> ServiceInventory:
    return ServiceInventory(
        service=name,
        state=state,
        version="1.0.0" if state in {"available", "partial"} else None,
        api_version=api_version,
        resources=("system_status",) if name in {"sonarr", "radarr", "prowlarr"} and state == "available" else (),
        evidence=evidence,
        failure_code=failure_code,
    )


def healthy_inventory() -> StackInventory:
    rows = (
        service(
            "sonarr",
            api_version=3,
            evidence=(("root_folder_count", 1), ("inaccessible_root_folder_count", 0), ("download_client_count", 1), ("enabled_download_client_count", 1), ("quality_profile_count", 1), ("queue_total", 0)),
        ),
        service(
            "radarr",
            api_version=3,
            evidence=(("root_folder_count", 1), ("inaccessible_root_folder_count", 0), ("download_client_count", 1), ("enabled_download_client_count", 1), ("quality_profile_count", 1), ("queue_total", 0)),
        ),
        service(
            "prowlarr",
            api_version=1,
            evidence=(("application_count", 2), ("indexer_total", 1), ("indexer_enabled", 1), ("indexer_rss_capable", 1), ("indexer_search_capable", 1)),
        ),
        ServiceInventory("qbittorrent", "available", version="5.2.3", resources=("categories", "queue"), evidence=(("webapi_version", "2.15.1"), ("category_count", 1), ("queue_total", 0))),
        ServiceInventory("jellyfin", "available", version="10.11.11", resources=("health", "libraries", "refresh_status"), evidence=(("healthy", True), ("startup_complete", True), ("library_count", 2), ("location_count", 2), ("refreshing_library_count", 0), ("collection_type_counts", (("movies", 1), ("tvshows", 1))))),
    )
    self_check = tuple(row.service for row in rows)
    assert self_check == REQUIRED_SERVICES
    return StackInventory(rows, "healthy")


def checks(**overrides: str) -> tuple[EvidenceCheck, ...]:
    statuses = {check_id: "verified" for check_id in REQUIRED_CHECKS}
    statuses.update(overrides)
    return tuple(
        EvidenceCheck(check_id, statuses[check_id], (f"doctor.{check_id}",))
        for check_id in sorted(statuses)
    )


class DoctorRuleTests(unittest.TestCase):
    def test_healthy_stack_has_no_findings_and_deterministic_json(self):
        report = DoctorEngine().diagnose(healthy_inventory(), checks())

        self.assertEqual("healthy", report.state)
        self.assertEqual((), report.findings)
        self.assertEqual(report.to_json(), report.to_json())
        self.assertEqual(report.to_dict(), json.loads(report.to_json()))

    def test_every_finding_has_complete_privacy_safe_guidance(self):
        report = DoctorEngine().diagnose(
            healthy_inventory(),
            checks(**{
                "category.arr-to-qbittorrent": "mismatch",
                "application.prowlarr-to-sonarr": "missing",
                "root-folder.sonarr": "mismatch",
                "container-path.shared-data": "ambiguous",
            }),
        )

        self.assertEqual("blocked", report.state)
        codes = {finding.code for finding in report.findings}
        self.assertEqual(
            {"APPLICATION_LINK_MISSING", "CATEGORY_MISMATCH", "CONTAINER_PATH_AMBIGUOUS", "ROOT_FOLDER_MISMATCH"},
            codes,
        )
        encoded = report.to_json()
        for finding in report.findings:
            payload = finding.to_dict()
            self.assertEqual("blocker", payload["severity"])
            self.assertTrue(payload["owner"])
            self.assertTrue(payload["evidence_refs"])
            self.assertTrue(payload["explanation"])
            self.assertTrue(payload["remediation"])
        self.assertNotIn("/data/", encoded)
        self.assertNotIn("PRIVATE", encoded)

    def test_partial_inventory_never_becomes_healthy(self):
        inventory = healthy_inventory()
        rows = list(inventory.services)
        rows[2] = service("prowlarr", state="unreachable", failure_code="SERVICE_UNREACHABLE")
        report = DoctorEngine().diagnose(StackInventory(tuple(rows), "partial"), checks())

        self.assertEqual("blocked", report.state)
        self.assertIn("SERVICE_UNREACHABLE", {finding.code for finding in report.findings})

    def test_missing_required_evidence_fails_closed(self):
        supplied = tuple(check for check in checks() if check.check_id != "hardlink.downloads-to-media")
        report = DoctorEngine().diagnose(healthy_inventory(), supplied)

        finding = next(item for item in report.findings if item.code == "HARDLINK_EVIDENCE_UNAVAILABLE")
        self.assertEqual("storage", finding.owner)
        self.assertEqual(("doctor.hardlink.downloads-to-media",), finding.evidence_refs)

    def test_unavailable_or_ambiguous_path_identity_fails_closed(self):
        for status, expected in (("unavailable", "CONTAINER_PATH_EVIDENCE_UNAVAILABLE"), ("ambiguous", "CONTAINER_PATH_AMBIGUOUS")):
            with self.subTest(status=status):
                report = DoctorEngine().diagnose(
                    healthy_inventory(),
                    checks(**{"container-path.shared-data": status}),
                )
                self.assertIn(expected, {finding.code for finding in report.findings})
                self.assertEqual("blocked", report.state)

    def test_hardlink_impossible_is_a_blocker(self):
        report = DoctorEngine().diagnose(
            healthy_inventory(),
            checks(**{"hardlink.downloads-to-media": "impossible"}),
        )

        finding = next(item for item in report.findings if item.code == "HARDLINK_IMPOSSIBLE")
        self.assertEqual("blocker", finding.severity)
        self.assertEqual("storage", finding.owner)

    def test_inventory_counts_detect_missing_local_configuration(self):
        inventory = healthy_inventory()
        rows = list(inventory.services)
        rows[0] = service(
            "sonarr",
            api_version=3,
            evidence=(("root_folder_count", 0), ("inaccessible_root_folder_count", 0), ("download_client_count", 0), ("enabled_download_client_count", 0), ("quality_profile_count", 0), ("queue_total", 0)),
        )
        report = DoctorEngine().diagnose(StackInventory(tuple(rows), "healthy"), checks())

        self.assertEqual(
            {"DOWNLOAD_CLIENT_MISSING", "QUALITY_PROFILE_MISSING", "ROOT_FOLDER_MISSING"},
            {finding.code for finding in report.findings},
        )

    def test_unknown_check_ids_and_private_evidence_refs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_CHECK"):
            EvidenceCheck("private.check", "verified", ("doctor.private",))
        with self.assertRaisesRegex(ValueError, "INVALID_EVIDENCE_REF"):
            EvidenceCheck("root-folder.sonarr", "verified", ("/private/path",))

    def test_hostile_string_subclasses_are_rejected_before_hashing(self):
        class HostileString(str):
            def __hash__(self):
                raise RuntimeError("PRIVATE-HASH")

            def __eq__(self, other):
                raise RuntimeError("PRIVATE-EQUALITY")

        with self.assertRaisesRegex(ValueError, "INVALID_CHECK_ID"):
            EvidenceCheck(HostileString("root-folder.sonarr"), "verified", ("doctor.root-folder.sonarr",))
        with self.assertRaisesRegex(ValueError, "INVALID_CHECK_STATUS"):
            EvidenceCheck("root-folder.sonarr", HostileString("verified"), ("doctor.root-folder.sonarr",))

    def test_subclassed_models_and_invalid_inventory_state_are_rejected(self):
        class InventorySubclass(StackInventory):
            pass

        class CheckSubclass(EvidenceCheck):
            pass

        engine = DoctorEngine()
        valid_checks = checks()
        with self.assertRaisesRegex(ValueError, "INVALID_INVENTORY"):
            engine.diagnose(InventorySubclass(healthy_inventory().services, "healthy"), valid_checks)
        with self.assertRaisesRegex(ValueError, "INVALID_CHECK"):
            engine.diagnose(healthy_inventory(), (CheckSubclass(valid_checks[0].check_id, "verified", valid_checks[0].evidence_refs), *valid_checks[1:]))
        with self.assertRaisesRegex(ValueError, "INVALID_INVENTORY_STATE"):
            engine.diagnose(StackInventory(healthy_inventory().services, "private-state"), valid_checks)


if __name__ == "__main__":
    unittest.main()
