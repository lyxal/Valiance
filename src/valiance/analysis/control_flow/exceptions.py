"""Focused exceptions control-flow analysis."""

from __future__ import annotations

from dataclasses import replace

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
        # Only the successful try paths can reach the next statement.
        # A handled fault returns from the lexically enclosing function.
        continuing = [output for output in body_outputs if not output.terminal]
        # A Never result from the protected body represents a raised fault,
        # not a function return.  Handlers consume those faults at runtime;
        # unmatched faults still propagate normally via the panic mechanism.
        terminal = [
            output for output in body_outputs
            if output.return_stack is not None or output.break_type is not None
        ]
        handler_returns: list[AnalysisBranch] = []
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
                # An explicit return/panic in the handler is already a
                # terminal path.  Only normally completed handlers acquire
                # an implicit function return (ordinary values, no wrapper).
                handler_returns.append(
                    output if output.terminal
                    else output.with_return(output.stack, exact=False)
                )

        def uncaught_effects(output: AnalysisBranch) -> AnalysisBranch:
            """Keep prior effects and any panic types this try cannot catch."""
            def caught(tag: T.ElementTag) -> bool:
                """Whether a reachable handler discharges one panic effect."""
                if tag.name != Symbol("Panic") or len(tag.args) != 1:
                    return False
                fault = T.normalize(tag.args[0])
                # Ownership-integrity faults bypass even catch-all handlers.
                if isinstance(fault, T.NominalType) and fault.name.text == "DuplicationFault":
                    return False
                return any(
                    handler.typ is None
                    or T.same(fault, T.normalize(handler.typ))
                    for handler in node.handlers
                )

            return replace(
                output,
                element_tags=frozenset(
                    tag for tag in output.element_tags
                    if tag in branch.element_tags or not caught(tag)
                ),
            )

        continuing = [uncaught_effects(output) for output in continuing]
        terminal = [uncaught_effects(output) for output in terminal]
        joined: AnalysisBranch | None = None
        for output in continuing:
            joined = _patterns._join_try_output(
                branch, joined, output, self.env.context,
            )
            if joined is None:
                self._diagnose("try branches inferred different inputs", node)
                return BranchSet()

        if joined is None and not terminal and not handler_returns:
            return BranchSet()

        typed = TypedTryNode(
            node,
            _calls._returns_result_type(joined.stack.items)
            if joined is not None else None,
            body=typed_body,
            handler_bodies=tuple(typed_handler_bodies),
        )
        # Each branch must retain the same checked TryNode so function code
        # generation does not drop a handler based on which exit it analysed.
        exits = (*terminal, *handler_returns)
        outcomes = [
            replace(joined, typed_body=branch.typed_body).emit(typed)
        ] if joined is not None else []
        outcomes.extend(
            replace(output, typed_body=branch.typed_body).emit(typed)
            for output in exits
        )
        return BranchSet.collect(outcomes)

