"""Project bundles round-trip source/data/settings without executing source."""

import hashlib
import io
import json
import sqlite3
import stat
import zipfile
from contextlib import closing
from unittest.mock import patch

from reqlica.projects.store import get_project, registry_directory
from reqlica.projects.transfer import export_project, import_project
from reqlica.projects.versions import code_directory, content_digest
from test_scenarios import ScenarioFixture


def rewrite_bundle(data, mutate):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {name:archive.read(name) for name in archive.namelist()}
    mutate(files)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return result.getvalue()


class TransferTests(ScenarioFixture):
    def setUp(self):
        super().setUp()
        self.target = self.root / "imported"
        with closing(sqlite3.connect(self.project.directory / "data.sqlite")) as db, db:
            db.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO records VALUES(1, 'saved data')")

    def test_roundtrip_preserves_code_data_auth_rules_and_baseline(self):
        self.configure({"method":"GET", "path":"/hello", "response":"failure", "failure_count":2})
        auth = f"/api/projects/{self.project.id}/mock-auth"
        revision = self.console.get(auth).json()["revision"]
        self.console.put(auth, json={"expected_revision":revision, "mode":"bearer", "credentials":[{"name":"test", "value":"test-token"}]})
        baseline_url = f"/api/projects/{self.project.id}/data-baseline"
        revision = self.console.get(baseline_url).json()["revision"]
        self.assertEqual(self.console.put(baseline_url, json={"expected_revision":revision,"confirm":True}).status_code, 200)
        self.gateway.get(self.mock + "/hello", headers={"Authorization":"Bearer test-token"})
        result = import_project(export_project(self.project.id), self.target, clone=True, name="Copy")
        project = get_project(result["project"]["id"])
        self.assertNotEqual(project.id, self.project.id)
        self.assertEqual(project.name, "Copy")
        self.assertEqual(content_digest(code_directory(project)), content_digest(code_directory(self.project)))
        with closing(sqlite3.connect(project.directory / "data.sqlite")) as db:
            self.assertEqual(db.execute("SELECT * FROM records").fetchall(), [(1,"saved data")])
        imported_mock = f"/mock/{project.id}/hello"
        self.assertEqual(self.gateway.get(imported_mock).status_code, 401)
        for _ in range(2):
            self.assertEqual(self.gateway.get(imported_mock, headers={"Authorization":"Bearer test-token"}).status_code, 503)
        self.assertEqual(self.gateway.get(imported_mock, headers={"Authorization":"Bearer test-token"}).status_code, 200)
        self.assertIsNotNone(self.console.get(f"/api/projects/{project.id}/data-baseline").json()["baseline"])

    def test_preserve_id_collision_and_no_existing_destination_overwrite(self):
        data = export_project(self.project.id)
        with self.assertRaisesRegex(ValueError, "already registered"):
            import_project(data, self.target)
        self.assertFalse(self.target.exists())
        self.gateway.request("DELETE", f"/api/projects/{self.project.id}", json={"delete_files":False})
        result = import_project(data, self.target)
        self.assertEqual(result["project"]["id"], self.project.id)
        with self.assertRaises(ValueError):
            import_project(data, self.target, clone=True)
        self.assertTrue((self.target / "project.json").exists())

    def test_private_environment_values_are_excluded(self):
        url = f"/api/projects/{self.project.id}/environment"
        revision = self.console.get(url).json()["revision"]
        reply = self.console.put(url + "/JWT_SECRET", json={"expected_revision":revision,"value":"never-export-this-secret"})
        self.assertEqual(reply.status_code, 200)
        data = export_project(self.project.id)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                self.assertNotIn(b"never-export-this-secret", archive.read(name))
        result = import_project(data, self.target, clone=True)
        self.assertEqual(result["environment_names_required"], ["JWT_SECRET"])
        self.assertFalse((self.target / ".reqlica/environment.json").exists())

    def test_import_never_executes_python_and_captures_wal(self):
        marker = self.root / "must-not-exist"
        (code_directory(self.project) / "endpoints/hello.py").write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError("must not execute")\n')
        with closing(sqlite3.connect(self.project.directory / "data.sqlite")) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("INSERT INTO records VALUES(2, 'wal data')")
            db.commit()
            data = export_project(self.project.id)
        import_project(data, self.target, clone=True)
        self.assertFalse(marker.exists())
        with closing(sqlite3.connect(self.target / "data.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records").fetchone()[0], 2)

    def test_http_download_and_bounded_upload(self):
        reply = self.console.get(f"/api/projects/{self.project.id}/export")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.headers["content-type"], "application/zip")
        self.assertEqual(reply.headers["cache-control"], "no-store")
        result = self.console.post("/api/projects/import", params={"directory":str(self.target),"clone":True},
                                   content=reply.content, headers={"Content-Type":"application/zip"})
        self.assertEqual(result.status_code, 201, result.text)
        with patch("reqlica.console.routes.projects.MAX_ARCHIVE", 3):
            self.assertEqual(self.console.post("/api/projects/import", params={"directory":str(self.root / 'oversize')}, content=b"four").status_code, 413)

    def test_malformed_checksums_paths_and_duplicates_are_rejected_before_install(self):
        data = export_project(self.project.id)
        for corrupt in (b"not zip", rewrite_bundle(data, lambda files: files.update({"code/manifest.json":b"{}"})),
                        rewrite_bundle(data, lambda files: files.update({"../escape":b"bad"})),
                        rewrite_bundle(data, lambda files: files.update({"/absolute":b"bad"}))):
            with self.assertRaises(ValueError):
                import_project(corrupt, self.target, clone=True)
            self.assertFalse(self.target.exists())
        result = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(result, "w") as archive:
            for name in original.namelist():
                archive.writestr(name, original.read(name))
            archive.writestr("bundle.json", original.read("bundle.json"))
        with self.assertRaises(ValueError):
            import_project(result.getvalue(), self.target, clone=True)

    def test_zip_symlink_and_invalid_database_rejected(self):
        data = export_project(self.project.id)
        result = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(result, "w") as archive:
            for name in original.namelist():
                entry = zipfile.ZipInfo(name)
                entry.external_attr = ((stat.S_IFLNK | 0o777) if name == "data.sqlite" else (stat.S_IFREG | 0o600)) << 16
                archive.writestr(entry, original.read(name))
        with self.assertRaises(ValueError):
            import_project(result.getvalue(), self.target, clone=True)
        def invalid_database(files):
            files["data.sqlite"] = b"not sqlite"
            metadata = json.loads(files["bundle.json"])
            metadata["files"]["data.sqlite"] = hashlib.sha256(files["data.sqlite"]).hexdigest()
            files["bundle.json"] = json.dumps(metadata).encode()
        with self.assertRaises(ValueError):
            import_project(rewrite_bundle(data, invalid_database), self.target, clone=True)
        self.assertFalse(self.target.exists())

    def test_failed_registration_rolls_back_only_import(self):
        data = export_project(self.project.id)
        before = set(registry_directory().glob("*.json"))
        with patch("reqlica.projects.transfer.os.link", side_effect=OSError("registry failure")):
            with self.assertRaises(OSError):
                import_project(data, self.target, clone=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(set(registry_directory().glob("*.json")), before)
        self.assertTrue(self.project.directory.exists())
