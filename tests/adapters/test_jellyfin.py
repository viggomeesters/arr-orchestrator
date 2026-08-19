from __future__ import annotations
import json, sys, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"src"))

from arr_orchestrator.adapters.jellyfin import JellyfinAdapter, JellyfinAdapterFailure
from arr_orchestrator.transport import TransportFailure

FIX=Path(__file__).parents[1]/"fixtures"/"jellyfin"
def fixture(name): return json.loads((FIX/name).read_text())

class FakeTransport:
    def __init__(self, data=None, failures=None): self.data=data or {}; self.failures=failures or {}; self.calls=[]
    def get_json(self,path):
        self.calls.append(("GET",path))
        if path in self.failures: raise self.failures[path]
        return self.data[path]
    def request_json(self,method,path):
        if method != "GET": raise TransportFailure("MUTATION_DISABLED")
        return self.get_json(path)
    def get_json_fields(self,path,fields):
        return {key:value for key,value in self.get_json(path).items() if key in fields}

class JellyfinAdapterTests(unittest.TestCase):
    def transport(self):
        return FakeTransport({
            "/System/Info/Public": fixture("system-info.json"),
            "/Library/VirtualFolders": fixture("virtual-folders.json"),
            "/ScheduledTasks": fixture("scheduled-tasks.json"),
        })

    def test_normalizes_health_libraries_and_refresh_without_private_identity(self):
        t=self.transport(); snapshot=JellyfinAdapter(t).read_snapshot()
        self.assertEqual("10.11.11",snapshot.capabilities.server_version)
        self.assertTrue(snapshot.health.healthy)
        self.assertTrue(snapshot.health.startup_complete)
        self.assertFalse(snapshot.health.detailed_status_supported)
        self.assertEqual(("/media/movies",),snapshot.libraries[0].locations)
        self.assertEqual("movies",snapshot.libraries[0].collection_type)
        self.assertEqual(("/media/tv","/media/archive/tv"),snapshot.libraries[1].locations)
        self.assertTrue(snapshot.libraries[1].refreshing)
        self.assertTrue(snapshot.refresh.supported)
        self.assertEqual("running",snapshot.refresh.state)
        self.assertEqual(42.5,snapshot.refresh.progress_percent)
        self.assertEqual([("GET","/System/Info/Public"),("GET","/Library/VirtualFolders"),("GET","/ScheduledTasks")],t.calls)
        public=repr(snapshot)
        for private in ("PRIVATE", "private-task-id", "private-server-id", "private-item-id", "ErrorMessage"):
            self.assertNotIn(private,public)

    def test_missing_refresh_task_is_explicitly_unsupported(self):
        t=self.transport(); t.data["/ScheduledTasks"]=[]
        refresh=JellyfinAdapter(t).read_snapshot().refresh
        self.assertFalse(refresh.supported); self.assertEqual("unsupported",refresh.state); self.assertIsNone(refresh.progress_percent)

    def test_maps_transport_failures_without_context(self):
        mapping={"AUTH_FAILED":"AUTH_FAILED","CREDENTIAL_INVALID":"CREDENTIAL_INVALID","RESOURCE_NOT_FOUND":"RESOURCE_NOT_FOUND","DEADLINE_EXCEEDED":"SERVICE_UNREACHABLE"}
        for source,expected in mapping.items():
            t=self.transport(); t.failures["/System/Info/Public"]=TransportFailure(source)
            with self.assertRaisesRegex(JellyfinAdapterFailure,f"^{expected}$") as caught: JellyfinAdapter(t).read_snapshot()
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

    def test_rejects_malformed_private_or_unbounded_responses(self):
        cases=[]
        t=self.transport(); t.data["/System/Info/Public"]={"Version":"10.11.11_.","StartupWizardCompleted":True}; cases.append(t)
        t=self.transport(); t.data["/System/Info/Public"]={"Version":"10.11.1٢","StartupWizardCompleted":True}; cases.append(t)
        t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":["relative/path"],"CollectionType":"movies"}]; cases.append(t)
        for path in ("/a/./b", "/a/.", "/a/b/", "/a/%2e%2e/b"):
            t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":[path],"CollectionType":"movies"}]; cases.append(t)
        t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":["/a\\b"],"CollectionType":"movies"}]; cases.append(t)
        t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":["/a"],"CollectionType":"movies"},{"Locations":["/a"],"CollectionType":"music"}]; cases.append(t)
        t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":["/media/movies"],"CollectionType":"   "}]; cases.append(t)
        t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":["/media/movies"],"CollectionType":"PRIVATE MEDIA TITLE"}]; cases.append(t)
        for collection_type in ("photos", "playlists", "Movies", "MOVIES"):
            t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":["/media/value"],"CollectionType":collection_type}]; cases.append(t)
        t=self.transport(); t.data["/ScheduledTasks"]=[{"Key":"RefreshLibrary","State":" Running ","CurrentProgressPercentage":5}]; cases.append(t)
        t=self.transport(); t.data["/ScheduledTasks"]=[{"Key":"RefreshLibrary","State":"Running","CurrentProgressPercentage":101}]; cases.append(t)
        t=self.transport(); t.data["/Library/VirtualFolders"]=[{"Locations":[],"CollectionType":"movies"}]*10001; cases.append(t)
        for t in cases:
            with self.assertRaises(JellyfinAdapterFailure): JellyfinAdapter(t).read_snapshot()

if __name__ == "__main__": unittest.main()
