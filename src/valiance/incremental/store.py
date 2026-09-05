"""Transactional content-addressed storage for incremental compiler artifacts."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

STORE_SCHEMA = 1


class ArtifactStoreError(Exception):
    """Report an invalid, corrupt, or unpublished incremental-store artifact."""


class ArtifactStore:
    """Publish immutable objects and mutable indexes with process-safe locking."""

    def __init__(self, project_root: Path) -> None:
        """Open the generated store below one resolved project root."""
        self.root = project_root.resolve() / ".vln" / "incremental"
        self.objects = self.root / "objects"
        self.indexes = self.root / "indexes"
        self.locks = self.root / "locks"
        self.temp = self.root / "temp"
        for directory in (self.objects, self.indexes, self.locks, self.temp):
            directory.mkdir(parents=True, exist_ok=True)
        schema = self.root / "schema"
        if schema.exists() and schema.read_text(encoding="ascii").strip() != str(STORE_SCHEMA):
            raise ArtifactStoreError("unsupported incremental artifact store schema")
        if not schema.exists():
            self._atomic_write(schema, f"{STORE_SCHEMA}\n".encode("ascii"))

    @staticmethod
    def digest(data: bytes) -> str:
        """Return the content address for immutable artifact bytes."""
        return hashlib.sha256(data).hexdigest()

    def object_path(self, digest: str) -> Path:
        """Return the sharded path for one validated SHA-256 object identity."""
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ArtifactStoreError("invalid incremental object identity")
        return self.objects / digest[:2] / digest[2:]

    @contextlib.contextmanager
    def writer_lock(self) -> Iterator[None]:
        """Serialize writers while allowing readers to access published files."""
        lock_path = self.locks / "writer.lock"
        lock_path.touch(exist_ok=True)
        with lock_path.open("rb+") as handle:
            try:
                import fcntl
            except ImportError:
                yield
                return
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def put(self, data: bytes, *, validate: Callable[[bytes], object] | None = None) -> str:
        """Validate and atomically publish immutable bytes by their content hash."""
        if validate is not None:
            validate(data)
        digest = self.digest(data)
        destination = self.object_path(digest)
        with self.writer_lock():
            if destination.exists():
                try:
                    self.read(digest, validate=validate)
                except ArtifactStoreError:
                    destination.unlink(missing_ok=True)
                else:
                    return digest
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(destination, data, temporary_directory=self.temp)
        return digest

    def read(
        self,
        digest: str,
        *,
        validate: Callable[[bytes], object] | None = None,
    ) -> bytes:
        """Read an immutable object and reject digest or format corruption."""
        path = self.object_path(digest)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ArtifactStoreError(f"incremental object {digest} is unavailable") from exc
        if self.digest(data) != digest:
            raise ArtifactStoreError(f"incremental object {digest} is corrupt")
        if validate is not None:
            try:
                validate(data)
            except Exception as exc:
                raise ArtifactStoreError(
                    f"incremental object {digest} failed validation"
                ) from exc
        return data

    def read_index(self, name: str) -> dict[str, dict[str, Any]]:
        """Return one complete index, treating a missing index as empty."""
        path = self._index_path(name)
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(f"incremental {name} index is corrupt") from exc
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(record, dict)
            for key, record in value.items()
        ):
            raise ArtifactStoreError(f"incremental {name} index is invalid")
        return value

    def publish_index(self, name: str, records: Mapping[str, Mapping[str, Any]]) -> None:
        """Atomically replace an index after all referenced objects are readable."""
        normalized = {key: dict(value) for key, value in records.items()}
        for record in normalized.values():
            digest = record.get("artifact")
            if isinstance(digest, str):
                self.read(digest)
        encoded = json.dumps(
            normalized, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with self.writer_lock():
            self._atomic_write(self._index_path(name), encoded)

    def update_index_record(
        self, name: str, identity: str, record: Mapping[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Merge one record under the writer lock without losing concurrent updates."""
        digest = record.get("artifact")
        if isinstance(digest, str):
            self.read(digest)
        with self.writer_lock():
            records = self.read_index(name)
            records[identity] = dict(record)
            encoded = json.dumps(
                records, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            self._atomic_write(self._index_path(name), encoded)
        return records

    def publish_output(self, output: Path, data: bytes) -> None:
        """Atomically replace a configured output with flushed complete bytes."""
        output.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(output, data)

    def collect_garbage(self) -> tuple[str, ...]:
        """Remove objects unreachable from current module and target indexes."""
        reachable: set[str] = set()
        for name in ("modules", "targets"):
            for record in self.read_index(name).values():
                digest = record.get("artifact")
                if isinstance(digest, str):
                    reachable.add(digest)
        removed = []
        with self.writer_lock():
            for path in self.objects.glob("*/*"):
                digest = path.parent.name + path.name
                if digest not in reachable:
                    path.unlink(missing_ok=True)
                    removed.append(digest)
        return tuple(sorted(removed))

    def _index_path(self, name: str) -> Path:
        """Return a path for one known mutable build index."""
        if name not in {"modules", "targets"}:
            raise ArtifactStoreError(f"unknown incremental index {name!r}")
        return self.indexes / name

    @staticmethod
    def _atomic_write(
        destination: Path,
        data: bytes,
        *,
        temporary_directory: Path | None = None,
    ) -> None:
        """Flush bytes and atomically replace a destination on its filesystem."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        directory = destination.parent
        if temporary_directory is not None:
            try:
                if os.stat(temporary_directory).st_dev == os.stat(directory).st_dev:
                    directory = temporary_directory
            except OSError:
                pass
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            try:
                directory_fd = os.open(destination.parent, os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
