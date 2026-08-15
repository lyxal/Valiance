import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from valiance.modules_system.packages import (
    PackageError, add_dependency, install, load_manifest,
)


def make_repo(root: Path, name: str, version: str, dependencies: str = "") -> Path:
    repo = root / name
    repo.mkdir()
    (repo / "valiance.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n\n'
        f'[dependencies]\n{dependencies}',
        encoding="utf-8",
    )
    (repo / f"{name}.vlnc").write_text(
        f'public define \\{name} => "{name}"\n', encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "initial",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "tag", f"v{version}"], cwd=repo, check=True)
    return repo


class PhaseOnePackageTests(unittest.TestCase):
    def test_install_fetches_transitive_git_graph_and_locks_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            leaf = make_repo(base, "leaf", "1.0.0")
            parent = make_repo(
                base,
                "parent",
                "1.0.0",
                f'leaf = {{ kind = "git", package = "leaf", location = "{leaf}", version = "1.0.0" }}\n',
            )
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nparent = {{ kind = "git", package = "parent", location = "{parent}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )

            manifest, lock_path = install(app)
            lock = json.loads(lock_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest.project["name"], "app")
            self.assertTrue((app / ".vln/parent/parent.vlnc").is_file())
            self.assertTrue((app / ".vln/parent/.vln/leaf/leaf.vlnc").is_file())
            self.assertEqual(len(lock["dependencies"]), 2)
            for dependency in lock["dependencies"]:
                self.assertRegex(dependency["revision"], r"^[0-9a-f]{40}$")
                self.assertRegex(dependency["integrity"], r"^sha256:[0-9a-f]{64}$")

    def test_locked_install_rejects_manifest_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = make_repo(base, "library", "1.0.0")
            app = base / "app"
            app.mkdir()
            manifest_path = app / "valiance.toml"
            manifest_path.write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nlibrary = {{ kind = "git", package = "library", location = "{package}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            install(app)
            manifest_path.write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PackageError, "out of date"):
                install(app, locked=True)

    def test_locked_install_restores_tampered_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = make_repo(base, "library", "1.0.0")
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nlibrary = {{ kind = "git", package = "library", location = "{package}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            install(app)
            installed = app / ".vln/library/library.vlnc"
            installed.write_text("tampered", encoding="utf-8")

            install(app, locked=True)

            self.assertNotEqual(installed.read_text(encoding="utf-8"), "tampered")
            self.assertEqual(load_manifest(app).dependency("library").version, "1.0.0")

class PackageProgressTests(unittest.TestCase):
    def test_progress_reports_all_install_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = make_repo(base, "library", "1.0.0")
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nlibrary = {{ kind = "git", package = "library", location = "{package}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            events = []

            install(app, progress=events.append)

            self.assertEqual(
                [event.action for event in events],
                ["resolve", "fetch", "verify", "install", "lock", "complete"],
            )
            self.assertEqual(events[0].step, 1)
            self.assertEqual(events[3].step, 4)


class ExplicitDependencySourceTests(unittest.TestCase):
    def test_compact_dependency_is_rejected_with_migration_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                '[dependencies]\njson = "2.1.0"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PackageError, "inline table with an explicit source") as raised:
                load_manifest(root)
            self.assertIn('kind = "git"', raised.exception.hint or "")

    def test_sources_require_package_identity_and_coordinates(self):
        cases = (
            ('git = { kind = "git", location = "repo", version = "1.0.0" }', "package identity"),
            ('local = { kind = "local", path = "../local", version = "1.0.0" }', "package identity"),
            ('git = { kind = "git", package = "pkg", version = "1.0.0" }', "non-empty location"),
            ('local = { kind = "local", package = "pkg", version = "1.0.0" }', "non-empty path"),
        )
        for declaration, message in cases:
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "valiance.toml").write_text(
                    '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                    f'[dependencies]\n{declaration}\n', encoding="utf-8",
                )
                with self.assertRaisesRegex(PackageError, message):
                    load_manifest(root)

    def test_reserved_source_kinds_are_manifest_errors(self):
        for kind in ("registry", "hg", "svn", "fossil"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "valiance.toml").write_text(
                    '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                    f'[dependencies]\npkg = {{ kind = "{kind}", package = "pkg", version = "1.0.0" }}\n',
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(PackageError, "unsupported source kind"):
                    load_manifest(root)

    def test_local_source_is_copied_locked_and_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = base / "library"
            package.mkdir()
            (package / "valiance.toml").write_text(
                '[project]\nname = "library"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            (package / "library.vlnc").write_text('public define \\library => "local"\n', encoding="utf-8")
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nlibrary = {{ kind = "local", package = "library", path = "{package}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            _, lock_path = install(app)
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(lock["dependencies"][0]["kind"], "local")
            self.assertEqual(lock["dependencies"][0]["revision"], "local")
            self.assertTrue((app / ".vln/library/library.vlnc").is_file())
            install(app, locked=True)

    def test_failed_add_restores_manifest_lock_and_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "valiance.toml"
            original = '[project]\nname = "app"\nversion = "1.0.0"\n\n[dependencies]\n'
            manifest_path.write_text(original, encoding="utf-8")
            lock_path = root / "valiance.lock"
            lock_path.write_text('{"sentinel": true}\n', encoding="utf-8")
            packages = root / ".vln"
            packages.mkdir()
            (packages / "sentinel.txt").write_text("kept", encoding="utf-8")
            with self.assertRaises(PackageError):
                add_dependency(
                    "missing", "1.0.0", source_kind="git", package="missing",
                    location=str(root / "does-not-exist"), start=root,
                )
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), original)
            self.assertEqual(lock_path.read_text(encoding="utf-8"), '{"sentinel": true}\n')
            self.assertEqual((packages / "sentinel.txt").read_text(encoding="utf-8"), "kept")

class LivePathDependencyTests(unittest.TestCase):
    def test_path_dependency_is_live_not_copied_and_resolves_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outer = base / "workspace"
            outer.mkdir()
            (outer / "valiance.toml").write_text(
                '[project]\nname = "workspace"\nversion = "0.1.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            (outer / "workspace.vlnc").write_text(
                'public define \\workspace => "live"\n', encoding="utf-8"
            )
            nested = outer / "apps" / "server"
            nested.mkdir(parents=True)
            (nested / "valiance.toml").write_text(
                '[project]\nname = "server"\nversion = "0.1.0"\n\n'
                '[dependencies]\nworkspace = { kind = "path", package = "workspace", path = "../..", version = "0.1.0" }\n',
                encoding="utf-8",
            )
            manifest, lock_path = install(nested)
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest.dependency("workspace").source_kind, "path")
            self.assertEqual(lock["dependencies"][0]["kind"], "path")
            self.assertIsNone(lock["dependencies"][0]["install_path"])
            self.assertFalse((nested / ".vln/workspace").exists())
            self.assertEqual(
                __import__("valiance.modules_system.packages", fromlist=["dependency_install_root"])
                .dependency_install_root(manifest, "workspace"),
                outer.resolve(),
            )
            install(nested, locked=True)

    def test_locked_path_dependency_allows_live_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = base / "shared"
            package.mkdir()
            (package / "valiance.toml").write_text(
                '[project]\nname = "shared"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            source = package / "shared.vlnc"
            source.write_text('public define \\value => 1\n', encoding="utf-8")
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nshared = {{ kind = "path", package = "shared", path = "{package}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            install(app)
            source.write_text('public define \\value => 2\n', encoding="utf-8")
            install(app, locked=True)
            self.assertEqual(source.read_text(encoding="utf-8"), 'public define \\value => 2\n')

class LocalizeDependencyTests(unittest.TestCase):
    def test_localize_converts_live_path_to_managed_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shared = base / "shared"
            shared.mkdir()
            (shared / "valiance.toml").write_text(
                '[project]\nname = "shared"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            source = shared / "shared.vlnc"
            source.write_text('public define \\value => 1\n', encoding="utf-8")
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nshared = {{ kind = "path", package = "shared", path = "{shared}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            from valiance.modules_system.packages import localize_dependency
            updated = localize_dependency("shared", start=app)
            self.assertEqual(updated.dependency("shared").source_kind, "local")
            installed = app / ".vln/shared/shared.vlnc"
            self.assertTrue(installed.is_file())
            source.write_text('public define \\value => 2\n', encoding="utf-8")
            self.assertIn("=> 1", installed.read_text(encoding="utf-8"))
            manifest_text = (app / "valiance.toml").read_text(encoding="utf-8")
            self.assertIn('kind = "local"', manifest_text)

class PathOwnershipAndCliTests(unittest.TestCase):
    def test_path_owned_managed_dependency_uses_structured_lock_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            child = base / "child"
            child.mkdir()
            (child / "valiance.toml").write_text(
                '[project]\nname = "child"\nversion = "1.0.0"\n\n[dependencies]\n', encoding="utf-8"
            )
            (child / "child.vlnc").write_text('public define \\child => 1\n', encoding="utf-8")
            workspace = base / "workspace"
            workspace.mkdir()
            (workspace / "valiance.toml").write_text(
                '[project]\nname = "workspace"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nchild = {{ kind = "local", package = "child", path = "{child}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nworkspace = {{ kind = "path", package = "workspace", path = "{workspace}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            _, lock_path = install(app)
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            managed = next(record for record in lock["dependencies"] if record["name"] == "child")
            self.assertEqual(managed["owner_kind"], "path")
            self.assertEqual(managed["owner_source"], str(workspace.resolve()))
            self.assertEqual(managed["install_path"], "child")
            self.assertNotIn("@path:", managed["install_path"])
            self.assertTrue((workspace / ".vln/child/child.vlnc").is_file())
            install(app, locked=True)

    def test_failed_operation_rolls_back_path_owned_managed_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            good = make_repo(base, "good", "1.0.0")
            workspace = base / "workspace"
            workspace.mkdir()
            missing = base / "missing"
            (workspace / "valiance.toml").write_text(
                '[project]\nname = "workspace"\nversion = "1.0.0"\n\n[dependencies]\n'
                f'a_good = {{ kind = "git", package = "good", location = "{good}", version = "1.0.0" }}\n'
                f'z_bad = {{ kind = "git", package = "missing", location = "{missing}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            managed = workspace / ".vln"
            managed.mkdir()
            (managed / "sentinel.txt").write_text("original", encoding="utf-8")
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nworkspace = {{ kind = "path", package = "workspace", path = "{workspace}", version = "1.0.0" }}\n',
                encoding="utf-8",
            )
            from valiance.modules_system.packages import upgrade_dependency
            with self.assertRaises(PackageError):
                upgrade_dependency("workspace", "1.0.0", start=app)
            self.assertEqual((managed / "sentinel.txt").read_text(encoding="utf-8"), "original")
            self.assertFalse((managed / "a_good").exists())

    def test_cli_add_path_infers_metadata_and_localize_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shared = base / "shared"
            shared.mkdir()
            (shared / "valiance.toml").write_text(
                '[project]\nname = "shared-package"\nversion = "1.2.0"\n\n[dependencies]\n', encoding="utf-8"
            )
            source = shared / "shared.vlnc"
            source.write_text('public define \\value => 1\n', encoding="utf-8")
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n[dependencies]\n', encoding="utf-8"
            )
            from valiance.main import main
            old = Path.cwd()
            try:
                import os
                os.chdir(app)
                self.assertEqual(main(["add", "shared", "--path", str(shared)]), 0)
                self.assertEqual(main(["localize", "shared"]), 0)
            finally:
                os.chdir(old)
            text = (app / "valiance.toml").read_text(encoding="utf-8")
            self.assertIn('package = "shared-package"', text)
            self.assertIn('version = "1.2.0"', text)
            self.assertIn('kind = "local"', text)
            self.assertTrue((app / ".vln/shared/shared.vlnc").is_file())
