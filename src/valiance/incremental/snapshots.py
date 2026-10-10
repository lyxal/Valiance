"""Immutable source views for untitled documents and transactional execution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from valiance.modules_system.modules import ModuleLoader


@dataclass(frozen=True, slots=True)
class RootSource:
    """Document identity and source; untitled documents have no disk path."""

    document_id: str
    source: str
    revision: int
    base_directory: Path
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class CapturedFile:
    """A present or absent source/artifact in the resolved import closure."""

    path: Path
    data: bytes | None
    overlay: bool = False
    version: int | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Captured root, overlays and dependency bytes suitable for worker IPC."""

    root: RootSource
    workspace_revision: int
    files: tuple[CapturedFile, ...]
    overlays: tuple[CapturedFile, ...]
    std_root: Path | None = None

    @property
    def identity(self) -> str:
        """Fingerprint identities, revisions, bytes and absent import candidates."""
        digest = hashlib.sha256()
        for item in (
            self.root.document_id,
            self.root.source,
            str(self.root.path),
            str(self.root.base_directory),
            str(self.root.revision),
            str(self.workspace_revision),
        ):
            data = item.encode("utf-8")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        for file in (*self.files, *self.overlays):
            digest.update(str(file.path).encode("utf-8") + b"\0")
            digest.update(str(file.version).encode("ascii") + b"\0")
            digest.update(
                b"missing" if file.data is None else hashlib.sha256(file.data).digest()
            )
        return digest.hexdigest()

    def module_loader(self) -> ModuleLoader:
        """Create a private loader that can only read the captured source view."""
        return ModuleLoader(
            std_root=self.std_root,
            base_directory=self.root.base_directory,
            source_provider=_FrozenSources((*self.files, *self.overlays)),
        )

    def disk_is_current(self) -> bool:
        """Reject changed or newly appeared disk dependencies before execution."""
        try:
            for file in self.files:
                if file.overlay:
                    continue
                current = file.path.read_bytes() if file.path.exists() else None
                if current != file.data:
                    return False
        except OSError:
            return False
        return True


class _FrozenSources:
    """Fail closed when resolution asks for a path absent from the snapshot."""

    def __init__(self, files: tuple[CapturedFile, ...]) -> None:
        """Index immutable records without adding a mutable disk fallback."""
        self.files = {file.path.resolve(): file.data for file in files}

    def exists(self, path: Path) -> bool:
        """Read captured existence, including nonexistent compiled candidates."""
        path = path.resolve()
        if path not in self.files:
            raise RuntimeError(f"module path was not captured: {path}")
        return self.files[path] is not None

    def read_bytes(self, path: Path) -> bytes:
        """Return captured bytes or the appropriate missing-file failure."""
        if not self.exists(path):
            raise FileNotFoundError(path)
        return self.files[path.resolve()]


class SourceCapture:
    """Record a coherent source view while the actual loader resolves imports."""

    def __init__(self, overlays: tuple[CapturedFile, ...]) -> None:
        """Capture overlays immediately and saved files at their first read."""
        self.overlays = {file.path.resolve(): file for file in overlays}
        self.files: dict[Path, CapturedFile] = {}

    def capture_configuration(self, start: Path) -> None:
        """Track project resolution/lint metadata, including absent nearer manifests.

        Resolution still uses the existing package helpers. Pre-execution checks
        reject a changed manifest or lock rather than executing its new policy.
        """
        directory = start.resolve()
        for parent in (directory, *directory.parents):
            manifest = parent / "valiance.toml"
            if self.exists(manifest):
                self.exists(parent / "valiance.lock")
                break

    def exists(self, path: Path) -> bool:
        """Freeze existence as well as bytes so new candidates cannot leak in."""
        path = path.resolve()
        if path not in self.files:
            self.files[path] = self.overlays.get(path) or CapturedFile(
                path,
                path.read_bytes() if path.exists() else None,
            )
        return self.files[path].data is not None

    def read_bytes(self, path: Path) -> bytes:
        """Read each resolved file at most once during closure capture."""
        if not self.exists(path):
            raise FileNotFoundError(path)
        return self.files[path.resolve()].data
