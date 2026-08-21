from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.adapters.jellyfin import JellyfinAdapterFailure
from arr_orchestrator.adapters.prowlarr import ProwlarrAdapterFailure
from arr_orchestrator.adapters.qbittorrent import QbittorrentAdapterFailure
from arr_orchestrator.adapters.radarr import RadarrAdapterFailure
from arr_orchestrator.adapters.sonarr import SonarrAdapterFailure
from arr_orchestrator.inventory import REQUIRED_SERVICES, StackInventoryBuilder


PRIVATE = "PRIVATE-MEDIA-TITLE-DO-NOT-SERIALIZE"


class Reader:
    def __init__(self, snapshot=None, failure: Exception | None = None):
        self.snapshot = snapshot
        self.failure = failure
        self.calls = 0

    def read_snapshot(self):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.snapshot


class Failure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(PRIVATE)


class ProjectionBomb:
    @property
    def capabilities(self):
        raise Failure("AUTH_FAILED", retryable=True)


class HostileFailureMetadata(RuntimeError):
    @property
    def code(self):
        raise RuntimeError("hostile-code-property")


class HostileRetryableMetadata(RuntimeError):
    def __init__(self):
        self.code = "SERVICE_UNREACHABLE"
        super().__init__("hostile-retryable-property")

    @property
    def retryable(self):
        raise RuntimeError("hostile-retryable-property")


class HostileFailureCode(str):
    def __hash__(self):
        raise RuntimeError("hostile-string-hash")


HOSTILE_DICTIONARY_CALLS: list[str] = []


class HostileMetadataKey(str):
    def __hash__(self):
        return hash("code")

    def __eq__(self, other):
        raise RuntimeError("hostile-dict-key-equality")


class HostileDictionaryFailure(RuntimeError):
    @property
    def __dict__(self):
        HOSTILE_DICTIONARY_CALLS.append("executed")
        return {HostileMetadataKey("code"): "AUTH_FAILED"}


class PrivateSnapshot(NS):
    def to_dict(self):
        raise AssertionError("inventory must not serialize raw adapter snapshots")


def successful_readers(*, jellyfin_refresh_supported: bool = True):
    sonarr = PrivateSnapshot(
        capabilities=NS(api_version=3, application_version="4.0.15", resources=("system_status", "root_folders")),
        root_folders=(NS(accessible=True), NS(accessible=False)),
        download_clients=(NS(enabled=True), NS(enabled=False)),
        quality_profiles=(NS(), NS()),
        queue=NS(total_records=3),
        private=PRIVATE,
    )
    radarr = PrivateSnapshot(
        capabilities=NS(api_version=3, application_version="6.3.0", resources=("system_status", "root_folders")),
        root_folders=(NS(accessible=True),),
        download_clients=(NS(enabled=True),),
        quality_profiles=(NS(),),
        queue=NS(total_records=2),
        private=PRIVATE,
    )
    prowlarr = PrivateSnapshot(
        capabilities=NS(api_version=1, application_version="2.5.2", resources=("applications", "indexers")),
        applications=(NS(), NS()),
        indexers=NS(total=4, enabled=3, rss_capable=3, search_capable=2),
        private=PRIVATE,
    )
    qbittorrent = PrivateSnapshot(
        capabilities=NS(application_version="v5.2.3", webapi_version="2.15.1", resources=("categories", "queue")),
        categories=(NS(), NS()),
        queue=NS(total=7),
        private=PRIVATE,
    )
    jellyfin = PrivateSnapshot(
        capabilities=NS(server_version="10.11.11", resources=("health", "libraries", "refresh_status")),
        health=NS(healthy=True, startup_complete=True),
        libraries=(
            NS(collection_type="movies", locations=("/private/movies",), refreshing=False),
            NS(collection_type="tvshows", locations=("/private/tv", "/private/tv-2"), refreshing=True),
        ),
        refresh=NS(supported=jellyfin_refresh_supported, state="idle" if jellyfin_refresh_supported else "unsupported"),
        private=PRIVATE,
    )
    return {
        "sonarr": Reader(sonarr),
        "radarr": Reader(radarr),
        "prowlarr": Reader(prowlarr),
        "qbittorrent": Reader(qbittorrent),
        "jellyfin": Reader(jellyfin),
    }


class StackInventoryTests(unittest.TestCase):
    def test_all_five_services_form_a_privacy_safe_healthy_inventory(self):
        readers = successful_readers()

        inventory = StackInventoryBuilder(readers).read()
        payload = inventory.to_dict()
        encoded = json.dumps(payload, sort_keys=True)

        self.assertEqual("arr-orchestrator.stack-inventory.v1", payload["schema"])
        self.assertEqual("healthy", payload["state"])
        self.assertEqual(list(REQUIRED_SERVICES), [item["service"] for item in payload["services"]])
        self.assertTrue(all(item["state"] == "available" for item in payload["services"]))
        self.assertEqual(5, payload["summary"]["available"])
        self.assertEqual(2, payload["services"][0]["evidence"]["root_folder_count"])
        self.assertEqual(1, payload["services"][0]["evidence"]["inaccessible_root_folder_count"])
        self.assertEqual(3, payload["services"][4]["evidence"]["location_count"])
        self.assertEqual({"movies": 1, "tvshows": 1}, payload["services"][4]["evidence"]["collection_type_counts"])
        self.assertNotIn(PRIVATE, encoded)
        self.assertNotIn("/private/", encoded)
        self.assertNotIn("name", encoded.lower())
        self.assertTrue(all(reader.calls == 1 for reader in readers.values()))

    def test_failure_classes_preserve_uncertainty_without_leaking_exception_text(self):
        readers = successful_readers()
        readers["radarr"] = Reader(failure=RadarrAdapterFailure("SERVICE_UNREACHABLE", retryable=True))
        readers["prowlarr"] = Reader(failure=ProwlarrAdapterFailure("UNSUPPORTED_API_VERSION"))
        readers["qbittorrent"] = Reader(failure=QbittorrentAdapterFailure("AUTH_FAILED"))
        readers["jellyfin"] = Reader(failure=JellyfinAdapterFailure(PRIVATE))

        payload = StackInventoryBuilder(readers).read().to_dict()
        services = {item["service"]: item for item in payload["services"]}

        self.assertEqual("partial", payload["state"])
        self.assertEqual(("unreachable", "SERVICE_UNREACHABLE", True), (
            services["radarr"]["state"], services["radarr"]["failure_code"], services["radarr"]["retryable"]
        ))
        self.assertEqual("unsupported", services["prowlarr"]["state"])
        self.assertEqual("unknown", services["qbittorrent"]["state"])
        self.assertEqual("unknown", services["jellyfin"]["state"])
        self.assertEqual("INVENTORY_READ_FAILED", services["jellyfin"]["failure_code"])
        self.assertNotIn(PRIVATE, json.dumps(payload, sort_keys=True))

    def test_known_transport_failure_code_is_preserved_but_arbitrary_code_is_redacted(self):
        readers = successful_readers()
        readers["prowlarr"] = Reader(failure=ProwlarrAdapterFailure("CONTENT_TYPE_INVALID"))
        readers["radarr"] = Reader(failure=Failure("PRIVATE_FAILURE_CODE"))

        services = {
            item["service"]: item
            for item in StackInventoryBuilder(readers).read().to_dict()["services"]
        }

        self.assertEqual("CONTENT_TYPE_INVALID", services["prowlarr"]["failure_code"])
        self.assertEqual("INVENTORY_READ_FAILED", services["radarr"]["failure_code"])

    def test_hostile_exception_code_property_is_redacted_without_escaping(self):
        readers = successful_readers()
        readers["radarr"] = Reader(failure=HostileFailureMetadata("hostile-code-property"))

        payload = StackInventoryBuilder(readers).read().to_dict()
        service = {item["service"]: item for item in payload["services"]}["radarr"]

        self.assertEqual("unknown", service["state"])
        self.assertEqual("INVENTORY_READ_FAILED", service["failure_code"])
        self.assertFalse(service["retryable"])
        self.assertNotIn("hostile-code-property", json.dumps(payload, sort_keys=True))

    def test_hostile_exception_retryable_property_is_redacted_without_escaping(self):
        readers = successful_readers()
        readers["radarr"] = Reader(failure=HostileRetryableMetadata())

        payload = StackInventoryBuilder(readers).read().to_dict()
        service = {item["service"]: item for item in payload["services"]}["radarr"]

        self.assertEqual("unknown", service["state"])
        self.assertEqual("INVENTORY_READ_FAILED", service["failure_code"])
        self.assertFalse(service["retryable"])
        self.assertNotIn("hostile-retryable-property", json.dumps(payload, sort_keys=True))

    def test_hostile_string_subclass_failure_code_is_redacted_without_hashing(self):
        readers = successful_readers()
        readers["radarr"] = Reader(failure=RadarrAdapterFailure(HostileFailureCode("AUTH_FAILED")))

        payload = StackInventoryBuilder(readers).read().to_dict()
        service = {item["service"]: item for item in payload["services"]}["radarr"]

        self.assertEqual("unknown", service["state"])
        self.assertEqual("INVENTORY_READ_FAILED", service["failure_code"])
        self.assertFalse(service["retryable"])
        self.assertNotIn("hostile-string-hash", json.dumps(payload, sort_keys=True))

    def test_hostile_exception_dictionary_property_is_not_executed(self):
        readers = successful_readers()
        HOSTILE_DICTIONARY_CALLS.clear()
        readers["radarr"] = Reader(failure=HostileDictionaryFailure("hostile-dict-property"))

        payload = StackInventoryBuilder(readers).read().to_dict()
        service = {item["service"]: item for item in payload["services"]}["radarr"]

        self.assertEqual("unknown", service["state"])
        self.assertEqual("INVENTORY_READ_FAILED", service["failure_code"])
        self.assertFalse(service["retryable"])
        self.assertEqual([], HOSTILE_DICTIONARY_CALLS)
        self.assertNotIn("hostile-dict-property", json.dumps(payload, sort_keys=True))

    def test_projection_failures_cannot_impersonate_adapter_failures(self):
        readers = successful_readers()
        readers["sonarr"] = Reader(ProjectionBomb())

        service = StackInventoryBuilder(readers).read().to_dict()["services"][0]

        self.assertEqual("unknown", service["state"])
        self.assertEqual("INVENTORY_PROJECTION_FAILED", service["failure_code"])
        self.assertFalse(service["retryable"])

    def test_process_control_exceptions_are_not_swallowed(self):
        readers = successful_readers()
        readers["sonarr"] = Reader(failure=KeyboardInterrupt())

        with self.assertRaises(KeyboardInterrupt):
            StackInventoryBuilder(readers).read()

    def test_missing_readers_are_explicit_unknowns_and_never_all_green(self):
        payload = StackInventoryBuilder({"sonarr": successful_readers()["sonarr"]}).read().to_dict()

        self.assertEqual("partial", payload["state"])
        self.assertEqual(1, payload["summary"]["available"])
        self.assertEqual(4, payload["summary"]["unknown"])
        missing = [item for item in payload["services"] if item["service"] != "sonarr"]
        self.assertTrue(all(item["failure_code"] == "ADAPTER_NOT_CONFIGURED" for item in missing))

    def test_unsupported_subcapability_makes_service_and_stack_partial(self):
        payload = StackInventoryBuilder(successful_readers(jellyfin_refresh_supported=False)).read().to_dict()
        jellyfin = payload["services"][-1]

        self.assertEqual("partial", payload["state"])
        self.assertEqual("partial", jellyfin["state"])
        self.assertEqual(["refresh_status"], jellyfin["unsupported_resources"])
        self.assertEqual(4, payload["summary"]["available"])
        self.assertEqual(1, payload["summary"]["partial"])

    def test_inventory_is_immutable_and_json_is_deterministic(self):
        inventory = StackInventoryBuilder(successful_readers()).read()
        with self.assertRaises(FrozenInstanceError):
            inventory.state = "partial"
        self.assertEqual(inventory.to_json(), inventory.to_json())
        self.assertEqual(inventory.to_dict(), json.loads(inventory.to_json()))

    def test_rejects_unknown_reader_keys_instead_of_silently_ignoring_them(self):
        readers = successful_readers()
        readers["private-service"] = Reader(NS())
        with self.assertRaisesRegex(ValueError, "UNKNOWN_SERVICE"):
            StackInventoryBuilder(readers)


if __name__ == "__main__":
    unittest.main()
