"""Focused imports declaration analysis."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import (
    dataclass,
    field,
    fields,
    is_dataclass,
    replace,
)
from enum import Enum, auto
from hashlib import sha1
from itertools import count
from pathlib import Path
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
import valiance.analysis.contracts.where_clauses as static_where
from valiance.elements.builtins import default_environment
from valiance.analysis.lints import (
    DEFAULT_REGISTRY as DEFAULT_LINT_REGISTRY,
    BlockLintContext,
    LintFinding,
    LintRegistry,
    MatchLintContext,
    NodeLintContext,
)
from valiance.asts import (
    AnnotationNode,
    ASTNode,
    BindingPatternNode,
    DefineNode,
    ElementExtension,
    ElementNode,
    EnumMemberNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    ImportComponent,
    ImportPath,
    ImportSpec,
    ListPatternNode,
    MatchCaseNode,
    MatchNode,
    MatchPatternNode,
    ObjectNode,
    OrPatternNode,
    PopNNode,
    SourceLocation,
    TraitRequirementNode,
    TryHandlerNode,
    TryNode,
    TypePatternNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedMatchNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedTryNode,
    VariantMemberNode,
    is_catch_all_match_case,
)
from valiance.asts.nodes import GetVariableNode, ObjectFieldNode
from valiance.modules_system.modules import ModuleLoader, ModuleLoadError, import_definitions
from valiance.asts.object_constructors import (
    constructor_definitions,
    definitely_initialized_fields,
    prepare_constructor_body,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean

from ..calls import candidates as _calls
from ..calls import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""


class _ImportDeclarations:
    """Own declaration operations for this domain."""

    def _load_import_definitions(
        self,
        spec: ImportSpec,
    ):
        """Load import definitions during static analysis."""
        try:
            exports = self.module_loader.load(
                spec.path,
                current_file=self.source_file,
            )
            return exports, spec, import_definitions(exports, spec)
        except ModuleLoadError:
            if spec.components or len(spec.path.parts) < 2:
                raise
            module_path = ImportPath(spec.path.parts[:-1], spec.path.root)
            component = ImportComponent(Symbol(spec.path.parts[-1]))
            split_spec = ImportSpec(module_path, spec.alias, (component,))
            exports = self.module_loader.load(
                split_spec.path,
                current_file=self.source_file,
            )
            return exports, split_spec, import_definitions(exports, split_spec)

    def _import_source_text(
        self,
        spec: ImportSpec,
        visible_name: Symbol,
    ) -> str:
        """Render the source component that introduced an imported name."""
        path = _show_import_path(spec.path)
        for component in spec.components:
            if (component.alias or component.name) == visible_name:
                return f"{path}.{component.name}"
        return f"{path}.{visible_name.text}"

    def _import_arity_diagnostic(
        self,
        message: str,
        spec: ImportSpec,
        name: Symbol,
    ) -> str:
        """Add concrete removal and namespacing fixes to an import conflict."""
        current = self._import_source_text(spec, name)
        previous = self._imported_definition_sources.get(name)
        current_module = _show_import_path(spec.path)
        current_namespace = spec.alias or Symbol(spec.path.parts[-1])
        if previous is None:
            return (
                f"{message}\n"
                f"help: rename the imported element with "
                f"`import {{ {current} as {name}Imported }}`\n"
                f"help: or keep it namespaced with "
                f"`import {{ {current_module} }}` and use "
                f"`{current_namespace}.{name.text}`"
            )
        previous_module = previous.rsplit(".", 1)[0]
        previous_namespace = previous_module.rsplit(".", 1)[-1]
        return (
            f"{message}\n"
            f"help: either remove one of these imports: "
            f"`{previous}` or `{current}`\n"
            f"help: or keep both namespaced with "
            f"`import {{ {previous_module}, {current_module} }}` and use "
            f"`{previous_namespace}.{name.text}` or "
            f"`{current_namespace}.{name.text}`"
        )

    def _register_imported_definition(
        self,
        name: Symbol,
        typed_node: TypedFunctionNode,
        runtime_name: Symbol,
    ) -> None:
        """Register imported definition during static analysis."""
        declared = tuple(
            typing.overload
            for typing in typed_node.overloads
            if isinstance(typing.overload, T.Overload)
        )
        for selected in _functions._callable_overloads(typed_node.typ):
            overload = next(
                (
                    candidate
                    for candidate in declared
                    if candidate.params == selected.params
                    and candidate.returns == selected.returns
                ),
                selected,
            )
            self.env.define_overload(name, overload)
            self.env.bind_runtime_name(name, runtime_name, overload)

    def _register_imported_friendly_runtime_elements(
        self,
        owner: Symbol,
        definitions: tuple[DefineNode, ...],
    ) -> None:
        """Make hidden native calls in imported friendly wrappers type-visible."""
        for definition in definitions:
            body = definition.function.body
            if not body or not isinstance(body[-1], ElementNode):
                continue
            runtime_name = body[-1].name
            if not runtime_name.namespace or self.env.overloads_for(runtime_name):
                continue
            params = tuple(
                param.typ or T.Any for param in definition.function.params or ()
            )
            self.env.define_overload(
                runtime_name,
                T.Overload((T.N(owner), *params), definition.function.returns or ()),
            )

    def _register_imported_object(
        self,
        obj,
        runtime_name: Symbol,
    ) -> None:
        """Register imported object during static analysis."""
        self.env.bind_runtime_name(obj.name, runtime_name)
        node = obj.typed.node
        if not isinstance(node, ObjectNode):
            return
        kind = node.kind.text
        if kind == "trait":
            self._define_trait_shape(obj.name, node)
            return

        if kind != "object" or node.target is not None:
            return

        object_attributes = self._object_attributes(node.fields, node.generics)
        if object_attributes is None:
            return
        defaults = frozenset(field.name for field in node.fields if field.default)
        constructors = constructor_definitions(obj.name, node.definitions)
        friendly_definitions = tuple(
            definition
            for definition in node.definitions
            if definition not in constructors
        )
        self._define_object_shape(
            obj.name,
            node,
            object_attributes,
            defaults=defaults,
            synthesize_constructor=not constructors,
        )
        current = AnalysisBranch()
        for constructor in constructors:
            current = self._register_constructor_definition(
                current,
                node,
                constructor,
                defaults,
            )
        if obj.import_friendly:
            self._register_imported_friendly_runtime_elements(
                obj.name,
                friendly_definitions,
            )
            self._register_friendly_definitions(
                current,
                obj.name,
                friendly_definitions,
            )



def _show_import_path(path: ImportPath) -> str:
    """Render an import path using source-level root syntax."""
    prefix = "" if path.root is None else f"{path.root}."
    return prefix + ".".join(path.parts)
