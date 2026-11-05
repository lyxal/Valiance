"""Focused loops control-flow analysis."""

from __future__ import annotations


from valiance.asts import FunctionNode

from ..calls import callable_values as _functions
from ..state import AnalysisBranch, BranchSet, BranchVariables, InputMode

from ..calls.models import FunctionAnalysis


class _LoopAnalysis:
    """Own loops control-flow operations."""

    def _analyse_unfold_body_function(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
    ) -> FunctionAnalysis | None:
        """Analyse unfold body function during static analysis."""
        if node.params is None:
            analysed = self._analyse_function_literal(outer, node)
            return None if analysed is None else analysed[0]

        params = _functions._declared_params(node)
        body_params = tuple(_functions._parameter_value_type(param) for param in params)
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params, body_params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_functions._function_capture_source(outer),
        )
        initial = AnalysisBranch(
            inputs=body_params,
            variables=variables,
            input_mode=(
                InputMode.CYCLE_EXPLICIT_PARAMS if body_params else InputMode.NILADIC
            ),
            cycle_params=body_params,
            atomic_type_vars=(
                outer.atomic_type_vars
                | _functions._atomic_parameter_type_vars(params)
            ),
            origin=outer.origin,
        )
        function_analyser = self._child_analyser(self.env.lexical_child_scope())
        final = function_analyser.analyse_block(BranchSet((initial,)), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _functions._function_analysis_from_signatures(signatures, self.env.context)
        if analysis is None:
            self.diagnostics.extend(function_analyser.diagnostics)
            self.warnings.extend(function_analyser.warnings)
            self._extend_lint_findings(function_analyser.lint_findings)
            return None
        self.warnings.extend(function_analyser.warnings)
        self._extend_lint_findings(function_analyser.lint_findings)
        return analysis

