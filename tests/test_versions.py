"""Exercise versioned layouts, legacy conversion, snapshots and locking."""

import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from reqlica.projects.store import (
    Project,
    ProjectMetadata,
    create_project,
    get_project,
    registry_directory,
)
from reqlica.projects.versions import (
    _fsync_directory,
    atomic_write_json,
    code_directory,
    content_digest,
    convert_project,
    project_lock,
    snapshot_code,
)


def _process_lock(directory, channel):
    channel.send("waiting")
    with project_lock(Path(directory)):
        channel.send("acquired")
    channel.close()


class VersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )

    def legacy_project(self):
        directory = self.root / "legacy"
        directory.mkdir()
        project = Project(id=uuid4().hex, name="Original name", directory=directory)
        atomic_write_json(
            directory / "project.json", {"id": project.id, "name": project.name}
        )
        atomic_write_json(directory / "manifest.json", {"endpoints": []})
        (directory / "endpoints").mkdir()
        (directory / "endpoints" / "hello.py").write_text(
            "def hello():\n    return 'legacy'\n"
        )
        registry_directory().mkdir(parents=True)
        atomic_write_json(
            registry_directory() / f"{project.id}.json", {"directory": str(directory)}
        )
        return project

    def test_metadata_defaults_and_validates_active_version(self):
        metadata = ProjectMetadata(id="a" * 32, name="Name")
        self.assertIsNone(metadata.active_version)
        for invalid in ("", "../outside", "A" * 32, "a" * 31, "a" * 33, 12):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ProjectMetadata(id="a" * 32, name="Name", active_version=invalid)

    def test_new_layout_reopens_and_sample_unittest_imports_hello(self):
        project = create_project(self.root / "new", name=" New ")
        self.assertRegex(project.active_version, r"^[0-9a-f]{32}$")
        self.assertEqual(get_project(project.id), project)
        code = code_directory(project)
        self.assertEqual(code, project.directory / "versions" / project.active_version)
        self.assertEqual(
            {path.name for path in project.directory.iterdir()},
            {"project.json", "versions", "drafts", "data.sqlite"},
        )
        self.assertEqual(
            json.loads((project.directory / "project.json").read_text())[
                "active_version"
            ],
            project.active_version,
        )
        self.assertTrue((code / "manifest.json").is_file())
        self.assertTrue((code / "endpoints" / "hello.py").is_file())
        self.assertTrue((code / "tests" / "test_hello.py").is_file())
        with closing(sqlite3.connect(project.directory / "data.sqlite")) as database:
            self.assertEqual(
                database.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            check=False,
            cwd=code,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_creation_failure_cleans_only_scaffolding(self):
        for existing in (False, True):
            directory = self.root / str(existing)
            if existing:
                directory.mkdir()
            with (
                patch.object(Path, "replace", side_effect=OSError("cannot publish")),
                self.assertRaisesRegex(OSError, "cannot publish"),
            ):
                create_project(directory)
            self.assertEqual(directory.exists(), existing)
            if existing:
                self.assertEqual(list(directory.iterdir()), [])
            self.assertEqual(list(registry_directory().iterdir()), [])

    def test_legacy_conversion_preserves_originals_and_database_is_idempotent(self):
        legacy = self.legacy_project()
        self.assertEqual(get_project(legacy.id), legacy)
        self.assertEqual(code_directory(legacy), legacy.directory)
        with closing(sqlite3.connect(legacy.directory / "data.sqlite")) as database:
            database.execute("CREATE TABLE records (value TEXT)")
            database.execute("INSERT INTO records VALUES ('keep')")
            database.commit()
        original = (legacy.directory / "endpoints" / "hello.py").read_bytes()
        before = content_digest(legacy.directory)
        converted = convert_project(legacy.id)
        self.assertEqual(converted.id, legacy.id)
        self.assertEqual(converted.name, legacy.name)
        self.assertEqual(get_project(legacy.id), converted)
        self.assertEqual(content_digest(code_directory(converted)), before)
        self.assertEqual(
            (legacy.directory / "endpoints" / "hello.py").read_bytes(), original
        )
        self.assertTrue((legacy.directory / "manifest.json").is_file())
        self.assertTrue((legacy.directory / "drafts").is_dir())
        self.assertEqual(convert_project(legacy.id), converted)
        self.assertEqual(len(list((legacy.directory / "versions").iterdir())), 1)
        (legacy.directory / "endpoints" / "hello.py").write_text(
            "changed legacy root\n"
        )
        self.assertEqual(
            (code_directory(converted) / "endpoints" / "hello.py").read_bytes(),
            original,
        )
        with closing(sqlite3.connect(legacy.directory / "data.sqlite")) as database:
            self.assertEqual(
                database.execute("SELECT value FROM records").fetchone()[0], "keep"
            )

    def test_conversion_initializes_missing_database_and_publishes_last(self):
        legacy = self.legacy_project()
        actual_write = atomic_write_json

        def checked_publish(path, payload):
            self.assertIsNone(get_project(legacy.id).active_version)
            candidate = legacy.model_copy(
                update={"active_version": payload["active_version"]}
            )
            self.assertEqual(
                content_digest(code_directory(candidate)),
                content_digest(legacy.directory),
            )
            self.assertTrue((legacy.directory / "data.sqlite").is_file())
            actual_write(path, payload)

        with patch(
            "reqlica.projects.versions.atomic_write_json", side_effect=checked_publish
        ):
            converted = convert_project(legacy.id)
        self.assertIsNotNone(converted.active_version)

    def test_failed_conversion_never_publishes_metadata(self):
        legacy = self.legacy_project()
        original = (legacy.directory / "project.json").read_bytes()
        with (
            patch(
                "reqlica.projects.versions.snapshot_code",
                side_effect=ValueError("changed"),
            ),
            self.assertRaisesRegex(ValueError, "changed"),
        ):
            convert_project(legacy.id)
        self.assertEqual((legacy.directory / "project.json").read_bytes(), original)
        self.assertIsNone(get_project(legacy.id).active_version)
        with (
            patch(
                "reqlica.projects.versions.atomic_write_json",
                side_effect=OSError("cannot publish"),
            ),
            self.assertRaisesRegex(OSError, "cannot publish"),
        ):
            convert_project(legacy.id)
        self.assertEqual((legacy.directory / "project.json").read_bytes(), original)
        self.assertIsNone(get_project(legacy.id).active_version)
        self.assertIsNotNone(convert_project(legacy.id).active_version)

    def test_content_digest_ignores_legacy_state_and_python_caches(self):
        project = self.legacy_project()
        expected = content_digest(project.directory)
        for name in (
            "data.sqlite",
            "data.sqlite-wal",
            "data.sqlite-shm",
            "data.sqlite-journal",
        ):
            (project.directory / name).write_bytes(b"mutable state")
        for name in ("drafts", "versions", ".reqlica", "__pycache__"):
            (project.directory / name).mkdir()
            (project.directory / name / "unrelated").write_text("ignore me")
        (project.directory / "endpoints" / "hello.pyc").write_bytes(b"cache")
        metadata = json.loads((project.directory / "project.json").read_text())
        metadata["name"] = "Renamed"
        atomic_write_json(project.directory / "project.json", metadata)
        self.assertEqual(content_digest(project.directory), expected)
        snapshot = self.root / "snapshot"
        self.assertEqual(snapshot_code(project.directory, snapshot), expected)
        self.assertEqual(
            {path.name for path in snapshot.iterdir()}, {"manifest.json", "endpoints"}
        )

    def test_digest_is_deterministic_and_sensitive_to_names_and_bytes(self):
        source = self.root / "source"
        source.mkdir()
        (source / "b.txt").write_bytes(b"b")
        (source / "a.txt").write_bytes(b"a")
        digest = content_digest(source)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        os.utime(source / "a.txt", (1, 1))
        self.assertEqual(content_digest(source), digest)
        snapshot = self.root / "snapshot"
        self.assertEqual(snapshot_code(source, snapshot), digest)
        self.assertEqual(content_digest(snapshot), digest)
        (source / "a.txt").rename(source / "c.txt")
        self.assertNotEqual(content_digest(source), digest)
        (source / "c.txt").rename(source / "a.txt")
        (source / "a.txt").write_bytes(b"different")
        self.assertNotEqual(content_digest(source), digest)

    def test_snapshot_never_overwrites_source_or_existing_destination(self):
        source = self.root / "source"
        source.mkdir()
        (source / "code.py").write_text("original")
        for destination in (source, source / "nested"):
            with self.assertRaises(ValueError):
                snapshot_code(source, destination)
        destination = self.root / "existing"
        destination.mkdir()
        (destination / "keep").write_text("untouched")
        with self.assertRaises(FileExistsError):
            snapshot_code(source, destination)
        self.assertEqual((destination / "keep").read_text(), "untouched")
        self.assertEqual((source / "code.py").read_text(), "original")

    def test_snapshot_cleans_own_destination_on_mutation_and_copy_failure(self):
        source = self.root / "source"
        source.mkdir()
        (source / "code.py").write_text("original")
        destination = self.root / "snapshot"
        actual_digest = content_digest
        calls = 0

        def mutate(directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                (source / "code.py").write_text("changed")
            return actual_digest(directory)

        with (
            patch("reqlica.projects.versions.content_digest", side_effect=mutate),
            self.assertRaisesRegex(ValueError, "changed"),
        ):
            snapshot_code(source, destination)
        self.assertFalse(destination.exists())
        with (
            patch(
                "reqlica.projects.versions._read_code_file",
                side_effect=[b"changed", OSError("copy failed")],
            ),
            self.assertRaisesRegex(OSError, "copy failed"),
        ):
            snapshot_code(source, destination)
        self.assertFalse(destination.exists())
        with (
            patch(
                "reqlica.projects.versions.content_digest",
                side_effect=["a" * 64, "a" * 64, "b" * 64],
            ),
            self.assertRaisesRegex(ValueError, "changed"),
        ):
            snapshot_code(source, destination)
        self.assertFalse(destination.exists())
        with (
            patch(
                "reqlica.projects.versions.os.fsync", side_effect=OSError("sync failed")
            ),
            self.assertRaisesRegex(OSError, "sync failed"),
        ):
            snapshot_code(source, destination)
        self.assertFalse(destination.exists())

    def test_rejects_symlinks_and_nonregular_code(self):
        source = self.root / "source"
        source.mkdir()
        outside = self.root / "outside.py"
        outside.write_text("secret")
        link = source / "link.py"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            content_digest(source)
        with self.assertRaises(ValueError):
            snapshot_code(source, self.root / "snapshot")
        link.unlink()
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            content_digest(source)
        link.unlink()
        os.mkfifo(source / "pipe")
        with self.assertRaises(ValueError):
            content_digest(source)
        self.assertFalse((self.root / "snapshot").exists())

    def test_code_directory_rejects_version_and_parent_symlink_escapes(self):
        project = create_project(self.root / "project")
        code = code_directory(project)
        moved = self.root / "outside"
        code.rename(moved)
        code.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            code_directory(project)
        code.unlink()
        moved.rename(code)
        versions = project.directory / "versions"
        relocated = self.root / "outside_versions"
        versions.rename(relocated)
        versions.symlink_to(relocated, target_is_directory=True)
        with self.assertRaises(ValueError):
            code_directory(project)
        unsafe = project.model_copy(update={"active_version": "../outside"})
        with self.assertRaises(ValueError):
            code_directory(unsafe)

    def test_atomic_json_replaces_and_cleans_temporary_files_on_failure(self):
        path = self.root / "metadata.json"
        atomic_write_json(path, {"value": "original"})
        with (
            patch.object(Path, "replace", side_effect=OSError("failed")),
            self.assertRaisesRegex(OSError, "failed"),
        ):
            atomic_write_json(path, {"value": "new"})
        self.assertEqual(json.loads(path.read_text()), {"value": "original"})
        self.assertEqual(list(self.root.iterdir()), [path])
        with patch("reqlica.projects.versions.os.fsync", wraps=os.fsync) as fsync:
            atomic_write_json(path, {"value": "new"})
        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(json.loads(path.read_text()), {"value": "new"})

    def test_project_lock_syncs_new_state_directory_entry_before_lock_creation(self):
        directory = self.root / "project"
        directory.mkdir()
        state = directory / ".reqlica"
        self.assertFalse(state.exists())

        def sync_root(path):
            self.assertEqual(path, directory)
            self.assertTrue(state.is_dir())
            self.assertFalse((state / "project.lock").exists())
            _fsync_directory(path)

        with (
            patch(
                "reqlica.projects.versions._fsync_directory", side_effect=sync_root
            ) as sync,
            project_lock(directory),
        ):
            sync.assert_called_once_with(directory)
            self.assertTrue((state / "project.lock").is_file())

    def test_lock_is_nested_and_serializes_threads_but_not_other_projects(self):
        directory = self.root / "project"
        directory.mkdir()
        other = self.root / "other"
        other.mkdir()
        waiting = threading.Event()
        acquired = threading.Event()
        other_acquired = threading.Event()

        def contender():
            waiting.set()
            with project_lock(directory):
                acquired.set()

        def independent():
            with project_lock(other):
                other_acquired.set()

        with project_lock(directory):
            with project_lock(directory):
                thread = threading.Thread(target=contender)
                thread.start()
                self.assertTrue(waiting.wait(5))
                self.assertFalse(acquired.wait(0.1))
                independent_thread = threading.Thread(target=independent)
                independent_thread.start()
                self.assertTrue(other_acquired.wait(5))
                independent_thread.join(timeout=5)
            self.assertFalse(acquired.is_set())
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())
        with self.assertRaises(ValueError), project_lock(directory):
            raise ValueError("release on exception")
        with project_lock(directory):
            pass

    def test_lock_serializes_processes(self):
        directory = self.root / "project"
        directory.mkdir()
        for method in ("spawn", "fork"):
            with self.subTest(method=method):
                context = multiprocessing.get_context(method)
                parent, child = context.Pipe()
                self.addCleanup(parent.close)
                self.addCleanup(child.close)
                process = context.Process(
                    target=_process_lock, args=(str(directory), child)
                )
                try:
                    with project_lock(directory):
                        process.start()
                        self.assertTrue(parent.poll(10))
                        self.assertEqual(parent.recv(), "waiting")
                        self.assertFalse(parent.poll(0.15))
                    self.assertTrue(parent.poll(10))
                    self.assertEqual(parent.recv(), "acquired")
                    process.join(timeout=10)
                    self.assertEqual(process.exitcode, 0)
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
