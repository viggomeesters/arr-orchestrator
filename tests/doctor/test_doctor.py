from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.doctor import DoctorEngine, DoctorReport, EvidenceCheck, Finding
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
        EvidenceCheck(check_id, statuses[check_id])
        for check_id in sorted(statuses)
    )


class DoctorRuleTests(unittest.TestCase):
    def test_healthy_stack_has_no_findings_and_deterministic_json(self):
        report = DoctorEngine().diagnose(healthy_inventory(), checks())

        self.assertEqual("healthy", report.state)
        self.assertEqual((), report.findings)
        self.assertEqual(report.to_json(), report.to_json())
        self.assertEqual(report.to_json(), DoctorEngine().diagnose(healthy_inventory(), reversed(checks())).to_json())
        self.assertEqual(report.to_dict(), json.loads(report.to_json()))

    def test_service_state_payload_combinations_fail_closed(self):
        valid = healthy_inventory().services[0]
        invalid_available = replace(
            valid,
            failure_code="SERVICE_UNREACHABLE",
            retryable=True,
        )
        invalid_available_unsupported = replace(
            valid,
            unsupported_resources=("queue_summary",),
        )
        invalid_available_version = replace(valid, version=None)
        invalid_available_empty_version = replace(valid, version="")
        invalid_available_whitespace_version = replace(valid, version="   ")
        invalid_available_api = replace(valid, api_version=None)
        invalid_partial = replace(
            valid,
            state="partial",
            unsupported_resources=(),
        )
        invalid_partial_overlap = replace(
            valid,
            state="partial",
            unsupported_resources=("system_status",),
        )
        invalid_failure = replace(
            valid,
            state="unreachable",
            failure_code="SERVICE_UNREACHABLE",
        )
        invalid_failure_code = replace(
            valid,
            state="unsupported",
            version=None,
            api_version=None,
            resources=(),
            evidence=(),
            failure_code="AUTH_FAILED",
        )
        for name, row in (
            ("available-failure", invalid_available),
            ("available-unsupported", invalid_available_unsupported),
            ("available-version", invalid_available_version),
            ("available-empty-version", invalid_available_empty_version),
            ("available-whitespace-version", invalid_available_whitespace_version),
            ("available-api-version", invalid_available_api),
            ("partial-without-unsupported", invalid_partial),
            ("partial-resource-overlap", invalid_partial_overlap),
            ("failure-with-snapshot", invalid_failure),
            ("failure-code-mismatch", invalid_failure_code),
        ):
            with self.subTest(case=name):
                services = list(healthy_inventory().services)
                services[0] = row
                stack_state = "healthy" if row.state == "available" else "partial"
                inventory = StackInventory(tuple(services), stack_state)
                with self.assertRaisesRegex(ValueError, "INCONSISTENT_SERVICE_STATE"):
                    DoctorEngine().diagnose(inventory, checks())

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

    def test_unknown_check_ids_are_rejected_and_references_are_not_caller_controlled(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_CHECK"):
            EvidenceCheck("private.check", "verified")
        with self.assertRaises(TypeError):
            EvidenceCheck("root-folder.sonarr", "verified", ("secret.private-provider-canary",))

    def test_hostile_string_subclasses_are_rejected_before_hashing(self):
        class HostileString(str):
            def __hash__(self):
                raise RuntimeError("PRIVATE-HASH")

            def __eq__(self, other):
                raise RuntimeError("PRIVATE-EQUALITY")

        with self.assertRaisesRegex(ValueError, "INVALID_CHECK_ID"):
            EvidenceCheck(HostileString("root-folder.sonarr"), "verified")
        with self.assertRaisesRegex(ValueError, "INVALID_CHECK_STATUS"):
            EvidenceCheck("root-folder.sonarr", HostileString("verified"))

    def test_hostile_post_construction_check_mutation_is_revalidated_before_hashing(self):
        class HostileString(str):
            def __hash__(self):
                raise RuntimeError("PRIVATE-HASH")

            def __eq__(self, other):
                raise RuntimeError("PRIVATE-EQUALITY")

        item = EvidenceCheck("root-folder.sonarr", "verified")
        object.__setattr__(item, "check_id", HostileString("root-folder.sonarr"))
        with self.assertRaisesRegex(ValueError, "INVALID_CHECK_ID"):
            DoctorEngine().diagnose(healthy_inventory(), (item, *checks()[1:]))

    def test_stack_state_must_match_service_state_reduction(self):
        for state in ("partial", "unreachable", "unsupported", "unknown"):
            with self.subTest(state=state), self.assertRaisesRegex(
                ValueError, "INCONSISTENT_INVENTORY_STATE"
            ):
                DoctorEngine().diagnose(StackInventory(healthy_inventory().services, state), checks())

    def test_duplicate_or_contradictory_service_evidence_fails_closed(self):
        inventory = healthy_inventory()
        base = inventory.services[0]
        invalid_evidence = (
            ("root_folder_count", 1),
            ("root_folder_count", 2),
            ("inaccessible_root_folder_count", 0),
            ("download_client_count", 1),
            ("enabled_download_client_count", 2),
            ("quality_profile_count", 1),
        )
        rows = (ServiceInventory(
            base.service,
            base.state,
            version=base.version,
            api_version=base.api_version,
            resources=base.resources,
            evidence=invalid_evidence,
        ), *inventory.services[1:])
        with self.assertRaisesRegex(ValueError, "INVALID_SERVICE_EVIDENCE"):
            DoctorEngine().diagnose(StackInventory(rows, "healthy"), checks())

        contradictory = tuple(
            (key, 2 if key == "enabled_download_client_count" else value)
            for key, value in base.evidence
        )
        rows = (ServiceInventory(
            base.service,
            base.state,
            version=base.version,
            api_version=base.api_version,
            resources=base.resources,
            evidence=contradictory,
        ), *inventory.services[1:])
        with self.assertRaisesRegex(ValueError, "INVALID_SERVICE_EVIDENCE"):
            DoctorEngine().diagnose(StackInventory(rows, "healthy"), checks())

    def test_public_report_models_reject_caller_controlled_text_and_schema(self):
        with self.assertRaisesRegex(ValueError, "INVALID_FINDING"):
            Finding(
                "CATEGORY_MISMATCH",
                "blocker",
                "qbittorrent",
                ("secret.private-provider-canary",),
                "PRIVATE EXPLANATION",
                "PRIVATE REMEDIATION",
            )
        with self.assertRaisesRegex(ValueError, "INVALID_REPORT"):
            DoctorReport("healthy", (), "private.schema")

        valid = DoctorEngine().diagnose(
            healthy_inventory(), checks(**{"category.arr-to-qbittorrent": "mismatch"})
        ).findings[0]
        with self.assertRaisesRegex(ValueError, "INVALID_FINDING"):
            Finding(
                valid.code,
                valid.severity,
                "jellyfin",
                valid.evidence_refs,
                valid.explanation,
                valid.remediation,
            )

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
            engine.diagnose(healthy_inventory(), (CheckSubclass(valid_checks[0].check_id, "verified"), *valid_checks[1:]))
        with self.assertRaisesRegex(ValueError, "INVALID_INVENTORY_STATE"):
            engine.diagnose(StackInventory(healthy_inventory().services, "private-state"), valid_checks)


if __name__ == "__main__":
    unittest.main()
