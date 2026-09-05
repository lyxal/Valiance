"""Module loading and import/export helpers for Valiance source files."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import hashlib
from pathlib import Path

import valiance.vtypes as T
from valiance.asts import (
    DefineNode,
    FunctionNode,
    FunctionOverloadTyping,
    ImportPath,
    ImportSpec,
    ObjectNode,
    Symbol,
    TagDeclarationNode,
    TagOverlayNode,
    TypedFunctionNode,
)
from valiance.asts.nodes import TypedNode
from valiance.vtypes.nodes import VarType, type_var_key
from valiance.vtypes.relations import _substitute
from valiance.asts.object_constructors import constructor_definitions
from valiance.modules_system.packages import (
    PackageError,
    dependency_install_root,
    find_project_root,
    load_manifest,
)
from valiance.parsing import parse
from valiance.parsing.unicode_identifiers import (
    forbidden_identifier_character,
    is_xid_continue,
    is_xid_start,
    normalize_identifier,
)


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
    private_friendly_definitions: tuple[DefineNode, ...] = ()


@dataclass(frozen=True)
class ModuleTraitImplementation:
    """One object-to-trait implementation pattern defined by a module."""

    object_name: Symbol
    trait_name: Symbol
    definitions: tuple[DefineNode, ...] = ()
    owned: bool = False
    object_pattern: T.Type | None = None
    trait_pattern: T.Type | None = None
    generics: tuple[Symbol, ...] = ()
    generic_constraints: tuple[T.Type | None, ...] = ()
    subject_kind: Symbol = Symbol("object")


@dataclass(frozen=True)
class ModuleExports:
    """The reusable symbol surface of an analysed module."""

    module_name: str
    definitions: tuple[ModuleDefinition, ...] = ()
    objects: tuple[ModuleObject, ...] = ()
    tags: tuple[T.DataTagDefinition, ...] = ()
    overlays: tuple[T.TagOverlayDefinition, ...] = ()
    runtime_prelude: tuple[TypedNode, ...] = ()
    trait_implementations: tuple[ModuleTraitImplementation, ...] = ()

    def public_definitions(self) -> tuple[ModuleDefinition, ...]:
        """Return the definitions exported publicly by this module."""
        return tuple(definition for definition in self.definitions if definition.public)

    def public_objects(self) -> tuple[ModuleObject, ...]:
        """Return the object declarations exported publicly by this module."""
        return tuple(obj for obj in self.objects if obj.public)


class ModuleLoadError(Exception):
    """Raised when an import cannot be resolved or analysed."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: tuple[str, ...] = (),
    ) -> None:
        """Initialize a module error, optionally retaining child diagnostics."""
        super().__init__(message)
        self.diagnostics = diagnostics


# Immutable analysed stdlib exports are safe to share between loader instances.
_PROCESS_INTERFACE_CACHE: dict[tuple[str, str, str], ModuleExports] = {}


def collect_module_exports(
    module_name: str,
    program: list,
    typed: list[TypedNode],
    analyser: object,
) -> ModuleExports:
    """Collect the complete analysed static and runtime-facing module interface."""
    return ModuleExports(
        module_name,
        _deduplicate(_module_definitions(program, typed) + analyser.public_import_definitions),
        _deduplicate(_module_objects(program, typed) + analyser.public_import_objects),
        _deduplicate(_module_tags(program, analyser.env) + analyser.public_import_tags),
        _deduplicate(_module_overlays(program, analyser.env) + analyser.public_import_overlays),
        analyser.runtime_prelude,
        _deduplicate(_module_trait_implementations(program, typed) + analyser.public_import_trait_implementations),
    )


def _deduplicate(items: tuple) -> tuple:
    """Preserve interface order while removing repeated identical exports."""
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return tuple(result)


@dataclass
class ModuleLoader:
    """Resolve and analyse source modules, caching by absolute file path."""

    std_root: Path | None = None
    _cache: dict[Path, ModuleExports] = field(default_factory=dict)
    _interface_hashes: dict[Path, str] = field(default_factory=dict)
    _implementation_hashes: dict[Path, str] = field(default_factory=dict)
    _dependency_hashes: dict[Path, dict[str, str]] = field(default_factory=dict)
    _dependency_implementation_hashes: dict[Path, dict[str, str]] = field(default_factory=dict)
    _loading: set[Path] = field(default_factory=set)
    _loading_stack: list[Path] = field(default_factory=list)
    _provisional: dict[Path, ModuleExports] = field(default_factory=dict)
    source_overrides: dict[Path, str] = field(default_factory=dict)

    def load(
        self,
        path: ImportPath,
        *,
        current_file: Path | None = None,
    ) -> ModuleExports:
        """Load a module, preferring a valid persisted analysed interface."""
        source_file = self.resolve(path, current_file=current_file)
        compiled_file = source_file.with_suffix(".vbcm")
        native_exports = _native_std_exports(path)
        cache_key = source_file if source_file.exists() else compiled_file
        if cache_key in self._cache:
            exports = self._cache[cache_key]
            self._record_dependency(current_file, path, cache_key)
            return exports
        if cache_key in self._loading:
            exports = self._provisional_exports(path, source_file, cache_key)
            self._record_dependency(current_file, path, cache_key)
            return exports
        if native_exports is not None and not source_file.exists() and not compiled_file.exists():
            return native_exports

        self._loading.add(cache_key)
        self._loading_stack.append(cache_key)
        try:
            compiled_module = None
            if compiled_file.exists():
                from valiance.runtime.compiled_module import load_module_file

                try:
                    candidate = load_module_file(compiled_file)
                except Exception as exc:
                    if not source_file.exists():
                        raise ModuleLoadError(f"could not load module {compiled_file}: {exc}") from exc
                else:
                    source_matches = not source_file.exists() or hashlib.sha256(
                        source_file.read_text(encoding="utf-8").encode("utf-8")
                    ).hexdigest() == candidate.source_hash
                    dependencies_match = source_matches and self._compiled_dependencies_match(
                        candidate.dependency_hashes, source_file
                    )
                    if dependencies_match and candidate.analysed_interface is not None:
                        expected_name = _module_name(path)
                        if candidate.module_name != expected_name:
                            raise ModuleLoadError(
                                f"compiled module {compiled_file} declares "
                                f"{candidate.module_name!r}, expected {expected_name!r}"
                            )
                        shared_key = (
                            candidate.module_name,
                            candidate.interface_hash,
                            candidate.implementation_hash,
                        )
                        exports = _PROCESS_INTERFACE_CACHE.get(shared_key)
                        if exports is None:
                            exports = candidate.analysed_interface
                            if not isinstance(exports, ModuleExports):
                                raise ModuleLoadError(
                                    f"compiled module {compiled_file} has an invalid analysed interface"
                                )
                            _PROCESS_INTERFACE_CACHE[shared_key] = exports
                        self._cache[cache_key] = exports
                        self._interface_hashes[cache_key] = candidate.interface_hash
                        self._implementation_hashes[cache_key] = candidate.implementation_hash
                        self._record_dependency(current_file, path, cache_key)
                        return exports
                    if source_matches:
                        compiled_module = candidate

            if source_file.resolve() in self.source_overrides:
                source = self.source_overrides[source_file.resolve()]
            elif source_file.exists():
                source = source_file.read_text(encoding="utf-8")
            elif compiled_module is not None:
                source = compiled_module.interface_source
                source_file = compiled_file
            elif compiled_file.exists():
                from valiance.runtime.compiled_module import load_module_file
                compiled_module = load_module_file(compiled_file)
                source = compiled_module.interface_source
                source_file = compiled_file
            else:
                raise ModuleLoadError(
                    _missing_module_message(path, source_file)
                )

            program = parse(source)
            if path.parts and path.parts[0] == "std":
                from valiance.elements.stdlib_native import attach_native_object_elements
                program = attach_native_object_elements(program, path.parts[-1])
            from valiance.analysis import Analyser
            from valiance.elements.builtins import default_environment
            from valiance.elements.stdlib_native import install_native_stdlib

            env = None
            if path.parts and path.parts[0] == "std":
                env = install_native_stdlib(default_environment().child_scope(), path.parts[-1])
            analyser = Analyser(env=env, module_loader=self, source_file=source_file)
            typed = analyser.analyse(program)
            if analyser.diagnostics:
                diagnostics = tuple(
                    _module_diagnostic(source_file, diagnostic)
                    for diagnostic in analyser.diagnostics
                )
                raise ModuleLoadError(
                    f"module {_module_name(path)!r} contains type errors",
                    diagnostics=diagnostics,
                )
            exports = collect_module_exports(_module_name(path), program, typed, analyser)
            if native_exports is not None:
                exports = ModuleExports(
                    exports.module_name,
                    native_exports.definitions + exports.definitions,
                    exports.objects,
                    exports.tags,
                    exports.overlays,
                    exports.runtime_prelude,
                    exports.trait_implementations,
                )
            self._cache[cache_key] = exports
            from valiance.runtime import compile_program
            from valiance.runtime.compiled_module import interface_hash, implementation_hash

            self._interface_hashes[cache_key] = interface_hash(exports)
            self._implementation_hashes[cache_key] = implementation_hash(
                compile_program(typed), options="optimize=true"
            )
            self._record_dependency(current_file, path, cache_key)
            return exports
        except OSError as exc:
            if native_exports is not None:
                return native_exports
            if isinstance(exc, FileNotFoundError):
                raise ModuleLoadError(
                    _missing_module_message(path, source_file)
                ) from exc
            raise ModuleLoadError(f"could not read module {source_file}: {exc}") from exc
        finally:
            self._loading.discard(cache_key)
            if self._loading_stack and self._loading_stack[-1] == cache_key:
                self._loading_stack.pop()
            elif cache_key in self._loading_stack:
                self._loading_stack.remove(cache_key)
            self._provisional.pop(cache_key, None)

    def _provisional_exports(
        self, path: ImportPath, source_file: Path, cache_key: Path
    ) -> ModuleExports:
        """Publish complete contracts during a recursive module load.

        This replaces the former empty-interface shortcut. Only declarations
        whose parameter and return contracts are complete can cross a module
        cycle, matching Valiance's declaration-first recursion rule.
        """
        existing = self._provisional.get(cache_key)
        if existing is not None:
            return existing
        if not source_file.exists():
            compiled = source_file.with_suffix(".vbcm")
            if compiled.exists():
                from valiance.runtime.compiled_module import load_module_file

                candidate = load_module_file(compiled)
                if isinstance(candidate.analysed_interface, ModuleExports):
                    self._interface_hashes[cache_key] = candidate.interface_hash
                    self._implementation_hashes[cache_key] = candidate.implementation_hash
                    self._provisional[cache_key] = candidate.analysed_interface
                    return candidate.analysed_interface
            raise ModuleLoadError(
                f"source-free cyclic module {source_file} has no valid interface"
            )
        source = self.source_overrides.get(
            source_file.resolve(), source_file.read_text(encoding="utf-8")
        )
        program = parse(source)
        definitions: list[ModuleDefinition] = []
        incomplete: list[DefineNode] = []
        from valiance.analysis.calls.callable_values import _fully_typed_overload

        for node in program:
            if not isinstance(node, DefineNode) or node.visibility != Symbol("public"):
                continue
            overload = _fully_typed_overload(node.function)
            if overload is None:
                incomplete.append(node)
                continue
            function_type = T.Fn(
                overload.params, overload.returns, overload.element_tags
            )
            typed = TypedFunctionNode(
                node,
                function_type,
                (FunctionOverloadTyping(function_type, (), overload),),
            )
            definitions.append(
                ModuleDefinition(
                    node.name,
                    typed,
                    public=True,
                    attached_tag=(
                        Symbol(node.attached_tag.name)
                        if node.attached_tag is not None
                        else None
                    ),
                )
            )
        if incomplete:
            cycle = " -> ".join(
                item.with_suffix("").name for item in (*self._loading_stack, cache_key)
            )
            declarations = ", ".join(
                f"{node.name} at line {node.location.line if node.location else '?'}"
                for node in incomplete
            )
            raise ModuleLoadError(
                "cross-module recursive declarations require complete parameter "
                f"and return signatures; cycle {cycle}; incomplete: {declarations}"
            )
        exports = ModuleExports(_module_name(path), definitions=tuple(definitions))
        from valiance.runtime.compiled_module import interface_hash

        self._interface_hashes[cache_key] = interface_hash(exports)
        self._provisional[cache_key] = exports
        return exports

    def invalidate(self, source_files: object) -> frozenset[Path]:
        """Invalidate selected source interfaces without clearing unrelated cache entries."""
        paths={Path(item).resolve() for item in source_files}
        for path in paths:
            for key in (path,path.with_suffix(".vbcm")):
                self._cache.pop(key,None); self._interface_hashes.pop(key,None); self._implementation_hashes.pop(key,None); self._provisional.pop(key,None)
            self._dependency_hashes.pop(path,None); self._dependency_implementation_hashes.pop(path,None)
        return frozenset(paths)

    def dependency_hashes_for(self, source_file: Path) -> tuple[tuple[str, str], ...]:
        """Return canonical direct dependency identities and semantic hashes."""
        dependencies = self._dependency_hashes.get(source_file.resolve(), {})
        return tuple(sorted(dependencies.items()))

    def interface_hash_for(self, exports: ModuleExports) -> str:
        """Return the semantic hash associated with a successfully loaded interface."""
        for cache_key, cached in self._cache.items():
            if cached is exports:
                return self._interface_hashes[cache_key]
        raise ModuleLoadError("module interface was not loaded by this loader")

    def implementation_hash_for(self, exports: ModuleExports) -> str:
        """Return the runtime implementation hash associated with loaded exports."""
        for cache_key, cached in self._cache.items():
            if cached is exports:
                return self._implementation_hashes[cache_key]
        raise ModuleLoadError("module implementation was not loaded by this loader")

    def dependency_implementation_hashes_for(
        self, source_file: Path
    ) -> tuple[tuple[str, str], ...]:
        """Return canonical direct dependency identities and implementation hashes."""
        dependencies = self._dependency_implementation_hashes.get(
            source_file.resolve(), {}
        )
        return tuple(sorted(dependencies.items()))

    def _record_dependency(
        self, current_file: Path | None, path: ImportPath, cache_key: Path
    ) -> None:
        """Record one direct semantic dependency of the requesting source file."""
        if current_file is None or cache_key not in self._interface_hashes:
            return
        requester = current_file.resolve()
        identity = canonical_dependency_identity(path)
        self._dependency_hashes.setdefault(requester, {})[identity] = self._interface_hashes[
            cache_key
        ]
        if cache_key in self._implementation_hashes:
            self._dependency_implementation_hashes.setdefault(requester, {})[
                identity
            ] = self._implementation_hashes[cache_key]

    def _compiled_dependencies_match(
        self, dependencies: tuple[tuple[str, str], ...], source_file: Path
    ) -> bool:
        """Validate recorded direct dependency interfaces before artifact reuse."""
        for identity, expected_hash in dependencies:
            try:
                path = dependency_path_from_identity(identity)
                exports = self.load(path, current_file=source_file)
                actual_hash = self.interface_hash_for(exports)
            except ModuleLoadError:
                return False
            if actual_hash != expected_hash:
                return False
        return True

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
        # Unqualified paths are strictly relative to the importing file. For
        # The analyser first interprets a dotted final segment as a component
        # when the parent module exports it. Otherwise this complete path is
        # resolved as a nested source module such as parsers/json.vlnc.
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


def canonical_dependency_identity(path: ImportPath) -> str:
    """Return a portable, resolution-aware identity for an imported module path."""
    root = path.root.text if path.root is not None else "local"
    return f"{root}:{'.'.join(path.parts)}"


def dependency_path_from_identity(identity: str) -> ImportPath:
    """Decode a canonical dependency identity stored in a module artifact."""
    root, separator, module = identity.partition(":")
    if not separator or not module or root not in {"local", "root", "dep"}:
        if root != "std":
            raise ModuleLoadError(f"invalid compiled dependency identity {identity!r}")
    parts = tuple(part for part in module.split(".") if part)
    if not parts:
        raise ModuleLoadError(f"invalid compiled dependency identity {identity!r}")
    if root == "local":
        return ImportPath(parts)
    if root == "std":
        return ImportPath(parts)
    return ImportPath(parts, Symbol(root))


def import_definitions(
    exports: ModuleExports,
    spec: ImportSpec,
) -> tuple[ModuleDefinition, ...]:
    """Return exported definitions selected by an import spec."""
    public = exports.public_definitions()
    public_objects = {obj.name for obj in exports.public_objects()}
    if spec.components:
        by_name: dict[Symbol, list[ModuleDefinition]] = {}
        for definition in public:
            by_name.setdefault(definition.name, []).append(definition)
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
            definitions = by_name.get(component.name, [])
            if not definitions:
                if component.name in public_objects:
                    continue
                raise ModuleLoadError(
                    f"module {exports.module_name!r} has no public "
                    f"component {component.name.text!r}"
                )
            for definition in definitions:
                definition = _select_overloads(
                    definition, component, exports.module_name
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
    program: list,
    typed: list[TypedNode],
) -> tuple[ModuleObject, ...]:
    """Compute module objects during module loading and import resolution."""
    local_traits = {
        node.name for node in program
        if isinstance(node, ObjectNode)
        and node.target is None
        and node.kind == Symbol("trait")
    }
    friendly_by_owner: dict[Symbol, list[DefineNode]] = {}
    for typed_node in typed:
        node = typed_node.node
        if not isinstance(node, ObjectNode):
            continue
        if node.target is not None and (
            node.kind == Symbol("object")
            or (node.kind == Symbol("trait") and node.name in local_traits)
        ):
            target = T.normalize(node.target)
            if isinstance(target, T.NominalType):
                friendly_by_owner.setdefault(node.name, []).extend(node.definitions)

    result: list[ModuleObject] = []
    for typed_node in typed:
        node = typed_node.node
        if not isinstance(node, ObjectNode):
            continue
        if node.target is not None and (
            node.kind == Symbol("object")
            or (node.kind == Symbol("trait") and node.name in local_traits)
        ):
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


def _implementation_pattern_type(
    typ: T.Type,
    generics: tuple[Symbol, ...],
) -> T.Type:
    """Convert source generic names in an implementation type to variables."""
    generic_names = {generic.text for generic in generics}
    typ = T.normalize(typ)
    if (
        isinstance(typ, T.NominalType)
        and not typ.args
        and not typ.name.namespace
        and typ.name.text in generic_names
    ):
        return T.V(typ.name.text)
    if isinstance(typ, T.NominalType):
        return T.N(
            typ.name,
            *(_implementation_pattern_type(arg, generics) for arg in typ.args),
        )
    return typ


def _implementation_object_pattern(node: ObjectNode) -> T.Type:
    """Return the source-level object pattern matched by an implementation."""
    args = tuple(T.V(generic.text) for generic in node.generics)
    return T.N(node.name, *args)


def _module_trait_implementations(
    program: list,
    typed: list[TypedNode],
) -> tuple[ModuleTraitImplementation, ...]:
    """Collect object-to-trait implementations defined by this module."""
    local_objects = {
        node.name for node in program
        if isinstance(node, ObjectNode)
        and node.target is None
        and node.kind == Symbol("object")
    }
    local_traits = {
        node.name for node in program
        if isinstance(node, ObjectNode)
        and node.target is None
        and node.kind == Symbol("trait")
    }
    result = []
    for typed_node in typed:
        node = typed_node.node
        if not isinstance(node, ObjectNode) or node.target is None:
            continue
        is_trait_impl = node.kind == Symbol("trait")
        if node.kind != Symbol("object") and not is_trait_impl:
            continue
        target = T.normalize(node.target)
        if isinstance(target, T.NominalType):
            result.append(
                ModuleTraitImplementation(
                    node.name,
                    target.name,
                    node.definitions,
                    owned=(
                        target.name in local_traits
                        and (node.name in local_objects or node.name in local_traits)
                    ),
                    object_pattern=_implementation_object_pattern(node),
                    trait_pattern=_implementation_pattern_type(
                        target,
                        node.generics,
                    ),
                    generics=node.generics,
                    generic_constraints=tuple(
                        _implementation_pattern_type(constraint, node.generics)
                        if constraint is not None else None
                        for constraint in node.generic_constraints
                    ),
                    subject_kind=Symbol("trait") if is_trait_impl else Symbol("object"),
                )
            )
    return tuple(result)


def import_behaviour_set_objects(
    exports: ModuleExports,
    spec: ImportSpec,
) -> tuple[ModuleObject, ...]:
    """Build provider-qualified friendly surfaces for behaviour set imports."""
    objects_by_name = {obj.name: obj for obj in exports.objects}
    provider = Symbol(exports.module_name.rsplit(".", 1)[-1])
    selected = []
    for component in spec.components:
        if component.kind != Symbol("trait_impl"):
            continue
        implementation = next(
            (
                item for item in exports.trait_implementations
                if item.object_name == component.name
                and item.trait_name == component.trait
                and item.subject_kind == (component.subject_kind or Symbol("object"))
            ),
            None,
        )
        obj = objects_by_name.get(component.name)
        if implementation is None or obj is None:
            continue
        surface = Symbol(component.trait.text, (provider.text,))
        implementation_object = replace(
            obj,
            friendly_definitions=tuple(
                replace(definition, visibility=Symbol("public"))
                for definition in implementation.definitions
            ),
        )
        selected.append(
            _renamed_object(
                implementation_object,
                surface,
                friendly_prefix=surface,
                import_friendly=True,
            )
        )
        selected.append(
            _renamed_object(
                implementation_object,
                surface,
                import_friendly=True,
            )
        )
    return tuple(selected)


def import_owned_trait_implementations(
    exports: ModuleExports,
    spec: ImportSpec,
) -> tuple[ModuleTraitImplementation, ...]:
    """Select owned implementations attached to direct object imports."""
    selected = []
    for component in spec.components:
        if component.kind is not None:
            continue
        selected.extend(
            item for item in exports.trait_implementations
            if item.owned and item.object_name == component.name
        )
    return tuple(selected)


def import_trait_implementations(
    exports: ModuleExports,
    spec: ImportSpec,
) -> tuple[ModuleTraitImplementation, ...]:
    """Select explicit object-to-trait implementation imports."""
    selected = []
    for component in spec.components:
        if component.kind != Symbol("trait_impl"):
            continue
        match = next(
            (
                impl for impl in exports.trait_implementations
                if impl.object_name == component.name
                and impl.trait_name == component.trait
                and impl.subject_kind == (component.subject_kind or Symbol("object"))
            ),
            None,
        )
        if match is None:
            raise ModuleLoadError(
                f"module {exports.module_name!r} defines no implementation "
                f"of trait {component.trait} for object {component.name}"
            )
        selected.append(match)
    return tuple(selected)


def _module_tags(program: list, env) -> tuple[T.DataTagDefinition, ...]:
    """Compute importable module tags; tags do not require public visibility."""
    importable = {
        Symbol(node.tag.name)
        for node in program
        if isinstance(node, TagDeclarationNode) and node.disjoint is None
    }
    return tuple(
        definition
        for name in importable
        if (definition := env.lookup_tag(name)) is not None
    )


def _module_overlays(program: list, env) -> tuple[T.TagOverlayDefinition, ...]:
    """Compute all module overlay rules available through an imported tag."""
    overlay_tags = {
        Symbol(node.tag.name)
        for node in program
        if isinstance(node, TagOverlayNode)
    }
    overlays: list[T.TagOverlayDefinition] = []
    for definitions in env.tag_overlays.values():
        overlays.extend(
            definition for definition in definitions if definition.tag in overlay_tags
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
        and definition.visibility == Symbol("public")
    )
    private_friendly_definitions = tuple(
        definition
        for definition in obj.friendly_definitions
        if definition not in explicit_constructors
        and definition.visibility != Symbol("public")
    )
    definitions = renamed_constructors
    renamed_private_friendly_definitions: tuple[DefineNode, ...] = ()
    if import_friendly:
        definitions += _renamed_friendly_definitions(
            friendly_definitions,
            friendly_prefix,
        )
        renamed_private_friendly_definitions = _renamed_friendly_definitions(
            private_friendly_definitions,
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
        renamed_private_friendly_definitions,
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
            if _same_import_signature(overload, component)
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


def _is_importable_module_stem(value: str) -> bool:
    """Return whether a file stem can be named by ordinary import syntax."""
    normalized = normalize_identifier(value)
    return (
        value == normalized
        and bool(value)
        and is_xid_start(value[0])
        and not forbidden_identifier_character(value[0])
        and all(
            is_xid_continue(char) and not forbidden_identifier_character(char)
            for char in value[1:]
        )
    )


def _module_name_similarity(value: str) -> str:
    """Return a punctuation-insensitive key used only for missing-module hints."""
    return "".join(char for char in value.casefold() if char.isalnum())


def _module_diagnostic(source_file: Path, diagnostic: str) -> str:
    """Attach a module source path without flattening its diagnostic text."""
    return f"@source[{source_file}]{diagnostic}"


def _nearby_module_names(source_file: Path) -> tuple[str, ...]:
    """Return importable sibling modules similar to a missing source module."""
    directory = source_file.parent
    if not directory.is_dir():
        return ()

    requested = source_file.stem
    requested_key = _module_name_similarity(requested)
    candidates: dict[str, float] = {}
    try:
        for entry in directory.iterdir():
            if not entry.is_file() or entry.suffix not in {".vlnc", ".vbcm"}:
                continue
            stem = entry.stem
            if stem == requested or not _is_importable_module_stem(stem):
                continue
            score = SequenceMatcher(
                None, requested_key, _module_name_similarity(stem)
            ).ratio()
            if score >= 0.62:
                candidates[stem] = max(score, candidates.get(stem, 0.0))
    except OSError:
        return ()

    return tuple(
        name
        for name, _ in sorted(
            candidates.items(), key=lambda item: (-item[1], item[0].casefold())
        )[:3]
    )


def _missing_module_message(path: ImportPath, source_file: Path) -> str:
    """Build an actionable diagnostic for a module that cannot be found."""
    module_name = _module_name(path)
    lines = [
        f"module {module_name!r} was not found",
        f"looked for: {source_file}",
    ]
    directory = source_file.with_suffix("")
    if directory.is_dir():
        lines.append(f"found directory: {directory}")
        lines.append(
            "help: directories cannot be imported directly; import a .vlnc file "
            "from the directory instead"
        )
    else:
        nearby = _nearby_module_names(source_file)
        if nearby:
            rendered = ", ".join(repr(name) for name in nearby)
            lines.append(f"help: did you mean {rendered}?")
        else:
            lines.append(
                "help: check the module name and make sure the source file exists "
                "next to the importing file"
            )
    unimportable = _unimportable_file_suggestion(source_file)
    if unimportable is not None:
        lines.append(unimportable)
    return "\n".join(lines)


def _unimportable_file_suggestion(source_file: Path) -> str | None:
    """Suggest renaming a nearby source file that ordinary imports cannot name."""
    directory = source_file.parent
    if not directory.is_dir():
        return None

    requested = source_file.stem
    requested_key = _module_name_similarity(requested)
    if not requested_key:
        return None

    candidates: list[tuple[float, str]] = []
    try:
        entries = directory.iterdir()
        for entry in entries:
            if not entry.is_file() or entry.suffix != ".vlnc":
                continue
            stem = entry.stem
            if _is_importable_module_stem(stem):
                continue
            candidate_key = _module_name_similarity(stem)
            if not candidate_key:
                continue
            score = SequenceMatcher(None, requested_key, candidate_key).ratio()
            if requested_key == candidate_key or score >= 0.8:
                candidates.append((score, entry.name))
    except OSError:
        return None

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1].casefold()))
    names = [name for _, name in candidates[:3]]
    lines = [
        "A similarly named source file cannot be imported because its filename "
        "is not a valid Valiance identifier:",
        *(f"  {name}" for name in names),
        f"help: rename {names[0]!r} to {source_file.name!r}",
    ]
    return "\n".join(lines)


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
    """Collect complete overload metadata for import resolution."""
    analysed = tuple(item.overload for item in definition.typed.overloads)
    if analysed:
        return analysed
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



def _same_import_signature(overload: T.Overload, component) -> bool:
    """Match an import signature, alpha-renaming declared generic parameters."""
    signature = component.signature
    if signature is None or len(overload.params) != len(signature):
        return False
    if not component.generics:
        return _same_signature(overload.params, signature)
    if len(overload.generic_params) != len(component.generics):
        return False

    import_variables = {
        generic.text: T.TypeVariable(generic.text) for generic in component.generics
    }
    substitution = {}
    for overload_name, import_generic in zip(
        overload.generic_params, component.generics, strict=True
    ):
        variable = _find_type_variable(overload.params, overload_name)
        if variable is None:
            return False
        substitution[type_var_key(variable)] = import_variables[import_generic.text]
    renamed = tuple(_substitute(param, substitution) for param in overload.params)
    return _same_signature(renamed, signature)


def _find_type_variable(
    types: tuple[T.Type, ...], name: str
) -> VarType | None:
    """Find the bound variable for one declared generic name in a signature."""
    stack = list(types)
    while stack:
        current = stack.pop()
        if isinstance(current, VarType) and current.name == name:
            return current
        if hasattr(current, "__dataclass_fields__"):
            for field_name in current.__dataclass_fields__:
                value = getattr(current, field_name)
                if isinstance(value, T.Type):
                    stack.append(value)
                elif isinstance(value, (tuple, frozenset)):
                    stack.extend(item for item in value if isinstance(item, T.Type))
                    stack.extend(
                        item.typ for item in value if hasattr(item, "typ")
                    )
    return None

def _same_signature(left: tuple[T.Type, ...], right: tuple[T.Type, ...]) -> bool:
    """Return the Boolean result of same signature during module loading and import resolution."""
    return len(left) == len(right) and all(
        T.same(a, b) for a, b in zip(left, right, strict=True)
    )


def _show_signature(signature: tuple[T.Type, ...]) -> str:
    """Format signature during module loading and import resolution."""
    return "(" + ", ".join(T.show(item) for item in signature) + ")"
