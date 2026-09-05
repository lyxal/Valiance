"""Structured explanations and cache inspection for incremental compilation."""
from __future__ import annotations
import json, shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from .store import ArtifactStore, ArtifactStoreError, STORE_SCHEMA

class ReasonCode(StrEnum):
    """Stable reason categories rendered by developer-facing commands."""
    REUSED = "reused"
    SOURCE_CHANGED = "source-changed"
    SEMANTIC_DEPENDENCY_CHANGED = "semantic-dependency-changed"
    IMPLEMENTATION_DEPENDENCY_CHANGED = "implementation-dependency-changed"
    OPTIONS_CHANGED = "options-changed"
    OUTPUT_CHANGED = "output-changed"
    FORCED = "forced"

@dataclass(frozen=True, slots=True)
class RebuildReason:
    """Describe one build decision without relying on rendered log text."""
    code: ReasonCode
    summary: str
    details: tuple[str, ...] = ()
    dependency: str | None = None

    def render(self, subject: str) -> str:
        """Render a deterministic multiline explanation for terminal or CI logs."""
        lines=[f"{self.summary} {subject}"]
        lines.extend(f"  {item}" for item in self.details)
        if self.dependency is not None: lines.append(f"  first changed dependency: {self.dependency}")
        return "\n".join(lines)

@dataclass(frozen=True, slots=True)
class CacheIssue:
    """Report one deterministic cache integrity failure."""
    kind: str
    identity: str
    message: str

@dataclass(frozen=True, slots=True)
class CacheReport:
    """Summarize incremental store records, objects, and integrity issues."""
    schema: int
    modules: tuple[dict[str, Any], ...]
    targets: tuple[dict[str, Any], ...]
    objects: tuple[str, ...]
    issues: tuple[CacheIssue, ...]

    @property
    def valid(self) -> bool:
        """Return whether every index and referenced object passed verification."""
        return not self.issues


def _portable_record(identity: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return stable inspection fields without exposing unnecessary absolute paths."""
    return {"identity":identity,"source":record.get("source"),"interface":record.get("interface"),"implementation":record.get("implementation"),"dependencies":record.get("dependencies",()),"artifact":record.get("artifact"),"disposition":record.get("disposition"),"reason":record.get("reason"),"reachability":record.get("implementations",())}


def inspect_cache(store: ArtifactStore) -> CacheReport:
    """Inspect cache indexes and objects while retaining corruption as data."""
    issues=[]; indexes={}
    for name in ("modules","targets"):
        try: indexes[name]=store.read_index(name)
        except ArtifactStoreError as exc: indexes[name]={}; issues.append(CacheIssue("index",name,str(exc)))
    objects=tuple(sorted(path.parent.name+path.name for path in store.objects.glob("*/*") if path.is_file()))
    for name,records in indexes.items():
        for identity,record in sorted(records.items()):
            digest=record.get("artifact")
            if not isinstance(digest,str): issues.append(CacheIssue("index",f"{name}:{identity}","missing artifact identity")); continue
            try: store.read(digest)
            except ArtifactStoreError as exc: issues.append(CacheIssue("object",digest,str(exc)))
    return CacheReport(STORE_SCHEMA,tuple(_portable_record(k,v) for k,v in sorted(indexes["modules"].items())),tuple(_portable_record(k,v) for k,v in sorted(indexes["targets"].items())),objects,tuple(sorted(issues,key=lambda x:(x.kind,x.identity,x.message))))


def render_cache_report(report: CacheReport) -> str:
    """Render stable cache inspection output suitable for redirected logs."""
    lines=[f"artifact schema: {report.schema}",f"modules: {len(report.modules)}",f"targets: {len(report.targets)}",f"objects: {len(report.objects)}",f"integrity: {'valid' if report.valid else 'invalid'}"]
    for group,records in (("module",report.modules),("target",report.targets)):
        for record in records:
            lines.append(f"{group} {record['identity']}")
            for key in ("source","interface","implementation","artifact","disposition","reason"):
                if record.get(key) is not None: lines.append(f"  {key}: {record[key]}")
            for dependency in record.get("dependencies") or (): lines.append(f"  dependency: {dependency[0]} {dependency[1]}")
    for issue in report.issues: lines.append(f"issue {issue.kind} {issue.identity}: {issue.message}")
    return "\n".join(lines)


def clean_cache(store: ArtifactStore) -> bool:
    """Remove generated incremental state while preserving source and final outputs."""
    existed=store.root.exists()
    if existed: shutil.rmtree(store.root)
    return existed
