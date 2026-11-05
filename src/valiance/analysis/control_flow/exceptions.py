"""Focused exceptions control-flow analysis."""

from __future__ import annotations


import valiance.vtypes as T
from valiance.asts import ASTNode, TryNode, TypedNode, TypedTryNode
from valiance.vtypes.symbols import Symbol

from ..calls import candidates as _calls
from . import patterns as _patterns
from ..state import AnalysisBranch, BranchSet


class _ExceptionAnalysis:
    """Own exceptions control-flow operations."""

    def _try(
        self,
        node: TryNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `TryNode` node and return the surviving branches."""
        if not node.handlers:
            self._diagnose("try requires at least one handler", node)
            return BranchSet()

        body_outputs = self.analyse_scoped_block(BranchSet((branch,)), node.body)
        typed_body = _patterns._typed_block(
            body_outputs,
            len(branch.typed_body),
            node.body,
        )
        outputs: list[AnalysisBranch] = list(body_outputs.branches)
        typed_handler_bodies: list[tuple[ASTNode | TypedNode, ...]] = []
        for handler in node.handlers:
            if handler.typ is not None:
                normalized_handler = T.normalize(handler.typ)
                if (
                    isinstance(normalized_handler, T.NominalType)
                    and self.env.lookup_trait(normalized_handler.name) is not None
                ):
                    self._diagnose(
                        f"try handler type {T.show(handler.typ)} is not a concrete "
                        "runtime fault type",
                        handler,
                    )
                elif not T.assignable(
                    handler.typ,
                    T.N(Symbol("Fault")),
                    self.env.context,
                ):
                    self._diagnose(
                        f"try handler type {T.show(handler.typ)} does not "
                        "implement Fault",
                        handler,
                    )
            handler_outputs = self.analyse_scoped_block(
                BranchSet((branch,)),
                handler.body,
            )
            typed_handler_bodies.append(
                _patterns._typed_block(
                    handler_outputs,
                    len(branch.typed_body),
                    handler.body,
                )
            )
            for output in handler_outputs:
                if output.inputs != branch.inputs:
                    self._diagnose("try handlers inferred different inputs", handler)
                    continue
                outputs.append(_patterns._try_handler_output(output, branch, handler))

        if not outputs:
            return BranchSet()

        joined: AnalysisBranch | None = None
        for output in outputs:
            joined = _patterns._join_try_output(
                branch,
                joined,
                output,
                self.env.context,
            )
            if joined is None:
                self._diagnose("try branches inferred different inputs", node)
                return BranchSet()
        if joined is None:
            return BranchSet()
        return BranchSet(
            (
                joined.emit(
                    TypedTryNode(
                        node,
                        _calls._returns_result_type(joined.stack.items),
                        body=typed_body,
                        handler_bodies=tuple(typed_handler_bodies),
                    )
                ),
            )
        )

