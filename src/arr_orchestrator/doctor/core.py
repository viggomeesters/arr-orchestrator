from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from ..inventory import REQUIRED_SERVICES, ServiceInventory, StackInventory

_SCHEMA = "arr-orchestrator.doctor-report.v1"
_VERSION = re.compile(r"^v?[0-9][0-9A-Za-z]*(?:[._+-][0-9A-Za-z]+)*$")

_CHECK_SPECS = {
    "category.arr-to-qbittorrent": ("qbittorrent", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "application.prowlarr-to-sonarr": ("prowlarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "application.prowlarr-to-radarr": ("prowlarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "root-folder.sonarr": ("sonarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "root-folder.radarr": ("radarr", {"verified", "missing", "mismatch", "ambiguous", "unavailable"}),
    "container-path.shared-data": ("storage", {"verified", "mismatch", "ambiguous", "unavailable"}),
    "hardlink.downloads-to-media": ("storage", {"verified", "impossible", "ambiguous", "unavailable"}),
}

_CHECK_REFS = {check_id: (f"doctor.{check_id}",) for check_id in _CHECK_SPECS}
_SAFE_OWNERS = {"sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin", "storage"}
_SERVICE_RESOURCES = {
    "sonarr": frozenset({"system_status", "root_folders", "download_clients", "quality_profiles", "queue_summary"}),
    "radarr": frozenset({"system_status", "root_folders", "download_clients", "quality_profiles", "queue_summary"}),
    "prowlarr": frozenset({"applications", "indexers", "system_status"}),
    "qbittorrent": frozenset({"categories", "queue"}),
    "jellyfin": frozenset({"health", "libraries", "refresh_status"}),
}
_FAILURE_CODES = {
    "unreachable": frozenset({"SERVICE_UNREACHABLE", "DEADLINE_EXCEEDED", "TLS_VERIFICATION_FAILED"}),
    "unsupported": frozenset({"UNSUPPORTED_API_VERSION", "UNSUPPORTED_CAPABILITY"}),
    "unknown": frozenset({
        "ADAPTER_NOT_CONFIGURED",
        "AUTH_FAILED",
        "CONTENT_TYPE_INVALID",
        "CREDENTIAL_INVALID",
        "FIELD_PROJECTION_REQUIRED",
        "HTTP_STATUS_INVALID",
        "INVENTORY_PROJECTION_FAILED",
        "INVENTORY_READ_FAILED",
        "JSON_INVALID",
        "MUTATION_DISABLED",
        "ORIGIN_DENIED",
        "PATH_INVALID",
        "PRIVATE_DATA_REDACTED",
        "QUEUE_BOUNDS_INVALID",
        "QUEUE_PAGINATION_INVALID",
        "REDIRECT_DENIED",
        "RESOURCE_NOT_FOUND",
        "RESPONSE_BOUNDS_INVALID",
        "RESPONSE_INVALID",
        "RESPONSE_SHAPE_INVALID",
        "RESPONSE_TOO_LARGE",
        "SERVICE_IDENTITY_INVALID",
        "TEXT_INVALID",
    }),
}
_SAFE_EVIDENCE_REFS = {
    *(reference for references in _CHECK_REFS.values() for reference in references),
    *(
        f"inventory.{service}.{suffix}"
        for service in REQUIRED_SERVICES
        for suffix in (
            "state",
            "api-version",
            "capabilities",
            "evidence",
            "root-folders",
            "download-clients",
            "quality-profiles",
            "applications",
            "categories",
            "health",
        )
    ),
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

    def __post_init__(self) -> None:
        _validate_check(self)


def _validate_check(check: EvidenceCheck) -> None:
    if type(check) is not EvidenceCheck:
        raise ValueError("INVALID_CHECK")
    check_id = check.check_id
    status = check.status
    if type(check_id) is not str:
        raise ValueError("INVALID_CHECK_ID")
    if type(status) is not str:
        raise ValueError("INVALID_CHECK_STATUS")
    spec = _CHECK_SPECS.get(check_id)
    if spec is None:
        raise ValueError("UNKNOWN_CHECK")
    if status not in spec[1]:
        raise ValueError("INVALID_CHECK_STATUS")


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: str
    owner: str
    evidence_refs: tuple[str, ...]
    explanation: str
    remediation: str

    def __post_init__(self) -> None:
        _validate_finding(self)

    def to_dict(self) -> dict[str, object]:
        _validate_finding(self)
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

    def __post_init__(self) -> None:
        _validate_report(self)

    def to_dict(self) -> dict[str, object]:
        _validate_report(self)
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


def _validate_finding(finding: Finding) -> None:
    if type(finding) is not Finding:
        raise ValueError("INVALID_FINDING")
    if any(
        type(value) is not str
        for value in (
            finding.code,
            finding.severity,
            finding.owner,
            finding.explanation,
            finding.remediation,
        )
    ):
        raise ValueError("INVALID_FINDING")
    text = _FINDING_TEXT.get(finding.code)
    if (
        text is None
        or finding.severity != "blocker"
        or finding.owner not in _SAFE_OWNERS
        or (finding.explanation, finding.remediation) != text
        or type(finding.evidence_refs) is not tuple
        or not finding.evidence_refs
        or any(
            type(item) is not str or item not in _SAFE_EVIDENCE_REFS
            for item in finding.evidence_refs
        )
        or tuple(sorted(set(finding.evidence_refs))) != finding.evidence_refs
        or not _finding_shape_is_registered(finding)
    ):
        raise ValueError("INVALID_FINDING")


def _finding_shape_is_registered(finding: Finding) -> bool:
    for check_id, (owner, statuses) in _CHECK_SPECS.items():
        if (
            finding.owner == owner
            and finding.evidence_refs == _CHECK_REFS[check_id]
            and finding.code
            in {_check_code(check_id, status) for status in statuses if status != "verified"}
        ):
            return True

    service_shapes = {
        "SERVICE_UNREACHABLE": "state",
        "SERVICE_STATE_UNKNOWN": "state",
        "API_VERSION_UNSUPPORTED": "api-version",
        "SERVICE_CAPABILITY_PARTIAL": "capabilities",
        "SERVICE_EVIDENCE_UNAVAILABLE": "evidence",
    }
    suffix = service_shapes.get(finding.code)
    if suffix is not None:
        return finding.evidence_refs == _service_ref(finding.owner, suffix)
    exact_service_shapes = {
        "ROOT_FOLDER_MISSING": ({"sonarr", "radarr"}, "root-folders"),
        "ROOT_FOLDER_INACCESSIBLE": ({"sonarr", "radarr"}, "root-folders"),
        "DOWNLOAD_CLIENT_MISSING": ({"sonarr", "radarr"}, "download-clients"),
        "QUALITY_PROFILE_MISSING": ({"sonarr", "radarr"}, "quality-profiles"),
        "APPLICATION_LINK_MISSING": ({"prowlarr"}, "applications"),
        "CATEGORY_MISSING": ({"qbittorrent"}, "categories"),
        "MEDIA_SERVER_UNHEALTHY": ({"jellyfin"}, "health"),
    }
    shape = exact_service_shapes.get(finding.code)
    return bool(
        shape
        and finding.owner in shape[0]
        and finding.evidence_refs == _service_ref(finding.owner, shape[1])
    )


def _validate_report(report: DoctorReport) -> None:
    if (
        type(report) is not DoctorReport
        or type(report.schema) is not str
        or report.schema != _SCHEMA
        or type(report.state) is not str
        or report.state not in {"healthy", "degraded", "blocked"}
        or type(report.findings) is not tuple
    ):
        raise ValueError("INVALID_REPORT")
    for finding in report.findings:
        _validate_finding(finding)
    ordered = tuple(sorted(report.findings, key=lambda item: (item.code, item.owner, item.evidence_refs)))
    if report.findings != ordered:
        raise ValueError("INVALID_REPORT")
    expected_state = (
        "blocked"
        if any(item.severity == "blocker" for item in report.findings)
        else "degraded"
        if report.findings
        else "healthy"
    )
    if report.state != expected_state:
        raise ValueError("INVALID_REPORT")


def _finding(code: str, owner: str, evidence_refs: tuple[str, ...], severity: str = "blocker") -> Finding:
    explanation, remediation = _FINDING_TEXT[code]
    return Finding(code, severity, owner, evidence_refs, explanation, remediation)


def _service_ref(service: str, suffix: str = "state") -> tuple[str, ...]:
    return (f"inventory.{service}.{suffix}",)


def _evidence_map(service: ServiceInventory) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in service.evidence:
        if key in result:
            raise ValueError("INVALID_SERVICE_EVIDENCE")
        result[key] = value
    return result


def _validate_service_state_payload(service: ServiceInventory) -> None:
    if (
        type(service.version) not in {str, type(None)}
        or type(service.api_version) not in {int, type(None)}
        or type(service.api_version) is bool
        or type(service.resources) is not tuple
        or type(service.unsupported_resources) is not tuple
        or any(type(item) is not str for item in service.resources)
        or any(type(item) is not str for item in service.unsupported_resources)
        or len(set(service.resources)) != len(service.resources)
        or len(set(service.unsupported_resources)) != len(service.unsupported_resources)
        or bool(set(service.resources) & set(service.unsupported_resources))
        or not set(service.resources).issubset(_SERVICE_RESOURCES[service.service])
        or not set(service.unsupported_resources).issubset(_SERVICE_RESOURCES[service.service])
        or type(service.failure_code) not in {str, type(None)}
        or type(service.retryable) is not bool
    ):
        raise ValueError("INCONSISTENT_SERVICE_STATE")
    if service.state in {"available", "partial"} and (
        type(service.version) is not str
        or _VERSION.fullmatch(service.version) is None
        or (
            service.service in {"sonarr", "radarr", "prowlarr"}
            and (type(service.api_version) is not int or service.api_version < 1)
        )
        or (
            service.service in {"qbittorrent", "jellyfin"}
            and service.api_version is not None
        )
    ):
        raise ValueError("INCONSISTENT_SERVICE_STATE")
    if service.state == "available":
        valid = (
            not service.unsupported_resources
            and service.failure_code is None
            and service.retryable is False
        )
    elif service.state == "partial":
        valid = (
            bool(service.unsupported_resources)
            and service.failure_code is None
            and service.retryable is False
        )
    else:
        valid = (
            service.version is None
            and service.api_version is None
            and not service.resources
            and not service.unsupported_resources
            and not service.evidence
            and service.failure_code in _FAILURE_CODES[service.state]
            and (service.state == "unreachable" or service.retryable is False)
        )
    if not valid:
        raise ValueError("INCONSISTENT_SERVICE_STATE")


def _validate_service_evidence(service: ServiceInventory) -> None:
    evidence = _evidence_map(service)
    if service.service in {"sonarr", "radarr"}:
        keys = (
            "root_folder_count",
            "inaccessible_root_folder_count",
            "download_client_count",
            "enabled_download_client_count",
            "quality_profile_count",
        )
        values = {key: evidence.get(key) for key in keys}
        if any(value is not None and (type(value) is not int or value < 0) for value in values.values()):
            raise ValueError("INVALID_SERVICE_EVIDENCE")
        if (
            type(values["root_folder_count"]) is int
            and type(values["inaccessible_root_folder_count"]) is int
            and values["inaccessible_root_folder_count"] > values["root_folder_count"]
        ):
            raise ValueError("INVALID_SERVICE_EVIDENCE")
        if (
            type(values["download_client_count"]) is int
            and type(values["enabled_download_client_count"]) is int
            and values["enabled_download_client_count"] > values["download_client_count"]
        ):
            raise ValueError("INVALID_SERVICE_EVIDENCE")
    elif service.service == "prowlarr":
        for key in (
            "application_count",
            "indexer_total",
            "indexer_enabled",
            "indexer_rss_capable",
            "indexer_search_capable",
        ):
            value = evidence.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("INVALID_SERVICE_EVIDENCE")
        total = evidence.get("indexer_total")
        if type(total) is int and any(
            type(evidence.get(key)) is int and evidence[key] > total
            for key in ("indexer_enabled", "indexer_rss_capable", "indexer_search_capable")
        ):
            raise ValueError("INVALID_SERVICE_EVIDENCE")


def _stack_state(services: tuple[ServiceInventory, ...]) -> str:
    states = {service.state for service in services}
    if states == {"available"}:
        return "healthy"
    if states == {"unknown"}:
        return "unknown"
    if states == {"unreachable"}:
        return "unreachable"
    if states == {"unsupported"}:
        return "unsupported"
    return "partial"


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
            _validate_service_state_payload(service)
            _validate_service_evidence(service)
        if len(inventory.services) != len(REQUIRED_SERVICES):
            raise ValueError("INVALID_INVENTORY_SERVICES")
        if inventory.state != _stack_state(inventory.services):
            raise ValueError("INCONSISTENT_INVENTORY_STATE")
        supplied: dict[str, EvidenceCheck] = {}
        for check in checks:
            _validate_check(check)
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
                findings.append(_finding(_check_code(check_id, check.status), owner, _CHECK_REFS[check_id]))

        ordered = tuple(sorted(findings, key=lambda item: (item.code, item.owner, item.evidence_refs)))
        state = "blocked" if any(item.severity == "blocker" for item in ordered) else "degraded" if ordered else "healthy"
        return DoctorReport(state, ordered)
