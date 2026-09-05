"""Unified compilation database shared by all Valiance frontends."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from valiance.analysis import Analyser
from valiance.asts import ASTNode, TypedNode
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.runtime import Program, compile_program
from .store import ArtifactStore

@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    source_file: Path
    source: str
    syntax: tuple[ASTNode, ...]
    typed: tuple[TypedNode, ...]
    analyser: Analyser
    source_hash: str
    overlay: bool
    @property
    def successful(self) -> bool:
        """Return whether analysis completed without diagnostics."""
        return not self.analyser.diagnostics

@dataclass(frozen=True, slots=True)
class ExecutableSnapshot:
    analysis: AnalysisSnapshot
    program: Program
    optimize: bool

class CompilationDatabase:
    """Own disk snapshots, unsaved overlays, imports, and coherent analyses."""
    def __init__(self, project_root: Path | None = None) -> None:
        """Initialize workspace caches and optional project-root state."""
        self.project_root = project_root.resolve() if project_root else None
        self.module_loader = ModuleLoader()
        self._overlays: dict[Path, str] = {}
        self._versions: dict[Path, int] = {}
        self._analyses: dict[Path, AnalysisSnapshot] = {}
        self._imports: dict[Path, frozenset[Path]] = {}
        self._disk_hashes: dict[str, str] = {}

    def open_document(self, path: Path, source: str, *, version: int = 0) -> None:
        """Open an in-memory source overlay and invalidate its dependents."""
        path=path.resolve(); self._overlays[path]=source; self._versions[path]=version
        self.module_loader.source_overrides[path]=source; self._invalidate({path})

    def replace_document(self, path: Path, source: str, *, version: int | None = None) -> None:
        """Replace an open overlay and advance its document version."""
        path=path.resolve(); self._overlays[path]=source
        self._versions[path]=self._versions.get(path,0)+1 if version is None else version
        self.module_loader.source_overrides[path]=source; self._invalidate({path})

    def close_document(self, path: Path) -> None:
        """Close an overlay and resume reading the document from disk."""
        path=path.resolve(); self._overlays.pop(path,None); self._versions.pop(path,None)
        self.module_loader.source_overrides.pop(path,None); self._invalidate({path})

    def source_for(self,path:Path)->tuple[str,bool]:
        """Return current source and whether it comes from an overlay."""
        path=path.resolve()
        return (self._overlays[path],True) if path in self._overlays else (path.read_text(encoding='utf-8'),False)

    def analyse(self,path:Path)->AnalysisSnapshot:
        """Analyse the current document snapshot, reusing a valid cache entry."""
        path=path.resolve(); source,overlay=self.source_for(path)
        digest=hashlib.sha256(source.encode()).hexdigest(); cached=self._analyses.get(path)
        if cached and cached.source_hash==digest: return cached
        syntax=tuple(parse(source)); analyser=Analyser(source_file=path,module_loader=self.module_loader)
        typed=tuple(analyser.analyse(list(syntax)))
        snap=AnalysisSnapshot(path,source,syntax,typed,analyser,digest,overlay)
        self._analyses[path]=snap
        deps={}
        for identity,_ in self.module_loader.dependency_hashes_for(path):
            try:
                from valiance.modules_system.modules import dependency_path_from_identity
                dep=dependency_path_from_identity(identity)
                deps[self.module_loader.resolve(dep,current_file=path).resolve()]=None
            except Exception: pass
        self._imports[path]=frozenset(deps)
        return snap

    def compile_current(self,path:Path,*,optimize:bool=True,publish:bool=False)->ExecutableSnapshot:
        """Compile the current successful snapshot into an executable program."""
        analysis=self.analyse(path)
        if not analysis.successful: raise RuntimeError('current workspace snapshot contains type errors')
        return ExecutableSnapshot(analysis,compile_program(list(analysis.typed),optimize=optimize),optimize)

    def refresh_disk(self,files:Iterable[Path])->frozenset[Path]:
        """Refresh disk fingerprints and invalidate changed dependency closures."""
        changed=set()
        for path in files:
            path=path.resolve(); digest=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''
            if self._disk_hashes.get(str(path))!=digest: changed.add(path); self._disk_hashes[str(path)]=digest
        if changed: self._invalidate(changed)
        if self.project_root:
            out=self.project_root/'.vln/incremental/workspace-snapshot.json'; out.parent.mkdir(parents=True,exist_ok=True)
            ArtifactStore._atomic_write(out,json.dumps(self._disk_hashes,sort_keys=True).encode())
        return frozenset(changed)

    def _invalidate(self,changed:set[Path])->frozenset[Path]:
        """Invalidate changed paths and every cached transitive importer."""
        affected={p.resolve() for p in changed}; again=True
        while again:
            again=False
            for importer,deps in self._imports.items():
                if importer not in affected and deps & affected: affected.add(importer); again=True
        for p in affected: self._analyses.pop(p,None)
        self.module_loader.invalidate(affected)
        return frozenset(affected)
