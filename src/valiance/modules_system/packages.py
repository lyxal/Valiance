"""Project manifest, lockfile, and package command helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from urllib.parse import urlparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RESERVED_DEPENDENCY_NAMES = frozenset({"root", "std", "dep"})
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_PROJECT_ENTRY = Path("src/main.vlnc")


@dataclass(frozen=True)
class ProjectTemplate:
    """One built-in project scaffold offered by ``vln init``."""

    name: str
    description: str


PROJECT_TEMPLATES = (
    ProjectTemplate("application", "A runnable application with one entry point"),
    ProjectTemplate("package", "A reusable package exposing a public API"),
    ProjectTemplate(
        "multi-module", "A runnable application split across source modules"
    ),
    ProjectTemplate("empty", "Project metadata and documentation without source"),
)
DEFAULT_PROJECT_TEMPLATE = "application"
_TEMPLATE_ALIASES = {
    "application": "application",
    "app": "application",
    "minimal": "application",
    "package": "package",
    "library": "package",
    "lib": "package",
    "multi-module": "multi-module",
    "multimodule": "multi-module",
    "multi": "multi-module",
    "empty": "empty",
}


class PackageError(Exception):
    """Raised when package metadata or commands are invalid."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        """Create a package failure with an optional actionable recovery hint."""
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class PackageProgress:
    """One user-facing package operation update."""

    action: str
    package: str | None = None
    detail: str | None = None
    step: int | None = None
    total: int | None = None


ProgressCallback = Callable[[PackageProgress], None]


def _report(
    callback: ProgressCallback | None,
    action: str,
    package: str | None = None,
    detail: str | None = None,
    *,
    step: int | None = None,
    total: int | None = None,
) -> None:
    """Emit an optional progress update without coupling package logic to a UI."""
    if callback is not None:
        callback(PackageProgress(action, package, detail, step, total))


@dataclass(frozen=True)
class Dependency:
    """One direct dependency from valiance.toml."""

    local_name: str
    version: str
    package: str | None = None
    source: str | None = None

    @property
    def identity(self) -> str:
        """Return the lockfile identity for this dependency source."""
        return self.package or self.source or self.local_name

    @property
    def kind(self) -> str:
        """Return the manifest source kind for this dependency."""
        return "vcs" if self.source is not None else "registry"

    def manifest_value(self) -> str | dict[str, str]:
        """Return the TOML-compatible manifest value for this dependency."""
        if self.source is not None:
            return {"source": self.source, "version": self.version}
        if self.package is not None and self.package != self.local_name:
            return {"package": self.package, "version": self.version}
        return self.version


@dataclass(frozen=True)
class LintSettings:
    """Project-wide lint policy loaded from ``valiance.toml``."""

    enabled: bool = True
    disabled: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormatSettings:
    """Project-wide formatter policy loaded from ``valiance.toml``."""

    add: tuple[str, ...] = ("trailing-commas",)


@dataclass(frozen=True)
class BuildTarget:
    """One named artifact-producing project build target."""

    name: str
    kind: str
    entry: str | None = None
    source: str | None = None
    output: str | None = None
    optimize: bool = True


@dataclass(frozen=True)
class Manifest:
    """A parsed Valiance project manifest."""

    root: Path
    project: dict[str, object]
    entries: dict[str, str]
    dependencies: tuple[Dependency, ...]
    lints: LintSettings = LintSettings()
    builds: dict[str, BuildTarget] = None  # type: ignore[assignment]
    formatting: FormatSettings = FormatSettings()

    def __post_init__(self) -> None:
        """Normalize omitted build-target mappings for compatibility."""
        if self.builds is None:
            object.__setattr__(self, "builds", {})

    @property
    def path(self) -> Path:
        """Return the filesystem path containing this project manifest."""
        return self.root / "valiance.toml"

    def dependency(self, name: str) -> Dependency | None:
        """Return the named dependency declared by this manifest, if present."""
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
    """Read and parse a project manifest from disk."""
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
    lints = data.get("lints", {})
    formatting = data.get("format", {})
    builds = data.get("build", {})
    if not isinstance(project, dict):
        raise PackageError("[project] must be a table")
    if not isinstance(entries, dict):
        raise PackageError("[entries] must be a table")
    if not isinstance(dependencies, dict):
        raise PackageError("[dependencies] must be a table")
    if not isinstance(lints, dict):
        raise PackageError("[lints] must be a table")
    if not isinstance(formatting, dict):
        raise PackageError("[format] must be a table")
    if not isinstance(builds, dict):
        raise PackageError("[build] must be a table")
    unknown_lint_keys = set(lints) - {"enabled", "disable"}
    if unknown_lint_keys:
        names = ", ".join(sorted(map(str, unknown_lint_keys)))
        raise PackageError(f"unknown [lints] setting(s): {names}")
    lint_enabled = lints.get("enabled", True)
    lint_disabled = lints.get("disable", [])
    if not isinstance(lint_enabled, bool):
        raise PackageError("[lints].enabled must be a boolean")
    if not isinstance(lint_disabled, list) or not all(
        isinstance(code, str) for code in lint_disabled
    ):
        raise PackageError("[lints].disable must be an array of lint-code strings")
    from valiance.analysis.lints import canonical_lint_code

    resolved_lint_codes = tuple(canonical_lint_code(code) for code in lint_disabled)
    unknown_codes = sorted(
        code for code, canonical in zip(lint_disabled, resolved_lint_codes, strict=True)
        if canonical is None
    )
    if unknown_codes:
        rendered = ", ".join(repr(code) for code in unknown_codes)
        raise PackageError(f"unknown lint code(s) in [lints].disable: {rendered}")

    unknown_format_keys = set(formatting) - {"add"}
    if unknown_format_keys:
        names = ", ".join(sorted(map(str, unknown_format_keys)))
        raise PackageError(f"unknown [format] setting(s): {names}")
    format_add = formatting.get("add", ["trailing-commas"])
    if not isinstance(format_add, list) or not all(isinstance(option, str) for option in format_add):
        raise PackageError("[format].add must be an array of formatter option strings")
    unknown_format_options = sorted(set(format_add) - {"trailing-commas"})
    if unknown_format_options:
        rendered = ", ".join(repr(option) for option in unknown_format_options)
        raise PackageError(f"unknown formatter option(s) in [format].add: {rendered}")

    parsed_entries: dict[str, str] = {}
    for entry_name, entry_path in entries.items():
        if not NAME_RE.fullmatch(entry_name):
            raise PackageError(f"entry name {entry_name!r} is not valid")
        if not isinstance(entry_path, str):
            raise PackageError(f"entry {entry_name!r} path must be a string")
        parsed_entries[entry_name] = entry_path

    parsed_builds: dict[str, BuildTarget] = {}
    for target_name, target_value in builds.items():
        if not NAME_RE.fullmatch(target_name):
            raise PackageError(f"build target name {target_name!r} is not valid")
        if not isinstance(target_value, dict):
            raise PackageError(f"[build.{target_name}] must be a table")
        unknown = set(target_value) - {"kind", "entry", "source", "output", "optimize"}
        if unknown:
            names = ", ".join(sorted(map(str, unknown)))
            raise PackageError(f"unknown [build.{target_name}] setting(s): {names}")
        kind = target_value.get("kind")
        entry = target_value.get("entry")
        source = target_value.get("source")
        output = target_value.get("output")
        optimize = target_value.get("optimize", True)
        if kind not in {"module", "executable"}:
            raise PackageError(
                f"build target {target_name!r} kind must be 'module' or 'executable'"
            )
        if (entry is None) == (source is None):
            raise PackageError(
                f"build target {target_name!r} must specify exactly one of entry or source"
            )
        for label, value in (("entry", entry), ("source", source), ("output", output)):
            if value is not None and not isinstance(value, str):
                raise PackageError(
                    f"build target {target_name!r} {label} must be a string"
                )
        if not isinstance(optimize, bool):
            raise PackageError(
                f"build target {target_name!r} optimize must be a boolean"
            )
        if entry is not None and entry not in parsed_entries:
            raise PackageError(
                f"build target {target_name!r} references unknown entry {entry!r}"
            )
        suffix = ".vbcm" if kind == "module" else ".vbc"
        if output is not None and Path(output).suffix != suffix:
            raise PackageError(
                f"{kind} build target {target_name!r} output must use {suffix!r}"
            )
        parsed_builds[target_name] = BuildTarget(
            target_name, kind, entry, source, output, optimize
        )

    outputs: dict[Path, str] = {}
    for target in parsed_builds.values():
        output = Path(target.output or f"bin/{target.name}{'.vbcm' if target.kind == 'module' else '.vbc'}")
        previous = outputs.get(output)
        if previous is not None:
            raise PackageError(
                f"build targets {previous!r} and {target.name!r} both write to {str(output)!r}"
            )
        outputs[output] = target.name

    return Manifest(
        root,
        dict(project),
        parsed_entries,
        tuple(_parse_dependency(name, value) for name, value in dependencies.items()),
        LintSettings(
            lint_enabled,
            tuple(dict.fromkeys(code for code in resolved_lint_codes if code is not None)),
        ),
        parsed_builds,
        FormatSettings(tuple(dict.fromkeys(format_add))),
    )


def require_manifest(start: Path | None = None) -> Manifest:
    """Load the nearest project manifest or raise a package error."""
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
        raise PackageError(f"entry {name!r} must stay within the project root") from exc

    if not resolved.is_file():
        raise PackageError(f"project entry {name!r} does not exist: {resolved}")
    return resolved


def init_project(
    path: Path | None = None,
    *,
    name: str | None = None,
    template: str = DEFAULT_PROJECT_TEMPLATE,
    tests: bool = True,
) -> Path:
    """Create a Valiance project in a new directory or the current directory."""
    root = (path or Path.cwd()).resolve()
    project_name = name or root.name
    if not NAME_RE.fullmatch(project_name):
        raise PackageError(
            f"project name {project_name!r} is not a valid module component",
            hint="Use letters, numbers, and underscores, beginning with a letter or underscore.",
        )
    template_name = normalize_project_template(template)
    if (root / "valiance.toml").exists():
        raise PackageError(f"{root} already contains valiance.toml")
    root.mkdir(parents=True, exist_ok=True)

    files, entries = _project_template_files(
        template_name, project_name, include_tests=tests
    )
    manifest_lines = [
        "[project]",
        f"name = {_toml_value(project_name)}",
        'version = "0.1.0"',
        "",
    ]
    if entries:
        manifest_lines.append("[entries]")
        manifest_lines.extend(
            f"{entry_name} = {_toml_value(entry_path)}"
            for entry_name, entry_path in entries.items()
        )
        manifest_lines.append("")
    manifest_lines.extend(
        (
            "[lints]",
            "enabled = true",
            "disable = []",
            "",
            "[dependencies]",
            "",
        )
    )
    (root / "valiance.toml").write_text(
        "\n".join(manifest_lines), encoding="utf-8"
    )
    for relative, contents in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(contents, encoding="utf-8")

    gitignore = root / ".gitignore"
    required_ignores = (".vln/", "bin/")
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = existing.splitlines()
    for ignored in required_ignores:
        if ignored not in lines:
            lines.append(ignored)
    gitignore.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    write_lockfile(load_manifest(root))
    return root


def normalize_project_template(template: str) -> str:
    """Normalize a template name or raise a helpful package error."""
    normalized = _TEMPLATE_ALIASES.get(template.strip().lower())
    if normalized is None:
        choices = ", ".join(item.name for item in PROJECT_TEMPLATES)
        raise PackageError(
            f"unknown project template {template!r}",
            hint=f"Choose one of: {choices}.",
        )
    return normalized


def _project_template_files(
    template: str, project_name: str, *, include_tests: bool
) -> tuple[dict[Path, str], dict[str, str]]:
    """Return source, README, optional tests, and entries for one scaffold."""
    greeting = (
        "#?? Return a greeting for `name`.\n"
        "#?? @param name The name to greet.\n"
        "#?? @returns A friendly greeting.\n"
        "public define greeting(name: String) -> String => \"Hello, $name!\"\n"
    )
    files: dict[Path, str] = {}
    entries: dict[str, str] = {}
    test_import = ""
    group_name = project_name

    if template == "application":
        files[Path("src/app.vlnc")] = greeting
        files[Path("src/main.vlnc")] = (
            "import { root.src.app.greeting }\n\n"
            'greeting("Valiance") println\n'
        )
        entries["main"] = "src/main.vlnc"
        test_import = "root.src.app.greeting"
        files[Path("README.md")] = (
            f"# {project_name}\n\n"
            "A Valiance application.\n\n"
            "## Run\n\n```text\nvln run\n```\n\n"
            "## Test\n\n```text\nvln test\n```\n\n"
            "## Build\n\n```text\nvln compile\n```\n"
        )
    elif template == "multi-module":
        files[Path("src/messages.vlnc")] = greeting
        files[Path("src/main.vlnc")] = (
            "import { root.src.messages.greeting }\n\n"
            'greeting("Valiance") println\n'
        )
        entries["main"] = "src/main.vlnc"
        test_import = "root.src.messages.greeting"
        group_name = "Messages"
        files[Path("README.md")] = (
            f"# {project_name}\n\n"
            "A multi-module Valiance application. The executable entry is "
            "`src/main.vlnc`; reusable functionality lives in "
            "`src/messages.vlnc`.\n\n"
            "## Commands\n\n```text\nvln run\nvln test\nvln compile\n```\n"
        )
    elif template == "package":
        files[Path(f"{project_name}.vlnc")] = greeting
        test_import = f"root.{project_name}.greeting"
        files[Path("README.md")] = (
            f"# {project_name}\n\n"
            "A reusable Valiance package.\n\n"
            "## Use\n\n"
            f"Consumers can import the public API from `dep.{project_name}`.\n\n"
            "## Test\n\n```text\nvln test\n```\n\n"
            "Create an exact version tag such as `v0.1.0` before sharing it.\n"
        )
    else:
        files[Path("README.md")] = (
            f"# {project_name}\n\n"
            "An empty Valiance project. Add an executable entry under `src/` "
            "and declare it in `[entries]`, or add a root `.vlnc` module for a "
            "reusable package.\n"
        )

    if include_tests and test_import:
        files[Path("tests/project.vlnc")] = (
            "import { std.testing }\n"
            f"import {{ {test_import} }}\n\n"
            f'@testgroup("{group_name}")\n'
            "define \\project =>\n"
            '  @test("greets a supplied name")\n'
            "  define \\greetingUsesName =>\n"
            '    testing.assertEqual(greeting("Valiance"), "Hello, Valiance!")\n'
            "  end\n"
            "end\n"
        )
    return files, entries

def install(
    start: Path | None = None,
    *,
    locked: bool = False,
    progress: ProgressCallback | None = None,
) -> tuple[Manifest, Path]:
    """Resolve, fetch, verify, and install the complete dependency graph.

    Source packages are checked out at an immutable Git revision.  The lockfile
    records that revision and a canonical SHA-256 tree digest.  ``locked`` mode
    refuses to re-resolve the manifest and reproduces only the recorded graph.
    """
    manifest = require_manifest(start)
    packages_dir = manifest.root / ".vln"
    packages_dir.mkdir(exist_ok=True)
    if locked:
        lock = _read_lockfile(manifest)
        _validate_lock_matches_manifest(manifest, lock)
        records = lock.get("dependencies")
        if not isinstance(records, list):
            raise PackageError("lockfile dependencies must be an array")
        _report(progress, "lock", detail="Lockfile matches valiance.toml")
        _install_locked_records(manifest, records, progress=progress)
        _report(progress, "complete", detail=f"Installed {len(records)} packages")
        return manifest, manifest.root / "valiance.lock"

    records: list[dict[str, object]] = []
    _resolve_and_install_manifest(
        manifest,
        packages_dir,
        records=records,
        ancestry=(),
        install_prefix=(),
        progress=progress,
    )
    lock_path = write_lockfile(manifest, resolved=records)
    _report(progress, "lock", detail=f"Wrote {lock_path.name}")
    _report(progress, "complete", detail=f"Installed {len(records)} packages")
    return manifest, lock_path


def _resolve_and_install_manifest(
    manifest: Manifest,
    packages_dir: Path,
    *,
    records: list[dict[str, object]],
    ancestry: tuple[str, ...],
    install_prefix: tuple[str, ...],
    progress: ProgressCallback | None,
) -> None:
    """Resolve and install every dependency declared by one manifest."""
    for dependency in sorted(manifest.dependencies, key=lambda item: item.local_name):
        if dependency.source is None:
            raise PackageError(
                f"cannot install registry dependency {dependency.identity!r}",
                hint=(
                    "Use an explicit Git source, for example: "
                    f'{dependency.local_name} = {{ source = "https://github.com/owner/repo.git", '
                    f'version = "{dependency.version}" }}'
                ),
            )
        source = _canonical_git_source(dependency.source, manifest.root)
        label = dependency.local_name
        _report(progress, "resolve", label, dependency.version, step=1, total=4)
        revision = _resolve_git_revision(source, dependency.version)
        _report(progress, "fetch", label, revision[:12], step=2, total=4)
        cycle_key = f"{source}@{revision}"
        if cycle_key in ancestry:
            chain = " -> ".join((*ancestry, cycle_key))
            raise PackageError(f"dependency cycle detected: {chain}")
        destination = packages_dir / dependency.local_name
        with tempfile.TemporaryDirectory(
            prefix=f".{dependency.local_name}-", dir=packages_dir
        ) as tmp_name:
            checkout = Path(tmp_name) / "source"
            _checkout_git_revision(source, revision, checkout)
            package_manifest = load_manifest(checkout)
            _report(progress, "verify", label, "manifest and SHA-256", step=3, total=4)
            _validate_package_manifest(dependency, package_manifest)
            integrity = _tree_integrity(checkout)
            child_names = [item.local_name for item in package_manifest.dependencies]
            record = {
                "name": dependency.local_name,
                "package": package_manifest.project.get(
                    "name", dependency.identity
                ),
                "kind": "vcs",
                "identity": dependency.identity,
                "source": source,
                "version": dependency.version,
                "revision": revision,
                "integrity": integrity,
                "dependencies": sorted(child_names),
                "install_path": "/".join((*install_prefix, dependency.local_name)),
            }
            records.append(record)
            child_root = checkout / ".vln"
            child_root.mkdir(exist_ok=True)
            _resolve_and_install_manifest(
                package_manifest,
                child_root,
                records=records,
                ancestry=(*ancestry, cycle_key),
                install_prefix=(*install_prefix, dependency.local_name, ".vln"),
                progress=progress,
            )
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(checkout, destination)
            _report(progress, "install", label, str(destination), step=4, total=4)


def _install_locked_records(
    manifest: Manifest,
    records: list[object],
    *,
    progress: ProgressCallback | None,
) -> None:
    """Install exactly the revisions and hashes recorded in a lockfile."""
    normalized: list[dict[str, object]] = []
    for value in records:
        if not isinstance(value, dict):
            raise PackageError("lockfile dependency entries must be objects")
        required = {"name", "source", "version", "revision", "integrity", "install_path"}
        missing = sorted(required - value.keys())
        if missing:
            raise PackageError(
                "lockfile dependency entry is missing: " + ", ".join(missing)
            )
        normalized.append(value)
    normalized.sort(key=lambda item: str(item["install_path"]).count("/"))
    for record in normalized:
        label = str(record["name"])
        _report(progress, "verify", label, "locked integrity", step=1, total=2)
        rel = Path(str(record["install_path"]))
        if rel.is_absolute() or ".." in rel.parts:
            raise PackageError("lockfile install_path must stay within .vln")
        destination = manifest.root / ".vln" / rel
        expected = str(record["integrity"])
        if destination.is_dir() and _tree_integrity(destination) == expected:
            _report(progress, "cached", label, "already verified", step=2, total=2)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{record['name']}-", dir=destination.parent
        ) as tmp_name:
            checkout = Path(tmp_name) / "source"
            _checkout_git_revision(
                str(record["source"]), str(record["revision"]), checkout
            )
            actual = _tree_integrity(checkout)
            if actual != expected:
                raise PackageError(
                    f"integrity check failed for {record['name']!r}: "
                    f"expected {expected}, got {actual}"
                )
            locked_manifest = load_manifest(checkout)
            if locked_manifest.project.get("version") != record["version"]:
                raise PackageError(
                    f"locked package {record['name']!r} manifest version changed"
                )
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(checkout, destination)
            _report(progress, "install", label, "restored locked revision", step=2, total=2)


def _canonical_git_source(source: str, relative_to: Path) -> str:
    """Return a stable Git source, resolving local paths from the manifest."""
    parsed = urlparse(source)
    if parsed.scheme or source.startswith("git@"):
        return source
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = relative_to / path
    return str(path.resolve())


def _git(args: list[str], *, cwd: Path | None = None) -> str:
    """Run Git and turn process failures into package diagnostics."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PackageError("Git is required to install source dependencies") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Git command failed").strip()
        raise PackageError(detail) from exc
    return completed.stdout


def _resolve_git_revision(source: str, version: str) -> str:
    """Resolve ``version`` or ``v<version>`` to an immutable commit SHA."""
    output = _git(
        [
            "ls-remote",
            "--tags",
            source,
            f"refs/tags/{version}",
            f"refs/tags/{version}^{{}}",
            f"refs/tags/v{version}",
            f"refs/tags/v{version}^{{}}",
        ]
    )
    refs: dict[str, str] = {}
    for line in output.splitlines():
        try:
            revision, ref = line.split("\t", 1)
        except ValueError:
            continue
        refs[ref] = revision
    for tag in (f"v{version}", version):
        peeled = refs.get(f"refs/tags/{tag}^{{}}")
        direct = refs.get(f"refs/tags/{tag}")
        if peeled or direct:
            return peeled or direct or ""
    raise PackageError(
        f"source {source!r} has no tag for version {version!r}",
        hint=f"Create and push tag v{version}, or choose an existing exact version.",
    )


def _checkout_git_revision(source: str, revision: str, destination: Path) -> None:
    """Clone one source and check out only its locked revision."""
    _git(["clone", "--quiet", "--no-checkout", source, str(destination)])
    _git(["checkout", "--quiet", "--detach", revision], cwd=destination)
    git_dir = destination / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)


def _validate_package_manifest(
    dependency: Dependency, package_manifest: Manifest
) -> None:
    """Ensure fetched package metadata agrees with the dependency request."""
    actual_version = package_manifest.project.get("version")
    if actual_version != dependency.version:
        raise PackageError(
            f"package {dependency.local_name!r} declares version {actual_version!r}, "
            f"expected {dependency.version!r}"
        )
    actual_name = package_manifest.project.get("name")
    expected_name = dependency.package
    if expected_name is not None and actual_name != expected_name:
        raise PackageError(
            f"package {dependency.local_name!r} declares name {actual_name!r}, "
            f"expected {expected_name!r}"
        )


def _tree_integrity(root: Path) -> str:
    """Hash a package tree canonically without VCS or managed dependency data."""
    digest = hashlib.sha256()
    ignored_roots = {".git", ".vln"}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in ignored_roots:
            continue
        if path.is_symlink():
            raise PackageError(f"package contains unsupported symlink: {rel}")
        if not path.is_file() or rel.as_posix() == "valiance.lock":
            continue
        encoded = rel.as_posix().encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return "sha256:" + digest.hexdigest()


def _manifest_dependency_records(manifest: Manifest) -> list[dict[str, str]]:
    """Return canonical direct intent for lockfile staleness checks."""
    return [
        {
            "name": item.local_name,
            "identity": item.identity,
            "source": item.source or "registry",
            "version": item.version,
        }
        for item in sorted(manifest.dependencies, key=lambda value: value.local_name)
    ]


def _read_lockfile(manifest: Manifest) -> dict[str, object]:
    """Read and validate the root lockfile container."""
    path = manifest.root / "valiance.lock"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PackageError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PackageError(f"invalid {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise PackageError("unsupported or invalid valiance.lock")
    return value


def _validate_lock_matches_manifest(
    manifest: Manifest, lock: dict[str, object]
) -> None:
    """Reject locked installs when direct dependency intent has changed."""
    if lock.get("manifest_dependencies") != _manifest_dependency_records(manifest):
        raise PackageError(
            "valiance.lock is out of date with valiance.toml",
            hint="Run `vln install` to resolve dependencies and refresh the lockfile.",
        )

def add_dependency(
    target: str,
    version: str,
    *,
    alias: str | None = None,
    start: Path | None = None,
    progress: ProgressCallback | None = None,
) -> Manifest:
    """Add or replace a project dependency and persist the manifest."""
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
        manifest.lints,
        manifest.builds,
    )
    write_manifest(updated)
    install(manifest.root, progress=progress)
    return updated


def remove_dependency(name: str, *, start: Path | None = None) -> Manifest:
    """Remove a project dependency and persist the manifest."""
    manifest = require_manifest(start)
    _validate_dependency_name(name)
    dependencies = _without_dependency(manifest.dependencies, name)
    if len(dependencies) == len(manifest.dependencies):
        raise PackageError(f"dependency {name!r} is not declared")
    updated = Manifest(manifest.root, manifest.project, manifest.entries, dependencies, manifest.lints, manifest.builds, manifest.formatting)
    write_manifest(updated)
    install(manifest.root)
    unused_dir = manifest.root / ".vln" / name
    if unused_dir.exists() and unused_dir.is_dir():
        shutil.rmtree(unused_dir)
    return updated


def upgrade_dependency(
    name: str,
    version: str,
    *,
    start: Path | None = None,
    progress: ProgressCallback | None = None,
) -> Manifest:
    """Update a dependency version and reinstall the resolved package set."""
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
    updated = Manifest(manifest.root, manifest.project, manifest.entries, dependencies, manifest.lints, manifest.builds, manifest.formatting)
    write_manifest(updated)
    install(manifest.root, progress=progress)
    return updated


def write_manifest(manifest: Manifest) -> None:
    """Serialize the project manifest to deterministic TOML."""
    lines = ["[project]"]
    for key, value in manifest.project.items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    lines.append("[entries]")
    for name, path in manifest.entries.items():
        lines.append(f"{name} = {_toml_value(path)}")
    for target in manifest.builds.values():
        lines.append("")
        lines.append(f"[build.{target.name}]")
        lines.append(f"kind = {_toml_value(target.kind)}")
        if target.entry is not None:
            lines.append(f"entry = {_toml_value(target.entry)}")
        if target.source is not None:
            lines.append(f"source = {_toml_value(target.source)}")
        if target.output is not None:
            lines.append(f"output = {_toml_value(target.output)}")
        lines.append(f"optimize = {_toml_value(target.optimize)}")
    lines.append("")
    lines.append("[lints]")
    lines.append(f"enabled = {_toml_value(manifest.lints.enabled)}")
    lines.append(f"disable = {_toml_value(list(manifest.lints.disabled))}")
    lines.append("")
    lines.append("[format]")
    lines.append(f"add = {_toml_value(list(manifest.formatting.add))}")
    lines.append("")
    lines.append("[dependencies]")
    for dependency in sorted(manifest.dependencies, key=lambda item: item.local_name):
        lines.extend(_dependency_lines(dependency))
    manifest.path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_lockfile(
    manifest: Manifest,
    *,
    resolved: list[dict[str, object]] | None = None,
) -> Path:
    """Write exact revisions, integrity, and the complete dependency graph."""
    dependencies = resolved if resolved is not None else []
    lock = {
        "version": 1,
        "package": {
            "name": manifest.project.get("name", manifest.root.name),
            "version": manifest.project.get("version", "0.0.0"),
        },
        "manifest_dependencies": _manifest_dependency_records(manifest),
        "dependencies": dependencies,
    }
    path = manifest.root / "valiance.lock"
    temporary = path.with_suffix(".lock.tmp")
    temporary.write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return path

def dependency_install_root(manifest: Manifest, name: str) -> Path:
    """Return the directory used for installed project dependencies."""
    dependency = manifest.dependency(name)
    if dependency is None:
        raise PackageError(f"dependency {name!r} is not declared")
    return manifest.root / ".vln" / dependency.local_name


def _parse_dependency(name: str, value: object) -> Dependency:
    """Parse dependency for project and dependency management."""
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
    """Validate dependency name for project and dependency management."""
    if name in RESERVED_DEPENDENCY_NAMES:
        raise PackageError(f"{name!r} is reserved and cannot be a dependency name")
    if not NAME_RE.fullmatch(name):
        raise PackageError(f"dependency name {name!r} is not a valid module component")


def _validate_exact_version(version: str) -> None:
    """Validate exact version for project and dependency management."""
    if not VERSION_RE.fullmatch(version):
        raise PackageError(f"version {version!r} is not an exact numeric version")


def _without_dependency(
    dependencies: tuple[Dependency, ...],
    name: str,
) -> tuple[Dependency, ...]:
    """Compute without dependency for project and dependency management."""
    return tuple(
        dependency for dependency in dependencies if dependency.local_name != name
    )


def _is_vcs_source(target: str) -> bool:
    """Return whether the value is VCS source."""
    return "/" in target


def _dependency_lines(dependency: Dependency) -> list[str]:
    """Compute dependency lines for project and dependency management."""
    value = dependency.manifest_value()
    if isinstance(value, str):
        return [f"{dependency.local_name} = {_toml_value(value)}"]
    body = ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items())
    return [f"{dependency.local_name} = {{ {body} }}"]


def _toml_value(value: object) -> str:
    """Compute toml value for project and dependency management."""
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise PackageError(f"cannot write TOML value {value!r}")
