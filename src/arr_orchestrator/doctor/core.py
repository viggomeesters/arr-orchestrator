from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from ..inventory import REQUIRED_SERVICES, ServiceInventory, StackInventory

_SCHEMA = "arr-orchestrator.doctor-report.v1"
_REF = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")

_CHECK_SPECS = {
    "category.arr-to-qbittorrent": ("qbittorrent", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "application.prowlarr-to-sonarr": ("prowlarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "application.prowlarr-to-radarr": ("prowlarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "root-folder.sonarr": ("sonarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "root-folder.radarr": ("radarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "container-path.shared-data": ("storage", {"verified", "mismatch", "ambiguous", "unavailable"}),
    "hardlink.downloads-to-media": ("storage", {"verified", "impossible", "ambiguous", "unavailable"}),
}

_FINDING_TEXT = {
    "APPLICATION_LINK_AMBIGUOUS": (
        "Application-link identity is ambiguous.",
        "Remove duplicate or unowned application links, then repeat readback.",
    ),
    "APPLICATION_LINK_MISMATCH": (
        "An application link targets the wrong service contract.",
        "Correct the application endpoint and synchronization mode, then verify it through the service API.",
    ),
    "APPLICATION_LINK_MISSING": (
        "A required Prowlarr application link is missing.",
        "Create the missing application link with the approved endpoint and read it back.",
    ),
    "APPLICATION_LINK_EVIDENCE_UNAVAILABLE": (
        "Application-link evidence is unavailable.",
        "Restore read-only application discovery before planning changes.",
    ),
    "API_VERSION_UNSUPPORTED": (
        "A service exposes an unsupported API version.",
        "Use a supported service version or add a reviewed adapter capability before applying changes.",
    ),
    "CATEGORY_AMBIGUOUS": (
        "Download-category ownership is ambiguous.",
        "Remove duplicate or unowned categories and repeat category readback.",
    ),
    "CATEGORY_MISMATCH": (
        "The Arr download category resolves to an unexpected destination.",
        "Correct the category destination and verify the exact normalized relationship.",
    ),
    "CATEGORY_MISSING": (
        "The required Arr download category is missing.",
        "Create the category with the approved destination and read it back.",
    ),
    "CATEGORY_EVIDENCE_UNAVAILABLE": (
        "Download-category evidence is unavailable.",
        "Restore read-only category discovery before planning changes.",
    ),
    "CONTAINER_PATH_AMBIGUOUS": (
        "Container path identity is ambiguous.",
        "Provide one authoritative mount identity for downloads and media before applying changes.",
    ),
    "CONTAINER_PATH_MISMATCH": (
        "Container paths do not resolve to the expected shared storage identity.",
        "Align the container mounts or add an explicit reviewed mapping, then re-run path verification.",
    ),
    "CONTAINER_PATH_EVIDENCE_UNAVAILABLE": (
        "Container path evidence is unavailable.",
        "Collect mount identity evidence from the owning runtime before planning changes.",
    ),
    "DOWNLOAD_CLIENT_MISSING": (
        "No enabled download client is available to an Arr service.",
        "Configure and enable the approved download client, then verify it through the service API.",
    ),
    "HARDLINK_AMBIGUOUS": (
        "Hardlink feasibility evidence is ambiguous.",
        "Repeat the filesystem probe against one authoritative downloads-to-media path pair.",
    ),
    "HARDLINK_IMPOSSIBLE": (
        "Downloads and media cannot be hardlinked.",
        "Place downloads and media on a compatible filesystem and mount topology, or explicitly accept copy-based imports.",
    ),
    "HARDLINK_EVIDENCE_UNAVAILABLE": (
        "Hardlink feasibility has not been proven.",
        "Run a bounded create-link-readback-cleanup probe on the target filesystem.",
    ),
    "MEDIA_SERVER_UNHEALTHY": (
        "The media server is not healthy and startup-complete.",
        "Restore media-server health and repeat read-only discovery.",
    ),
    "QUALITY_PROFILE_MISSING": (
        "No quality profile is available to an Arr service.",
        "Configure at least one approved quality profile and read it back.",
    ),
    "ROOT_FOLDER_AMBIGUOUS": (
        "Root-folder identity is ambiguous.",
        "Remove duplicate or unowned root folders and repeat readback.",
    ),
    "ROOT_FOLDER_INACCESSIBLE": (
        "A configured root folder is inaccessible.",
        "Repair the mount or permissions and verify accessibility from the owning service.",
    ),
    "ROOT_FOLDER_MISMATCH": (
        "A root folder resolves to the wrong storage relationship.",
        "Correct the root-folder relationship and verify its normalized identity.",
    ),
    "ROOT_FOLDER_MISSING": (
        "A required root folder is missing.",
        "Create the approved root folder and verify accessibility through the service API.",
    ),
    "ROOT_FOLDER_EVIDENCE_UNAVAILABLE": (
        "Root-folder evidence is unavailable.",
        "Restore read-only root-folder discovery before planning changes.",
    ),
    "SERVICE_CAPABILITY_PARTIAL": (
        "A service exposes only part of the required read-only capability set.",
        "Resolve the unsupported capability or explicitly accept the limitation before planning changes.",
    ),
    "SERVICE_EVIDENCE_UNAVAILABLE": (
        "Required normalized service evidence is unavailable.",
        "Restore complete adapter readback before planning changes.",
    ),
    "SERVICE_STATE_UNKNOWN": (
        "Service state could not be classified safely.",
        "Restore trusted read-only discovery and repeat inventory.",
    ),
    "SERVICE_UNREACHABLE": (
        "A required service is unreachable.",
        "Restore connectivity and authentication, then repeat read-only inventory.",
    ),
}


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    check_id: str
    status: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.check_id) is not str:
            raise ValueError("INVALID_CHECK_ID")
        if type(self.status) is not str:
            raise ValueError("INVALID_CHECK_STATUS")
        spec = _CHECK_SPECS.get(self.check_id)
        if spec is None:
            raise ValueError("UNKNOWN_CHECK")
        if self.status not in spec[1]:
            raise ValueError("INVALID_CHECK_STATUS")
        if (
            type(self.evidence_refs) is not tuple
            or not self.evidence_refs
            or len(self.evidence_refs) > 8
            or any(type(item) is not str or _REF.fullmatch(item) is None for item in self.evidence_refs)
        ):
            raise ValueError("INVALID_EVIDENCE_REF")


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: str
    owner: str
    evidence_refs: tuple[str, ...]
    explanation: str
    remediation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "owner": self.owner,
            "evidence_refs": list(self.evidence_refs),
            "explanation": self.explanation,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class DoctorReport:
    state: str
    findings: tuple[Finding, ...]
    schema: str = _SCHEMA

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "state": self.state,
            "summary": {
                "blocker": sum(item.severity == "blocker" for item in self.findings),
                "warning": sum(item.severity == "warning" for item in self.findings),
            },
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _finding(code: str, owner: str, evidence_refs: tuple[str, ...], severity: str = "blocker") -> Finding:
    explanation, remediation = _FINDING_TEXT[code]
    return Finding(code, severity, owner, evidence_refs, explanation, remediation)


def _service_ref(service: str, suffix: str = "state") -> tuple[str, ...]:
    return (f"inventory.{service}.{suffix}",)


def _evidence_map(service: ServiceInventory) -> dict[str, object]:
    return {key: value for key, value in service.evidence if type(key) is str}


def _service_findings(service: ServiceInventory) -> Iterable[Finding]:
    if service.state == "unreachable":
        yield _finding("SERVICE_UNREACHABLE", service.service, _service_ref(service.service))
        return
    if service.state == "unsupported":
        yield _finding("API_VERSION_UNSUPPORTED", service.service, _service_ref(service.service, "api-version"))
        return
    if service.state == "unknown":
        yield _finding("SERVICE_STATE_UNKNOWN", service.service, _service_ref(service.service))
        return
    if service.state == "partial":
        yield _finding("SERVICE_CAPABILITY_PARTIAL", service.service, _service_ref(service.service, "capabilities"))

    evidence = _evidence_map(service)
    if service.service in {"sonarr", "radarr"}:
        required = (
            "root_folder_count",
            "inaccessible_root_folder_count",
            "download_client_count",
            "enabled_download_client_count",
            "quality_profile_count",
        )
        if any(type(evidence.get(key)) is not int or int(evidence[key]) < 0 for key in required):
            yield _finding("SERVICE_EVIDENCE_UNAVAILABLE", service.service, _service_ref(service.service, "evidence"))
            return
        if evidence["root_folder_count"] == 0:
            yield _finding("ROOT_FOLDER_MISSING", service.service, _service_ref(service.service, "root-folders"))
        elif evidence["inaccessible_root_folder_count"] > 0:
            yield _finding("ROOT_FOLDER_INACCESSIBLE", service.service, _service_ref(service.service, "root-folders"))
        if evidence["download_client_count"] == 0 or evidence["enabled_download_client_count"] == 0:
            yield _finding("DOWNLOAD_CLIENT_MISSING", service.service, _service_ref(service.service, "download-clients"))
        if evidence["quality_profile_count"] == 0:
            yield _finding("QUALITY_PROFILE_MISSING", service.service, _service_ref(service.service, "quality-profiles"))
    elif service.service == "prowlarr":
        count = evidence.get("application_count")
        if type(count) is not int:
            yield _finding("SERVICE_EVIDENCE_UNAVAILABLE", service.service, _service_ref(service.service, "evidence"))
        elif count < 2:
            yield _finding("APPLICATION_LINK_MISSING", service.service, _service_ref(service.service, "applications"))
    elif service.service == "qbittorrent":
        count = evidence.get("category_count")
        if type(count) is not int:
            yield _finding("SERVICE_EVIDENCE_UNAVAILABLE", service.service, _service_ref(service.service, "evidence"))
        elif count < 1:
            yield _finding("CATEGORY_MISSING", service.service, _service_ref(service.service, "categories"))
    elif service.service == "jellyfin":
        healthy = evidence.get("healthy")
        startup = evidence.get("startup_complete")
        if type(healthy) is not bool or type(startup) is not bool:
            yield _finding("SERVICE_EVIDENCE_UNAVAILABLE", service.service, _service_ref(service.service, "evidence"))
        elif not healthy or not startup:
            yield _finding("MEDIA_SERVER_UNHEALTHY", service.service, _service_ref(service.service, "health"))


def _check_code(check_id: str, status: str) -> str:
    prefix = check_id.split(".", 1)[0]
    stem = {
        "category": "CATEGORY",
        "application": "APPLICATION_LINK",
        "root-folder": "ROOT_FOLDER",
        "container-path": "CONTAINER_PATH",
        "hardlink": "HARDLINK",
    }[prefix]
    suffix = {
        "missing": "MISSING",
        "mismatch": "MISMATCH",
        "ambiguous": "AMBIGUOUS",
        "unavailable": "EVIDENCE_UNAVAILABLE",
        "impossible": "IMPOSSIBLE",
    }[status]
    return f"{stem}_{suffix}"


class DoctorEngine:
    def diagnose(self, inventory: StackInventory, checks: Iterable[EvidenceCheck]) -> DoctorReport:
        if type(inventory) is not StackInventory or type(inventory.services) is not tuple:
            raise ValueError("INVALID_INVENTORY")
        if type(inventory.state) is not str or inventory.state not in {
            "healthy", "partial", "unreachable", "unsupported", "unknown"
        }:
            raise ValueError("INVALID_INVENTORY_STATE")
        for service, expected in zip(inventory.services, REQUIRED_SERVICES, strict=False):
            if type(service) is not ServiceInventory:
                raise ValueError("INVALID_SERVICE_INVENTORY")
            if type(service.service) is not str or service.service != expected:
                raise ValueError("INVALID_INVENTORY_SERVICES")
            if type(service.state) is not str or service.state not in {
                "available", "partial", "unreachable", "unsupported", "unknown"
            }:
                raise ValueError("INVALID_SERVICE_STATE")
            if type(service.evidence) is not tuple or any(
                type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                for item in service.evidence
            ):
                raise ValueError("INVALID_SERVICE_EVIDENCE")
        if len(inventory.services) != len(REQUIRED_SERVICES):
            raise ValueError("INVALID_INVENTORY_SERVICES")
        supplied: dict[str, EvidenceCheck] = {}
        for check in checks:
            if type(check) is not EvidenceCheck:
                raise ValueError("INVALID_CHECK")
            if check.check_id in supplied:
                raise ValueError("DUPLICATE_CHECK")
            supplied[check.check_id] = check

        findings: list[Finding] = []
        for service in inventory.services:
            findings.extend(_service_findings(service))
        for check_id, (owner, _statuses) in _CHECK_SPECS.items():
            check = supplied.get(check_id)
            if check is None:
                findings.append(
                    _finding(
                        _check_code(check_id, "unavailable"),
                        owner,
                        (f"doctor.{check_id}",),
                    )
                )
            elif check.status != "verified":
                findings.append(_finding(_check_code(check_id, check.status), owner, check.evidence_refs))

        ordered = tuple(sorted(findings, key=lambda item: (item.code, item.owner, item.evidence_refs)))
        state = "blocked" if any(item.severity == "blocker" for item in ordered) else "degraded" if ordered else "healthy"
        return DoctorReport(state, ordered)
