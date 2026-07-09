"""Project manifest, lockfile, and package command helpers."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

RESERVED_DEPENDENCY_NAMES = frozenset({"root", "std", "dep"})
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_PROJECT_ENTRY = Path("src/main.vlnc")


class PackageError(Exception):
    """Raised when package metadata or commands are invalid."""


@dataclass(frozen=True)
class Dependency:
    """One direct dependency from valiance.toml."""

    local_name: str
    version: str
    package: str | None = None
    source: str | None = None

    @property
    def identity(self) -> str:
        return self.package or self.source or self.local_name

    @property
    def kind(self) -> str:
        return "vcs" if self.source is not None else "registry"

    def manifest_value(self) -> str | dict[str, str]:
        if self.source is not None:
            return {"source": self.source, "version": self.version}
        if self.package is not None and self.package != self.local_name:
            return {"package": self.package, "version": self.version}
        return self.version


@dataclass(frozen=True)
class Manifest:
    """A parsed Valiance project manifest."""

    root: Path
    project: dict[str, object]
    entries: dict[str, str]
    dependencies: tuple[Dependency, ...]

    @property
    def path(self) -> Path:
        return self.root / "valiance.toml"

    def dependency(self, name: str) -> Dependency | None:
        for dependency in self.dependencies:
            if dependency.local_name == name:
                return dependency
        return None


def find_project_root(start: Path | None = None) -> Path | None:
    """Return the nearest directory containing valiance.toml."""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for parent in (current, *current.parents):
        if (parent / "valiance.toml").exists():
            return parent
    return None


def load_manifest(root: Path) -> Manifest:
    path = root / "valiance.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PackageError(f"could not read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PackageError(f"invalid {path}: {exc}") from exc

    project = data.get("project", {})
    entries = data.get("entries", {})
    dependencies = data.get("dependencies", {})
    if not isinstance(project, dict):
        raise PackageError("[project] must be a table")
    if not isinstance(entries, dict):
        raise PackageError("[entries] must be a table")
    if not isinstance(dependencies, dict):
        raise PackageError("[dependencies] must be a table")

    parsed_entries: dict[str, str] = {}
    for entry_name, entry_path in entries.items():
        if not NAME_RE.fullmatch(entry_name):
            raise PackageError(f"entry name {entry_name!r} is not valid")
        if not isinstance(entry_path, str):
            raise PackageError(f"entry {entry_name!r} path must be a string")
        parsed_entries[entry_name] = entry_path

    return Manifest(
        root,
        dict(project),
        parsed_entries,
        tuple(_parse_dependency(name, value) for name, value in dependencies.items()),
    )


def require_manifest(start: Path | None = None) -> Manifest:
    root = find_project_root(start)
    if root is None:
        raise PackageError("no enclosing valiance.toml found")
    return load_manifest(root)


def project_entry_path(manifest: Manifest, name: str = "main") -> Path:
    """Resolve and validate one named project entry point."""
    configured = manifest.entries.get(name)
    if configured is None:
        available = ", ".join(sorted(manifest.entries)) or "(none)"
        raise PackageError(
            f"project has no entry named {name!r}; available entries: {available}"
        )

    entry = Path(configured)
    if entry.is_absolute():
        raise PackageError(f"entry {name!r} must be relative to the project root")

    root = manifest.root.resolve()
    resolved = (root / entry).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PackageError(
            f"entry {name!r} must stay within the project root"
        ) from exc

    if not resolved.is_file():
        raise PackageError(f"project entry {name!r} does not exist: {resolved}")
    return resolved


def init_project(
    path: Path | None = None,
    *,
    name: str | None = None,
) -> Path:
    """Create a minimal Valiance project structure."""
    root = (path or Path.cwd()).resolve()
    project_name = name or root.name
    if (root / "valiance.toml").exists():
        raise PackageError(f"{root} already contains valiance.toml")
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "valiance.toml").write_text(
        "\n".join(
            (
                "[project]",
                f"name = {_toml_value(project_name)}",
                'version = "0.1.0"',
                "",
                "[entries]",
                'main = "src/main.vlnc"',
                "",
                "[dependencies]",
                "",
            )
        ),
        encoding="utf-8",
    )
    main = root / "src" / "main.vlnc"
    if not main.exists():
        main.write_text('"Hello, Valiance" println\n', encoding="utf-8")
    gitignore = root / ".gitignore"
    if gitignore.exists():
        contents = gitignore.read_text(encoding="utf-8")
        if ".vln/" not in contents.splitlines():
            separator = "" if contents.endswith("\n") else "\n"
            gitignore.write_text(contents + separator + ".vln/\n", encoding="utf-8")
    else:
        gitignore.write_text(".vln/\n", encoding="utf-8")
    write_lockfile(load_manifest(root))
    return root


def install(start: Path | None = None) -> tuple[Manifest, Path]:
    """Update the lockfile and ensure local package directories exist."""
    manifest = require_manifest(start)
    lock_path = write_lockfile(manifest)
    packages_dir = manifest.root / ".vln"
    packages_dir.mkdir(exist_ok=True)
    for dependency in manifest.dependencies:
        package_dir = packages_dir / dependency.local_name
        package_dir.mkdir(exist_ok=True)
        metadata = {
            "name": dependency.local_name,
            "identity": dependency.identity,
            "source": dependency.source or "registry",
            "version": dependency.version,
        }
        (package_dir / "package.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return manifest, lock_path


def add_dependency(
    target: str,
    version: str,
    *,
    alias: str | None = None,
    start: Path | None = None,
) -> Manifest:
    manifest = require_manifest(start)
    _validate_exact_version(version)
    if _is_vcs_source(target):
        local_name = alias or target.rstrip("/").rsplit("/", 1)[-1]
        dependency = Dependency(local_name, version, source=target)
    else:
        local_name = alias or target
        package_name = None if local_name == target else target
        dependency = Dependency(local_name, version, package=package_name)
    _validate_dependency_name(dependency.local_name)
    dependencies = _without_dependency(manifest.dependencies, dependency.local_name)
    updated = Manifest(
        manifest.root,
        manifest.project,
        manifest.entries,
        dependencies + (dependency,),
    )
    write_manifest(updated)
    install(manifest.root)
    return updated


def remove_dependency(name: str, *, start: Path | None = None) -> Manifest:
    manifest = require_manifest(start)
    _validate_dependency_name(name)
    dependencies = _without_dependency(manifest.dependencies, name)
    if len(dependencies) == len(manifest.dependencies):
        raise PackageError(f"dependency {name!r} is not declared")
    updated = Manifest(manifest.root, manifest.project, manifest.entries, dependencies)
    write_manifest(updated)
    install(manifest.root)
    unused_dir = manifest.root / ".vln" / name
    if unused_dir.exists() and unused_dir.is_dir():
        for child in sorted(unused_dir.iterdir(), reverse=True):
            if child.is_file():
                child.unlink()
        unused_dir.rmdir()
    return updated


def upgrade_dependency(
    name: str,
    version: str,
    *,
    start: Path | None = None,
) -> Manifest:
    manifest = require_manifest(start)
    _validate_dependency_name(name)
    _validate_exact_version(version)
    existing = manifest.dependency(name)
    if existing is None:
        raise PackageError(f"dependency {name!r} is not declared")
    updated_dependency = Dependency(
        existing.local_name,
        version,
        package=existing.package,
        source=existing.source,
    )
    dependencies = tuple(
        updated_dependency if dependency.local_name == name else dependency
        for dependency in manifest.dependencies
    )
    updated = Manifest(manifest.root, manifest.project, manifest.entries, dependencies)
    write_manifest(updated)
    install(manifest.root)
    return updated


def write_manifest(manifest: Manifest) -> None:
    lines = ["[project]"]
    for key, value in manifest.project.items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    lines.append("[entries]")
    for name, path in manifest.entries.items():
        lines.append(f"{name} = {_toml_value(path)}")
    lines.append("")
    lines.append("[dependencies]")
    for dependency in sorted(manifest.dependencies, key=lambda item: item.local_name):
        lines.extend(_dependency_lines(dependency))
    manifest.path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_lockfile(manifest: Manifest) -> Path:
    lock = {
        "version": 1,
        "package": {
            "name": manifest.project.get("name", manifest.root.name),
            "version": manifest.project.get("version", "0.0.0"),
        },
        "dependencies": [
            {
                "name": dependency.local_name,
                "kind": dependency.kind,
                "identity": dependency.identity,
                "source": dependency.source or "registry",
                "version": dependency.version,
                "dependencies": [],
                "integrity": None,
            }
            for dependency in sorted(
                manifest.dependencies,
                key=lambda item: item.local_name,
            )
        ],
    }
    path = manifest.root / "valiance.lock"
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def dependency_install_root(manifest: Manifest, name: str) -> Path:
    dependency = manifest.dependency(name)
    if dependency is None:
        raise PackageError(f"dependency {name!r} is not declared")
    return manifest.root / ".vln" / dependency.local_name


def _parse_dependency(name: str, value: object) -> Dependency:
    _validate_dependency_name(name)
    if isinstance(value, str):
        _validate_exact_version(value)
        return Dependency(name, value)
    if not isinstance(value, dict):
        raise PackageError(f"dependency {name!r} must be a version string or table")
    version = value.get("version")
    package = value.get("package")
    source = value.get("source")
    if not isinstance(version, str):
        raise PackageError(f"dependency {name!r} needs an exact version")
    _validate_exact_version(version)
    if package is not None and not isinstance(package, str):
        raise PackageError(f"dependency {name!r} package must be a string")
    if source is not None and not isinstance(source, str):
        raise PackageError(f"dependency {name!r} source must be a string")
    if package is not None and source is not None:
        raise PackageError(f"dependency {name!r} cannot use both package and source")
    return Dependency(name, version, package=package, source=source)


def _validate_dependency_name(name: str) -> None:
    if name in RESERVED_DEPENDENCY_NAMES:
        raise PackageError(f"{name!r} is reserved and cannot be a dependency name")
    if not NAME_RE.fullmatch(name):
        raise PackageError(f"dependency name {name!r} is not a valid module component")


def _validate_exact_version(version: str) -> None:
    if not VERSION_RE.fullmatch(version):
        raise PackageError(f"version {version!r} is not an exact numeric version")


def _without_dependency(
    dependencies: tuple[Dependency, ...],
    name: str,
) -> tuple[Dependency, ...]:
    return tuple(
        dependency for dependency in dependencies if dependency.local_name != name
    )


def _is_vcs_source(target: str) -> bool:
    return "/" in target


def _dependency_lines(dependency: Dependency) -> list[str]:
    value = dependency.manifest_value()
    if isinstance(value, str):
        return [f"{dependency.local_name} = {_toml_value(value)}"]
    body = ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items())
    return [f"{dependency.local_name} = {{ {body} }}"]


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise PackageError(f"cannot write TOML value {value!r}")
