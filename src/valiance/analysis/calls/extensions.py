"""Focused extensions call analysis."""

from __future__ import annotations

from dataclasses import replace

import valiance.vtypes as T
from valiance.asts import (
    ElementExtension,
    FunctionNode,
    FunctionParam,
    TypedElementExtension,
    TypedExtensionPatternRule,
    TypedFunctionNode,
)

from . import candidates as _calls
from ..control_flow import patterns as _patterns
from ..state import AnalysisBranch


class _CallExtensions:
    """Own extensions operations for call planning."""

    def _analyse_element_extension(
        self,
        extension: ElementExtension | None,
        applied: T.AppliedOverload,
        outer: AnalysisBranch,
    ) -> TypedElementExtension | None:
        """Analyse element extension during static analysis."""
        if extension is None:
            return None

        if extension.default is not None:
            typed = self._analyse_extension_function(outer, extension.default)
            if typed is None:
                return None
            returns = _calls._single_function_return(typed)
            if returns is None:
                self._diagnose(
                    "extend default must produce exactly one value",
                    extension,
                )
                return None
            if not all(
                T.assignable(returns, param, self.env.context)
                for param in applied.params
            ):
                self._diagnose(
                    "extend default must produce a scalar value assignable to every element parameter",
                    extension,
                )
                return None
            return TypedElementExtension(default=typed)

        if extension.rules:
            typed_rules: list[TypedExtensionPatternRule] = []
            seen_patterns: set[tuple[bool, ...]] = set()
            for rule in extension.rules:
                if len(rule.pattern) != len(applied.params):
                    self._diagnose(
                        "extend pattern arity must match the target element arity",
                        extension,
                    )
                    return None
                presence = tuple(name is not None for name in rule.pattern)
                if presence in seen_patterns:
                    self._diagnose("duplicate extend pattern", extension)
                    return None
                seen_patterns.add(presence)

                typed_params = tuple(
                    FunctionParam(name=name, typ=param)
                    for name, param in zip(
                        rule.pattern,
                        applied.params,
                        strict=True,
                    )
                    if name is not None
                )
                function = replace(rule.function, params=typed_params)
                typed = self._analyse_extension_function(outer, function)
                if typed is None:
                    return None
                returns = _calls._consistent_function_returns(typed)
                missing = tuple(
                    param
                    for name, param in zip(
                        rule.pattern,
                        applied.params,
                        strict=True,
                    )
                    if name is None
                )
                if returns is None or len(returns) != len(missing):
                    self._diagnose(
                        "extend pattern rule must produce one substitution "
                        "for each missing argument",
                        extension,
                    )
                    return None
                if not all(
                    T.assignable(actual, expected, self.env.context)
                    for actual, expected in zip(returns, missing, strict=True)
                ):
                    self._diagnose(
                        "extend pattern substitutions must be assignable to the missing "
                        "parameter types",
                        extension,
                    )
                    return None
                typed_rules.append(TypedExtensionPatternRule(rule.pattern, typed))
            return TypedElementExtension(rules=tuple(typed_rules))

        if extension.selector is not None:
            optional_params = tuple(T.optional(param) for param in applied.params)
            selector = replace(
                extension.selector,
                params=tuple(FunctionParam(typ=param) for param in optional_params),
            )
            typed = self._analyse_extension_function(outer, selector)
            if typed is None:
                return None
            selector_arity = _patterns._extension_selector_arity(typed)
            if selector_arity != len(applied.params):
                self._diagnose(
                    "extend selector arity must match the target element arity",
                    extension,
                )
                return None
            returned = _calls._single_function_return(typed)
            if returned is None:
                self._diagnose(
                    "extend selector must produce exactly one value",
                    extension,
                )
                return None
            if not all(
                T.assignable(returned, T.optional(param), self.env.context)
                for param in applied.params
            ):
                self._diagnose(
                    "extend selector result must be assignable to every optional "
                    "element parameter",
                    extension,
                )
                return None
            return TypedElementExtension(selector=typed)

        self._diagnose("invalid extend clause", extension)
        return None

    def _analyse_extension_function(
        self,
        outer: AnalysisBranch,
        function: FunctionNode,
    ) -> TypedFunctionNode | None:
        """Analyse extension function during static analysis."""
        result = self._analyse_function_literal(outer, function)
        if result is None:
            return None
        analysis, _ = result
        return TypedFunctionNode(function, analysis.typ, analysis.overloads)

