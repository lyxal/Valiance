"""Module loading and import/export helpers for Valiance source files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from valiance.asts import (
    DefineNode,
    FunctionNode,
    ImportPath,
    ImportSpec,
    Symbol,
    TypedFunctionNode,
)
from valiance.asts.nodes import TypedNode
from valiance.parsing import parse


@dataclass(frozen=True)
class ModuleDefinition:
    """One analysed definition exported or required by a module."""

    name: Symbol
    typed: TypedFunctionNode
    public: bool = False


@dataclass(frozen=True)
class ModuleExports:
    """The reusable symbol surface of an analysed module."""

    module_name: str
    definitions: tuple[ModuleDefinition, ...] = ()

    def public_definitions(self) -> tuple[ModuleDefinition, ...]:
        return tuple(definition for definition in self.definitions if definition.public)


class ModuleLoadError(Exception):
    """Raised when an import cannot be resolved or analysed."""


@dataclass
class ModuleLoader:
    """Resolve and analyse source modules, caching by absolute file path."""

    std_root: Path | None = None
    _cache: dict[Path, ModuleExports] = field(default_factory=dict)
    _loading: set[Path] = field(default_factory=set)

    def load(
        self,
        path: ImportPath,
        *,
        current_file: Path | None = None,
    ) -> ModuleExports:
        source_file = self.resolve(path, current_file=current_file)
        if source_file in self._cache:
            return self._cache[source_file]
        if source_file in self._loading:
            return ModuleExports(_module_name(path))
        self._loading.add(source_file)
        try:
            source = source_file.read_text(encoding="utf-8")
            program = parse(source)
            from valiance.analysis import Analyser

            analyser = Analyser(module_loader=self, source_file=source_file)
            typed = analyser.analyse(program)
            if analyser.diagnostics:
                joined = "; ".join(analyser.diagnostics)
                raise ModuleLoadError(f"{source_file}: {joined}")
            exports = ModuleExports(
                _module_name(path),
                _module_definitions(program, typed),
            )
            self._cache[source_file] = exports
            return exports
        except OSError as exc:
            message = f"could not read module {source_file}: {exc}"
            raise ModuleLoadError(message) from exc
        finally:
            self._loading.discard(source_file)

    def resolve(
        self,
        path: ImportPath,
        *,
        current_file: Path | None = None,
    ) -> Path:
        if path.root == Symbol("@"):
            raise ModuleLoadError("installed package imports are not available yet")
        if path.parts and path.parts[0] == "std":
            if self.std_root is None:
                root = Path(__file__).parent / "std"
            else:
                root = self.std_root
            return _source_path(root, path.parts[1:])
        if path.root == Symbol("~"):
            root = _project_root(current_file)
            return _source_path(root, path.parts)
        if current_file is None:
            raise ModuleLoadError("local imports require a source file")
        return _source_path(current_file.parent, path.parts)


def import_definitions(
    exports: ModuleExports,
    spec: ImportSpec,
) -> tuple[ModuleDefinition, ...]:
    """Return exported definitions selected by an import spec."""
    public = exports.public_definitions()
    if spec.components:
        by_name = {definition.name: definition for definition in public}
        selected: list[ModuleDefinition] = []
        for component in spec.components:
            definition = by_name.get(component.name)
            if definition is None:
                raise ModuleLoadError(
                    f"module {exports.module_name!r} has no public "
                    f"component {component.name.text!r}"
                )
            selected.append(
                _renamed_definition(
                    definition,
                    component.alias or component.name,
                )
            )
        return tuple(selected)

    namespace = spec.alias or Symbol(exports.module_name.rsplit(".", 1)[-1])
    return tuple(
        _renamed_definition(
            definition,
            Symbol(f"{namespace.text}.{definition.name.text}"),
        )
        for definition in public
    )


def _module_definitions(
    program: list,
    typed: list[TypedNode],
) -> tuple[ModuleDefinition, ...]:
    public_names = {
        node.name
        for node in program
        if isinstance(node, DefineNode) and node.visibility == Symbol("public")
    }
    result: list[ModuleDefinition] = []
    for typed_node in typed:
        if not isinstance(typed_node, TypedFunctionNode):
            continue
        node = typed_node.node
        if not isinstance(node, DefineNode):
            continue
        result.append(
            ModuleDefinition(
                node.name,
                typed_node,
                public=node.name in public_names,
            )
        )
    return tuple(result)


def _renamed_definition(
    definition: ModuleDefinition,
    name: Symbol,
) -> ModuleDefinition:
    node = definition.typed.node
    if not isinstance(node, DefineNode):
        return definition
    renamed = DefineNode(
        name,
        FunctionNode(
            params=node.function.params,
            body=node.function.body,
            returns=node.function.returns,
            location=node.function.location,
        ),
        node.annotations,
        node.is_multi,
        node.visibility,
        location=node.location,
    )
    typed = TypedFunctionNode(renamed, definition.typed.typ, definition.typed.overloads)
    return ModuleDefinition(name, typed, definition.public)


def _source_path(root: Path, parts: tuple[str, ...]) -> Path:
    if not parts:
        raise ModuleLoadError("empty module path")
    return root.joinpath(*parts).with_suffix(".vlnc").resolve()


def _project_root(current_file: Path | None) -> Path:
    start = Path.cwd() if current_file is None else current_file.resolve().parent
    for parent in (start, *start.parents):
        if (parent / "valiance.toml").exists():
            return parent
    return start


def _module_name(path: ImportPath) -> str:
    if path.root == Symbol("@"):
        return "@" + ".".join(path.parts)
    if path.root == Symbol("~"):
        return "~" + ".".join(path.parts)
    return ".".join(path.parts)
