import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from valiance.modules_system.packages import PackageError, install, load_manifest


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
                f'leaf = {{ source = "{leaf}", version = "1.0.0" }}\n',
            )
            app = base / "app"
            app.mkdir()
            (app / "valiance.toml").write_text(
                '[project]\nname = "app"\nversion = "1.0.0"\n\n'
                f'[dependencies]\nparent = {{ source = "{parent}", version = "1.0.0" }}\n',
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
                f'[dependencies]\nlibrary = {{ source = "{package}", version = "1.0.0" }}\n',
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
                f'[dependencies]\nlibrary = {{ source = "{package}", version = "1.0.0" }}\n',
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
                f'[dependencies]\nlibrary = {{ source = "{package}", version = "1.0.0" }}\n',
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
