"""Conservative path-sensitive proof for local ``@mustcall`` obligations."""

from __future__ import annotations

from dataclasses import dataclass, replace

from valiance.asts import (
    ASTNode,
    AtNode,
    ElementNode,
    GetVariableNode,
    IfNode,
    ReturnNode,
    SetVariableNode,
    TryNode,
    WhileNode,
)
from valiance.vtypes.environment import Environment
from valiance.vtypes.symbols import Symbol


@dataclass(frozen=True)
class MustCallViolation:
    """One locally created object that can reach function exit unresolved."""

    type_name: Symbol
    mode: str
    methods: tuple[str, ...]
    location: object | None


@dataclass(frozen=True)
class _Obligation:
    type_name: Symbol
    mode: str
    methods: tuple[str, ...]
    called: frozenset[str]
    location: object | None
    escaped: bool = False

    @property
    def satisfied(self) -> bool:
        """Return whether the required call set is definitely satisfied."""
        required = frozenset(self.methods)
        return required.issubset(self.called) if self.mode == "all" else bool(
            required & self.called
        )


@dataclass(frozen=True)
class _State:
    obligations: tuple[_Obligation, ...] = ()
    variables: tuple[tuple[Symbol, int], ...] = ()
    stack: tuple[int | None, ...] = ()
    terminal: bool = False

    def variable(self, name: Symbol) -> int | None:
        """Return the allocation token currently held by one local variable."""
        return dict(self.variables).get(name)

    def bind(self, name: Symbol, token: int | None) -> _State:
        """Bind one local alias to an allocation token."""
        variables = dict(self.variables)
        if token is None:
            variables.pop(name, None)
        else:
            variables[name] = token
        return replace(self, variables=tuple(sorted(variables.items(), key=lambda item: str(item[0]))))

    def push(self, token: int | None) -> _State:
        """Push one symbolic allocation token onto the flow stack."""
        return replace(self, stack=(*self.stack, token))

    def pop(self) -> tuple[int | None, _State]:
        """Pop one symbolic allocation token without underflowing."""
        if not self.stack:
            return None, self
        return self.stack[-1], replace(self, stack=self.stack[:-1])

    def update_obligation(self, token: int, **changes: object) -> _State:
        """Return a state with one allocation obligation immutably updated."""
        if token < 0 or token >= len(self.obligations):
            return self
        obligations = list(self.obligations)
        obligations[token] = replace(obligations[token], **changes)
        return replace(self, obligations=tuple(obligations))

    def escape_stack(self) -> _State:
        """Mark allocation tokens remaining on the return stack as transferred."""
        state = self
        for token in self.stack:
            if token is not None:
                state = state.update_obligation(token, escaped=True)
        return replace(state, terminal=True)


def prove_local_mustcall(
    body: tuple[ASTNode, ...],
    env: Environment,
) -> tuple[MustCallViolation, ...]:
    """Return obligations whose local destruction is provably unresolved."""
    states = _analyse_sequence(body, (_State(),), env)
    violations: dict[tuple[Symbol, object | None], MustCallViolation] = {}
    for state in states:
        final = state if state.terminal else state.escape_stack()
        for obligation in final.obligations:
            if obligation.escaped or obligation.satisfied:
                continue
            key = obligation.type_name, obligation.location
            violations[key] = MustCallViolation(
                obligation.type_name,
                obligation.mode,
                obligation.methods,
                obligation.location,
            )
    return tuple(violations.values())


def _analyse_sequence(
    nodes: tuple[ASTNode, ...],
    states: tuple[_State, ...],
    env: Environment,
) -> tuple[_State, ...]:
    """Apply a source sequence independently to every reachable flow state."""
    current = states
    for node in nodes:
        next_states: list[_State] = []
        for state in current:
            if state.terminal:
                next_states.append(state)
            else:
                next_states.extend(_analyse_node(node, state, env))
        current = tuple(next_states)
    return current


def _analyse_node(node: ASTNode, state: _State, env: Environment) -> tuple[_State, ...]:
    """Transfer one source node through the bounded must-call flow model."""
    if isinstance(node, ElementNode):
        definition = env.lookup_object(node.name)
        if (
            definition is not None
            and definition.mustcall_mode is not None
            and definition.mustcall_methods
        ):
            token = len(state.obligations)
            obligation = _Obligation(
                node.name,
                definition.mustcall_mode,
                definition.mustcall_methods,
                frozenset(),
                node.location,
            )
            return (replace(state, obligations=(*state.obligations, obligation)).push(token),)
        token = state.stack[-1] if state.stack else None
        if token is not None and token < len(state.obligations):
            obligation = state.obligations[token]
            method = node.name.text
            if method in obligation.methods:
                state = state.update_obligation(
                    token,
                    called=frozenset((*obligation.called, method)),
                )
                _receiver, state = state.pop()
        return (state,)
    if isinstance(node, GetVariableNode):
        return (state.push(state.variable(node.name)),)
    if isinstance(node, SetVariableNode):
        token, state = state.pop()
        return (state.bind(node.name, token),)
    if isinstance(node, IfNode):
        conditioned = _analyse_sequence(node.condition, (state,), env)
        outputs: list[_State] = []
        for branch_state in conditioned:
            branch_state = replace(branch_state, stack=state.stack)
            outputs.extend(_analyse_sequence(node.then_branch, (branch_state,), env))
            outputs.extend(_analyse_sequence(node.else_branch, (branch_state,), env))
        return tuple(outputs)
    if isinstance(node, TryNode):
        outputs = list(_analyse_sequence(node.body, (state,), env))
        for handler in node.handlers:
            outputs.extend(_analyse_sequence(handler.body, (state,), env))
        return tuple(outputs)
    if isinstance(node, WhileNode):
        # Include the zero-iteration path and one representative iteration.
        entered = _analyse_sequence(node.condition, (state,), env)
        outputs = list(entered)
        for entered_state in entered:
            outputs.extend(_analyse_sequence(node.body, (entered_state,), env))
        return tuple(outputs)
    if isinstance(node, AtNode):
        return _analyse_sequence(node.body, (state,), env)
    if isinstance(node, ReturnNode):
        returned = state
        for expression in node.values:
            [returned] = _analyse_sequence(expression, (returned,), env)
        return (returned.escape_stack(),)
    return (state,)
