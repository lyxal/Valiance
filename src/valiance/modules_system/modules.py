"""Module loading and import/export helpers for Valiance source files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import valiance.types as T
from valiance.asts import (
    DefineNode,
    FunctionNode,
    ImportPath,
    ImportSpec,
    ObjectNode,
    Symbol,
    TagDeclarationNode,
    TagOverlayNode,
    TypedFunctionNode,
)
from valiance.asts.nodes import TypedNode
from valiance.asts.object_constructors import constructor_definitions
from valiance.modules_system.packages import (
    PackageError,
    dependency_install_root,
    find_project_root,
    load_manifest,
)
from valiance.parsing import parse


@dataclass(frozen=True)
class ModuleDefinition:
    """One analysed definition exported or required by a module."""

    name: Symbol
    typed: TypedFunctionNode
    public: bool = False
    attached_tag: Symbol | None = None


@dataclass(frozen=True)
class ModuleObject:
    """One analysed object-like declaration exported by a module."""

    name: Symbol
    typed: TypedNode
    public: bool = False
    friendly_definitions: tuple[DefineNode, ...] = ()
    import_friendly: bool = False


@dataclass(frozen=True)
class ModuleExports:
    """The reusable symbol surface of an analysed module."""

    module_name: str
    definitions: tuple[ModuleDefinition, ...] = ()
    objects: tuple[ModuleObject, ...] = ()
    tags: tuple[T.DataTagDefinition, ...] = ()
    overlays: tuple[T.TagOverlayDefinition, ...] = ()
    runtime_prelude: tuple[TypedNode, ...] = ()

    def public_definitions(self) -> tuple[ModuleDefinition, ...]:
        """Return the definitions exported publicly by this module."""
        return tuple(definition for definition in self.definitions if definition.public)

    def public_objects(self) -> tuple[ModuleObject, ...]:
        """Return the object declarations exported publicly by this module."""
        return tuple(obj for obj in self.objects if obj.public)


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
        """Load, analyse, and cache a module and its exported facts."""
        source_file = self.resolve(path, current_file=current_file)
        native_exports = _native_std_exports(path)
        if source_file in self._cache:
            return self._cache[source_file]
        if source_file in self._loading:
            return ModuleExports(_module_name(path))
        if native_exports is not None and not source_file.exists():
            return native_exports
        self._loading.add(source_file)
        try:
            source = source_file.read_text(encoding="utf-8")
            program = parse(source)
            from valiance.analysis import Analyser
            from valiance.elements.builtins import default_environment
            from valiance.elements.stdlib_native import install_native_stdlib

            env = None
            if path.parts and path.parts[0] == "std":
                env = install_native_stdlib(
                    default_environment().child_scope(),
                    path.parts[-1],
                )
            analyser = Analyser(env=env, module_loader=self, source_file=source_file)
            typed = analyser.analyse(program)
            if analyser.diagnostics:
                joined = "; ".join(analyser.diagnostics)
                raise ModuleLoadError(f"{source_file}: {joined}")
            definitions = _module_definitions(program, typed)
            objects = _module_objects(typed)
            tags = _module_tags(program, analyser.env)
            overlays = _module_overlays(program, analyser.env)
            if native_exports is not None:
                definitions = native_exports.definitions + definitions
            exports = ModuleExports(
                _module_name(path),
                definitions,
                objects,
                tags,
                overlays,
                analyser.runtime_prelude,
            )
            self._cache[source_file] = exports
            return exports
        except OSError as exc:
            if native_exports is not None:
                return native_exports
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
        """Resolve an import path relative to the requesting source file."""
        if path.root == Symbol("dep"):
            return self._resolve_dependency(path, current_file=current_file)
        if path.parts and path.parts[0] == "std":
            if self.std_root is None:
                root = Path(__file__).parent.parent / "std"
            else:
                root = self.std_root
            return _source_path(root, path.parts[1:])
        if path.root is None and len(path.parts) == 1:
            native_exports = _native_std_exports(path)
            if native_exports is not None:
                root = Path(__file__).parent.parent / "std"
                return _source_path(root, path.parts)
        if path.root == Symbol("root"):
            root = _project_root(current_file)
            if root is None:
                raise ModuleLoadError("root imports require an enclosing valiance.toml")
            return _source_path(root, path.parts)
        if current_file is None:
            raise ModuleLoadError("local imports require a source file")
        return _source_path(current_file.parent, path.parts)

    def _resolve_dependency(
        self,
        path: ImportPath,
        *,
        current_file: Path | None = None,
    ) -> Path:
        """Resolve dependency during module loading and import resolution."""
        if not path.parts:
            raise ModuleLoadError("dep imports require a dependency name")
        project_root = _project_root(current_file)
        if project_root is None:
            raise ModuleLoadError("dep imports require an enclosing valiance.toml")
        dependency_name = path.parts[0]
        try:
            manifest = load_manifest(project_root)
            package_root = dependency_install_root(manifest, dependency_name)
        except PackageError as exc:
            raise ModuleLoadError(str(exc)) from exc
        module_parts = path.parts[1:] or (dependency_name,)
        return _source_path(package_root, module_parts)


def import_definitions(
    exports: ModuleExports,
    spec: ImportSpec,
) -> tuple[ModuleDefinition, ...]:
    """Return exported definitions selected by an import spec."""
    public = exports.public_definitions()
    public_objects = {obj.name for obj in exports.public_objects()}
    if spec.components:
        by_name = {definition.name: definition for definition in public}
        selected: list[ModuleDefinition] = []
        for component in spec.components:
            if component.kind == Symbol("tag"):
                tag_name = Symbol(component.name.text.removeprefix("#"))
                selected.extend(
                    _renamed_definition(definition, definition.name)
                    for definition in public
                    if definition.attached_tag == tag_name
                )
                continue
            if component.kind is not None:
                continue
            definition = by_name.get(component.name)
            if definition is None:
                if component.name in public_objects:
                    continue
                raise ModuleLoadError(
                    f"module {exports.module_name!r} has no public "
                    f"component {component.name.text!r}"
                )
            definition = _select_overloads(definition, component, exports.module_name)
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
            _namespaced_symbol(definition.name, namespace),
        )
        for definition in public
    )


def import_objects(
    exports: ModuleExports,
    spec: ImportSpec,
) -> tuple[ModuleObject, ...]:
    """Return exported object-like declarations selected by an import spec."""
    public = exports.public_objects()
    by_name = {obj.name: obj for obj in public}
    if spec.components:
        selected: list[ModuleObject] = []
        for component in spec.components:
            if component.kind is not None:
                continue
            obj = by_name.get(component.name)
            if obj is None:
                continue
            selected.append(
                _renamed_object(
                    obj,
                    component.alias or component.name,
                    import_friendly=True,
                )
            )
        return tuple(selected)

    namespace = spec.alias or Symbol(exports.module_name.rsplit(".", 1)[-1])
    return tuple(
        _renamed_object(
            obj,
            _namespaced_symbol(obj.name, namespace),
            friendly_prefix=namespace,
            import_friendly=True,
        )
        for obj in public
    )


def import_environment_facts(
    exports: ModuleExports,
    spec: ImportSpec,
    env: T.Environment,
) -> None:
    """Install exported non-callable environment facts selected by an import."""
    if not spec.components:
        return
    tags = {Symbol(f"#{tag.name.text}"): tag for tag in exports.tags}
    for component in spec.components:
        if component.kind != Symbol("tag"):
            continue
        tag = tags.get(component.name)
        if tag is None:
            raise ModuleLoadError(
                f"module {exports.module_name!r} has no public tag "
                f"{component.name.text!r}"
            )
        env.define_tag(tag.name, tag.kind)
        for overlay in exports.overlays:
            if overlay.tag == tag.name:
                env.define_tag_overlay(
                    overlay.tag,
                    overlay.element,
                    overlay.overload,
                    public=overlay.public,
                )


def _module_definitions(
    program: list,
    typed: list[TypedNode],
) -> tuple[ModuleDefinition, ...]:
    """Collect the definitions for module during module loading and import resolution."""
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
                attached_tag=(
                    Symbol(node.attached_tag.name)
                    if node.attached_tag is not None
                    else None
                ),
            )
        )
    return tuple(result)


def _module_objects(
    typed: list[TypedNode],
) -> tuple[ModuleObject, ...]:
    """Compute module objects during module loading and import resolution."""
    friendly_by_owner: dict[Symbol, list[DefineNode]] = {}
    for typed_node in typed:
        node = typed_node.node
        if not isinstance(node, ObjectNode):
            continue
        if node.kind == Symbol("object") and node.target is not None:
            friendly_by_owner.setdefault(node.name, []).extend(node.definitions)

    result: list[ModuleObject] = []
    for typed_node in typed:
        node = typed_node.node
        if not isinstance(node, ObjectNode):
            continue
        if node.kind == Symbol("object") and node.target is not None:
            continue
        result.append(
            ModuleObject(
                node.name,
                typed_node,
                public=node.visibility == Symbol("public"),
                friendly_definitions=(
                    node.definitions
                    + tuple(friendly_by_owner.get(node.name, ()))
                ),
            )
        )
    return tuple(result)


def _module_tags(program: list, env) -> tuple[T.DataTagDefinition, ...]:
    """Compute module tags during module loading and import resolution."""
    public = {
        Symbol(node.tag.name)
        for node in program
        if isinstance(node, TagDeclarationNode)
        and node.visibility == Symbol("public")
        and node.disjoint is None
    }
    return tuple(
        definition
        for name in public
        if (definition := env.lookup_tag(name)) is not None
    )


def _module_overlays(program: list, env) -> tuple[T.TagOverlayDefinition, ...]:
    """Compute module overlays during module loading and import resolution."""
    public_tags = {
        Symbol(node.tag.name)
        for node in program
        if isinstance(node, TagOverlayNode) and node.visibility == Symbol("public")
    }
    overlays: list[T.TagOverlayDefinition] = []
    for definitions in env.tag_overlays.values():
        overlays.extend(
            definition for definition in definitions if definition.tag in public_tags
        )
    return tuple(overlays)


def _renamed_definition(
    definition: ModuleDefinition,
    name: Symbol,
) -> ModuleDefinition:
    """Build the definition for renamed during module loading and import resolution."""
    node = definition.typed.node
    if not isinstance(node, DefineNode):
        return definition
    renamed = _renamed_define_node(node, name)
    typed = TypedFunctionNode(
        renamed,
        definition.typed.typ,
        definition.typed.overloads,
        definition.typed.dispatch_plan,
    )
    return ModuleDefinition(name, typed, definition.public, definition.attached_tag)


def _renamed_object(
    obj: ModuleObject,
    name: Symbol,
    *,
    friendly_prefix: Symbol | None = None,
    import_friendly: bool = False,
) -> ModuleObject:
    """Compute renamed object during module loading and import resolution."""
    node = obj.typed.node
    if not isinstance(node, ObjectNode):
        return obj
    explicit_constructors = constructor_definitions(
        node.name,
        obj.friendly_definitions,
    )
    renamed_constructors = tuple(
        _renamed_define_node(definition, name)
        for definition in explicit_constructors
    )
    friendly_definitions = tuple(
        definition
        for definition in obj.friendly_definitions
        if definition not in explicit_constructors
    )
    definitions = renamed_constructors
    if import_friendly:
        definitions += _renamed_friendly_definitions(
            friendly_definitions,
            friendly_prefix,
        )
    renamed = ObjectNode(
        node.kind,
        name,
        node.generics,
        node.target,
        node.fields,
        definitions,
        node.requirements,
        node.variants,
        node.enum_members,
        node.annotations,
        node.generic_variances,
        node.generic_constraints,
        node.visibility,
        location=node.location,
    )
    return ModuleObject(
        name,
        TypedNode(renamed, obj.typed.typ),
        obj.public,
        definitions,
        import_friendly,
    )


def _renamed_define_node(node: DefineNode, name: Symbol) -> DefineNode:
    """Compute renamed define node during module loading and import resolution."""
    return DefineNode(
        name,
        FunctionNode(
            generics=node.function.generics,
            generic_variances=node.function.generic_variances,
            params=node.function.params,
            body=node.function.body,
            returns=node.function.returns,
            where_clause=node.function.where_clause,
            element_tags=node.function.element_tags,
            annotations=node.function.annotations,
            element_tags_explicit=node.function.element_tags_explicit,
            companion_tags_allowed=node.function.companion_tags_allowed,
            generic_constraints=node.function.generic_constraints,
            location=node.function.location,
        ),
        node.annotations,
        node.is_multi,
        node.visibility,
        node.generics,
        node.generic_variances,
        node.generic_constraints,
        node.attached_tag,
        location=node.location,
    )


def _renamed_friendly_definitions(
    definitions: tuple[DefineNode, ...],
    prefix: Symbol | None,
) -> tuple[DefineNode, ...]:
    """Collect the definitions for renamed friendly during module loading and import resolution."""
    if prefix is None:
        return definitions
    return tuple(
        _renamed_define_node(
            definition,
            _namespaced_symbol(definition.name, prefix),
        )
        for definition in definitions
    )


def _namespaced_symbol(name: Symbol, prefix: Symbol) -> Symbol:
    """Compute namespaced symbol during module loading and import resolution."""
    return Symbol(name.text, (*prefix.namespace, prefix.text, *name.namespace))


def _select_overloads(
    definition: ModuleDefinition,
    component,
    module_name: str,
) -> ModuleDefinition:
    """Select overloads during module loading and import resolution."""
    if component.signature is None and not component.exclusions:
        return definition
    overloads = _definition_overloads(definition)
    if component.signature is not None:
        selected = tuple(
            overload
            for overload in overloads
            if _same_signature(overload.params, component.signature)
        )
        if not selected:
            raise ModuleLoadError(
                f"module {module_name!r} has no overload "
                f"{component.name.text}{_show_signature(component.signature)}"
            )
    else:
        exclusions = set(component.exclusions)
        missing = tuple(
            signature
            for signature in component.exclusions
            if not any(
                _same_signature(overload.params, signature)
                for overload in overloads
            )
        )
        if missing:
            raise ModuleLoadError(
                f"module {module_name!r} has no overload "
                f"{component.name.text}{_show_signature(missing[0])}"
            )
        selected = tuple(
            overload for overload in overloads if overload.params not in exclusions
        )
    typed = TypedFunctionNode(
        definition.typed.node,
        T.Overloads(*selected),
        definition.typed.overloads,
        definition.typed.dispatch_plan,
    )
    return ModuleDefinition(
        definition.name,
        typed,
        definition.public,
        definition.attached_tag,
    )


def _source_path(root: Path, parts: tuple[str, ...]) -> Path:
    """Source path during module loading and import resolution."""
    if not parts:
        raise ModuleLoadError("empty module path")
    return root.joinpath(*parts).with_suffix(".vlnc").resolve()


def _project_root(current_file: Path | None) -> Path | None:
    """Compute project root during module loading and import resolution."""
    start = Path.cwd() if current_file is None else current_file.resolve().parent
    return find_project_root(start)


def _module_name(path: ImportPath) -> str:
    """Return the canonical name for module during module loading and import resolution."""
    if path.root == Symbol("@"):
        return "@" + ".".join(path.parts)
    if path.root is not None:
        return f"{path.root.text}." + ".".join(path.parts)
    return ".".join(path.parts)


def _native_std_exports(path: ImportPath) -> ModuleExports | None:
    """Compute native std exports during module loading and import resolution."""
    if path.root is not None:
        return None
    if len(path.parts) == 2 and path.parts[0] == "std":
        module_name = path.parts[1]
    elif len(path.parts) == 1:
        module_name = path.parts[0]
    else:
        return None
    from valiance.elements.stdlib_native import native_module_exports

    exports = native_module_exports(module_name)
    if isinstance(exports, ModuleExports):
        return exports
    return None


def _definition_overloads(definition: ModuleDefinition) -> tuple[T.Overload, ...]:
    """Collect the overloads for definition during module loading and import resolution."""
    typ = definition.typed.typ
    if (
        isinstance(typ, T.FunctionType)
        and typ.params is not None
        and typ.returns is not None
    ):
        return (T.Overload(typ.params, typ.returns, element_tags=typ.element_tags),)
    if isinstance(typ, T.OverloadSetType):
        return typ.overloads
    return ()


def _same_signature(left: tuple[T.Type, ...], right: tuple[T.Type, ...]) -> bool:
    """Return the Boolean result of same signature during module loading and import resolution."""
    return len(left) == len(right) and all(
        T.same(a, b) for a, b in zip(left, right, strict=True)
    )


def _show_signature(signature: tuple[T.Type, ...]) -> str:
    """Format signature during module loading and import resolution."""
    return "(" + ", ".join(T.show(item) for item in signature) + ")"
