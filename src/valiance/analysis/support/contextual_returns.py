"""Specialize a return-only generic call using a concrete expected result type.

This is contextual *type-argument* inference, not inference of trait or
operator requirements. An unconstrained generic body is still checked once
under its declared constraints.
"""

from dataclasses import replace

import valiance.vtypes as T
from valiance.asts import TypedCallNode, TypedElementNode
from valiance.vtypes.relations import (
    _combined_substitution,
    _contains_type_var,
    _ordered_type_vars,
)

from ..state.branches import AnalysisBranch


def specialize_last_call_result(
    branch: AnalysisBranch,
    expected: T.Type,
    context: T.Context,
) -> AnalysisBranch:
    """Use a unique expected result to solve only as-yet-unbound generics.

    The last typed operation must be the producer of the current stack top;
    this cannot retroactively change an earlier operation or a selected
    overload. A normal assignability check still validates the new result.
    """
    if (
        not branch.stack
        or not branch.typed_body
        or _contains_type_var(expected)
        or T.assignable(branch.stack[-1], expected, context)
    ):
        return branch
    typed = branch.typed_body[-1]
    if not isinstance(typed, (TypedElementNode, TypedCallNode)):
        return branch
    previous = typed.overload
    if previous is None or len(previous.actual_returns) != 1:
        return branch
    # The call is already selected, so contextual information must not choose
    # a different overload or reinterpret previously solved arguments.
    if not _contains_type_var(previous.actual_returns[0]):
        return branch
    evidence = T._solve(previous.actual_returns[0], expected, context)
    if evidence is None:
        return branch
    solved = _combined_substitution(evidence, context)
    if solved is None:
        return branch
    for key, value in previous.substitution.items():
        if key in solved and not T.same(value, solved[key]):
            return branch
    signature = previous.overload
    vars_by_name = {
        var.name: T.type_var_key(var)
        for var in _ordered_type_vars(
            *signature.params,
            *signature.returns,
            *(constraint.bound for constraint in signature.generic_constraints),
        )
    }
    all_solutions = {**solved, **previous.substitution}
    arguments = tuple(all_solutions.get(vars_by_name.get(name))
                      for name in signature.generic_params)
    if not arguments or not all(argument is not None for argument in arguments):
        return branch
    applied = T.apply_overload(
        signature, previous.params, context, generic_args=arguments,
    )
    if (
        applied is None
        or len(applied.actual_returns) != 1
        or not T.assignable(applied.actual_returns[0], expected, context)
    ):
        return branch
    new_type = applied.actual_returns[0]
    return replace(
        branch,
        stack=branch.stack.pop().push(new_type),
        typed_body=(*branch.typed_body[:-1], replace(typed, typ=new_type, overload=applied)),
    )
