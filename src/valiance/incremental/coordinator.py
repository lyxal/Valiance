"""Coordinate reusable source, module, and executable build artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from valiance.analysis import Analyser
from valiance.modules_system.modules import (
    ModuleLoader,
    collect_module_exports,
    dependency_path_from_identity,
)
from valiance.parsing import parse
from valiance.runtime import compile_program
from valiance.runtime.compiled_module import build_module, dumps_module, load_module_file
from valiance.runtime.serialization import dumps, loads

from .store import ArtifactStore, ArtifactStoreError
from .inspection import ReasonCode, RebuildReason


def load_module_file_bytes(data: bytes) -> object:
    """Validate module bytes without requiring an intermediate filesystem path."""
    from valiance.runtime.compiled_module import loads_module

    return loads_module(data)


class BuildDisposition(StrEnum):
    """Describe the most expensive stage performed for one build unit."""

    REUSED = "reused"
    REPARSED = "reparsed"
    REANALYSED = "reanalysed"
    RECOMPILED = "recompiled"
    RELINKED = "relinked"
    FAILED = "failed"


@dataclass(frozen=True)
class BuildResult:
    """Report the artifact path, disposition, and machine-readable reason."""

    output: Path
    disposition: BuildDisposition
    reason: RebuildReason


class CompilationCoordinator:
    """Schedule compilation and persist target freshness across processes."""

    def __init__(self, project_root: Path | None = None) -> None:
        """Create a coordinator rooted at a project or a direct-file directory."""
        self.project_root = project_root.resolve() if project_root is not None else None
        self.module_loader = ModuleLoader()
        self._active_modules: set[Path] = set()
        self.store = ArtifactStore(self.project_root) if self.project_root else None
        self._targets = self._read_index("targets")
        self._modules = self._read_index("modules")

    def _read_index(self, name: str) -> dict[str, dict[str, object]]:
        """Read one store index, rebuilding from source if it is corrupt."""
        if self.store is None:
            return {}
        try:
            return self.store.read_index(name)
        except ArtifactStoreError:
            return {}

    def _module_identity(self, source_file: Path, module_name: str) -> str:
        """Return a stable project-relative build identity for one module."""
        if self.project_root is None:
            return f"direct:{source_file.resolve()}:{module_name}"
        try:
            relative = source_file.resolve().relative_to(self.project_root)
        except ValueError:
            return f"external:{source_file.resolve()}:{module_name}"
        return f"project:{relative.as_posix()}:{module_name}"

    def _publish_artifact(
        self,
        output: Path,
        data: bytes,
        *,
        validate: Callable[[bytes], object],
    ) -> str:
        """Validate and atomically publish object bytes and configured output."""
        validate(data)
        if self.store is None:
            ArtifactStore._atomic_write(output, data)
            return self._sha256(data)
        digest = self.store.put(data, validate=validate)
        self.store.publish_output(output, data)
        return digest

    @staticmethod
    def _sha256(data: bytes) -> str:
        """Return a lowercase SHA-256 digest for artifact and source checks."""
        return hashlib.sha256(data).hexdigest()

    def _dependencies_current(
        self, source_file: Path, dependencies: tuple[tuple[str, str], ...]
    ) -> bool:
        """Return whether all recorded semantic dependency interfaces still match."""
        for identity, expected in dependencies:
            try:
                path = dependency_path_from_identity(identity)
                exports = self.module_loader.load(path, current_file=source_file)
                actual = self.module_loader.interface_hash_for(exports)
            except Exception:
                return False
            if actual != expected:
                return False
        return True

    def _dependency_implementations_current(
        self, source_file: Path, dependencies: tuple[tuple[str, str], ...]
    ) -> bool:
        """Return whether recorded dependency implementations still match."""
        for identity, expected in dependencies:
            try:
                path = dependency_path_from_identity(identity)
                exports = self.module_loader.load(path, current_file=source_file)
                actual = self.module_loader.implementation_hash_for(exports)
            except Exception:
                return False
            if actual != expected:
                return False
        return True

    def build_module(
        self,
        source_file: Path,
        output: Path,
        *,
        module_name: str,
        optimize: bool = True,
        incremental: bool = True,
        rebuild: bool = False,
    ) -> BuildResult:
        """Build or reuse one module and recursively publish imported modules."""
        source_file = source_file.resolve()
        output = output.resolve()
        source_bytes = source_file.read_bytes()
        source_digest = self._sha256(source_bytes)
        module_identity = self._module_identity(source_file, module_name)
        record = self._modules.get(module_identity, {})
        if incremental and not rebuild and not output.is_file() and self.store is not None:
            digest = record.get("artifact")
            if isinstance(digest, str):
                try:
                    restored = self.store.read(digest, validate=load_module_file_bytes)
                except ArtifactStoreError:
                    pass
                else:
                    self.store.publish_output(output, restored)
        if incremental and not rebuild and output.is_file():
            try:
                candidate = load_module_file(output)
                output_matches = candidate.module_name == module_name
                options_match = candidate.implementation_options == (
                    f"optimize={str(optimize).lower()}"
                )
                dependencies_match = self._dependencies_current(
                    source_file, candidate.dependency_hashes
                )
            except Exception:
                output_matches = options_match = dependencies_match = False
            if (
                output_matches
                and options_match
                and candidate.source_hash == source_digest
                and dependencies_match
            ):
                return BuildResult(
                    output,
                    BuildDisposition.REUSED,
                    RebuildReason(ReasonCode.REUSED, "Reused module", ("source unchanged", "dependency interfaces unchanged", "compiler ABI unchanged")),
                )

        if source_file in self._active_modules:
            raise RuntimeError(f"cyclic module artifact dependency at {source_file}")
        self._active_modules.add(source_file)
        try:
            source = source_bytes.decode("utf-8")
            program = parse(source)
            analyser = Analyser(module_loader=self.module_loader, source_file=source_file)
            typed = analyser.analyse(program)
            if analyser.diagnostics:
                raise RuntimeError("; ".join(analyser.diagnostics))
            bytecode = compile_program(typed, optimize=optimize)
            interface = collect_module_exports(module_name, program, typed, analyser)
            dependencies = self.module_loader.dependency_hashes_for(source_file)
            for identity, _ in dependencies:
                path = dependency_path_from_identity(identity)
                dependency_source = self.module_loader.resolve(path, current_file=source_file)
                if (
                    dependency_source.exists()
                    and dependency_source.resolve() != source_file
                    and dependency_source.resolve() not in self._active_modules
                ):
                    self.build_module(
                        dependency_source,
                        dependency_source.with_suffix(".vbcm"),
                        module_name=dependency_source.stem,
                        optimize=optimize,
                        incremental=incremental,
                        rebuild=rebuild,
                    )
            artifact = build_module(
                module_name,
                source,
                bytecode,
                analysed_interface=interface,
                dependency_hashes=dependencies,
                implementation_options=f"optimize={str(optimize).lower()}",
            )
            artifact_bytes = dumps_module(artifact)
            digest = self._publish_artifact(
                output, artifact_bytes, validate=load_module_file_bytes
            )
            if self.store is not None:
                record = {
                    "artifact": digest,
                    "source": source_digest,
                    "interface": artifact.interface_hash,
                    "implementation": artifact.implementation_hash,
                    "dependencies": [list(item) for item in dependencies],
                    "disposition": BuildDisposition.RECOMPILED.value,
                    "reason": ReasonCode.SOURCE_CHANGED.value,
                }
                self._modules = self.store.update_index_record(
                    "modules", module_identity, record
                )
            return BuildResult(
                output,
                BuildDisposition.RECOMPILED,
                RebuildReason(ReasonCode.SOURCE_CHANGED, "Recompiled module", ("source or dependency state changed",)),
            )
        finally:
            self._active_modules.discard(source_file)

    def build_executable(
        self,
        source_file: Path,
        output: Path,
        *,
        target_identity: str,
        optimize: bool = True,
        incremental: bool = True,
        rebuild: bool = False,
    ) -> BuildResult:
        """Build or reuse an executable target with semantic dependency validation."""
        source_file = source_file.resolve()
        output = output.resolve()
        source = source_file.read_text(encoding="utf-8")
        source_hash = self._sha256(source.encode("utf-8"))
        key = target_identity
        record = self._targets.get(key, {})
        if incremental and not rebuild and not output.is_file() and self.store is not None:
            digest = record.get("artifact")
            if isinstance(digest, str):
                try:
                    restored = self.store.read(digest, validate=loads)
                except ArtifactStoreError:
                    pass
                else:
                    self.store.publish_output(output, restored)
        dependencies_value = record.get("dependencies", [])
        dependencies = tuple(
            (str(item[0]), str(item[1]))
            for item in dependencies_value
            if isinstance(item, list) and len(item) == 2
        )
        implementations_value = record.get("implementations", [])
        implementations = tuple(
            (str(item[0]), str(item[1]))
            for item in implementations_value
            if isinstance(item, list) and len(item) == 2
        )
        reusable = (
            incremental
            and not rebuild
            and output.is_file()
            and record.get("source") == source_hash
            and record.get("optimize") is optimize
            and record.get("output") == self._sha256(output.read_bytes())
            and len(dependencies) == len(dependencies_value)
            and len(implementations) == len(implementations_value)
            and self._dependencies_current(source_file, dependencies)
            and self._dependency_implementations_current(
                source_file, implementations
            )
        )
        if reusable:
            return BuildResult(
                output,
                BuildDisposition.REUSED,
                RebuildReason(ReasonCode.REUSED, "Reused target", ("source unchanged", "dependency interfaces unchanged", "dependency implementations unchanged", "linked output unchanged")),
            )

        program = parse(source)
        analyser = Analyser(module_loader=self.module_loader, source_file=source_file)
        typed = analyser.analyse(program)
        if analyser.diagnostics:
            raise RuntimeError("; ".join(analyser.diagnostics))
        bytecode = dumps(compile_program(typed, optimize=optimize))
        digest = self._publish_artifact(output, bytecode, validate=loads)
        direct_dependencies = self.module_loader.dependency_hashes_for(source_file)
        direct_implementations = (
            self.module_loader.dependency_implementation_hashes_for(source_file)
        )
        record = {
            "artifact": digest,
            "source": source_hash,
            "optimize": optimize,
            "output": self._sha256(bytecode),
            "dependencies": [list(item) for item in direct_dependencies],
            "implementations": [list(item) for item in direct_implementations],
            "disposition": BuildDisposition.RELINKED.value,
            "reason": ReasonCode.IMPLEMENTATION_DEPENDENCY_CHANGED.value,
        }
        if self.store is not None:
            self._targets = self.store.update_index_record("targets", key, record)
        return BuildResult(
            output,
            BuildDisposition.RELINKED,
            RebuildReason(ReasonCode.IMPLEMENTATION_DEPENDENCY_CHANGED, "Relinked target", ("current workspace required analysis and linking",)),
        )
