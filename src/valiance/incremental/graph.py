"""Canonical module graph discovery and strongly connected components."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from valiance.asts import ImportNode
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse


@dataclass(frozen=True)
class ModuleComponent:
    """One deterministic strongly connected module component."""

    members: tuple[Path, ...]

    @property
    def cyclic(self) -> bool:
        """Return whether the component contains multiple mutually reachable modules."""
        return len(self.members) > 1


def discover_module_graph(root: Path, loader: ModuleLoader) -> dict[Path, tuple[Path, ...]]:
    """Discover reachable source imports using ModuleLoader resolution semantics."""
    graph: dict[Path, tuple[Path, ...]] = {}

    def visit(source_file: Path) -> None:
        """Parse one source once and recursively visit source-backed imports."""
        source_file = source_file.resolve()
        if source_file in graph or not source_file.exists():
            return
        program = parse(source_file.read_text(encoding="utf-8"))
        dependencies: set[Path] = set()
        for node in program:
            if not isinstance(node, ImportNode):
                continue
            for spec in node.specs:
                resolved = loader.resolve(spec.path, current_file=source_file).resolve()
                if resolved.exists():
                    dependencies.add(resolved)
        graph[source_file] = tuple(sorted(dependencies, key=lambda item: item.as_posix()))
        for dependency in graph[source_file]:
            visit(dependency)

    visit(root)
    return graph


def strongly_connected_components(
    graph: dict[Path, tuple[Path, ...]],
) -> tuple[ModuleComponent, ...]:
    """Return Tarjan components in deterministic dependency-first order."""
    index = 0
    indexes: dict[Path, int] = {}
    lowlinks: dict[Path, int] = {}
    stack: list[Path] = []
    active: set[Path] = set()
    components: list[ModuleComponent] = []

    def connect(node: Path) -> None:
        """Visit one node and emit its component when its root is complete."""
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        active.add(node)
        for dependency in graph.get(node, ()):
            if dependency not in indexes:
                connect(dependency)
                lowlinks[node] = min(lowlinks[node], lowlinks[dependency])
            elif dependency in active:
                lowlinks[node] = min(lowlinks[node], indexes[dependency])
        if lowlinks[node] != indexes[node]:
            return
        members = []
        while True:
            member = stack.pop()
            active.remove(member)
            members.append(member)
            if member == node:
                break
        components.append(
            ModuleComponent(tuple(sorted(members, key=lambda item: item.as_posix())))
        )

    for node in sorted(graph, key=lambda item: item.as_posix()):
        if node not in indexes:
            connect(node)
    return tuple(components)
