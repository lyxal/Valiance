"""Immutable variable frames for branch-based analysis."""

from __future__ import annotations

from dataclasses import dataclass

import valiance.vtypes as T
from valiance.vtypes.symbols import Symbol

from . import transformations as _ops


@dataclass(frozen=True)
class VariableWrite:
    """The result of an immutable variable assignment."""

    variables: BranchVariables | None
    error: str | None = None


@dataclass(frozen=True)
class BranchVariables:
    """Branch-local variable facts.

    The environment owns global facts such as overloads and object definitions.
    This record owns values whose types can differ between analysis branches.
    """

    function_locals: tuple[tuple[Symbol, T.Type], ...] = ()
    parameters: tuple[tuple[Symbol, T.Type], ...] = ()
    captures: tuple[tuple[Symbol, T.Type], ...] = ()
    block_locals: tuple[tuple[Symbol, T.Type], ...] = ()
    function_constants: tuple[Symbol, ...] = ()
    block_constants: tuple[Symbol, ...] = ()

    def visible_names(self) -> tuple[Symbol, ...]:
        """Return variable names readable from this branch frame."""
        names = {
            name
            for entries in (
                self.function_locals,
                self.parameters,
                self.captures,
                self.block_locals,
            )
            for name, _ in entries
        }
        return tuple(sorted(names, key=str))

    @classmethod
    def from_parameters(
        cls,
        params: tuple[tuple[Symbol, T.Type], ...],
        *,
        captures: BranchVariables | None = None,
    ) -> BranchVariables:
        """Create a function variable frame from named parameters."""
        captured = () if captures is None else captures.visible_items()
        return cls(
            parameters=_ops._sorted_items(params),
            captures=_ops._sorted_items(captured),
        )

    def visible_items(self) -> tuple[tuple[Symbol, T.Type], ...]:
        """Return all currently readable variables, inner names first."""
        result: dict[Symbol, T.Type] = {}
        for name, typ in reversed(self.captures):
            result.setdefault(name, typ)
        for name, typ in reversed(self.parameters):
            result[name] = typ
        for name, typ in reversed(self.function_locals):
            result[name] = typ
        for name, typ in reversed(self.block_locals):
            result[name] = typ
        return _ops._sorted_items(result.items())

    def constant_items(self) -> tuple[tuple[Symbol, T.Type], ...]:
        """Return visible immutable bindings that are safe to read in functions."""
        constant_names = set(self.function_constants) | set(self.block_constants)
        return tuple(
            (name, typ)
            for name, typ in self.visible_items()
            if name in constant_names
        )

    def nonconstant_names(self) -> frozenset[Symbol]:
        """Return visible binding names that may change after function creation."""
        constant_names = set(self.function_constants) | set(self.block_constants)
        return frozenset(
            name for name, _typ in self.visible_items() if name not in constant_names
        )

    def read(self, name: Symbol) -> T.Type | None:
        """Read a variable using block, function, parameter, capture order."""
        for scope in (
            self.block_locals,
            self.function_locals,
            self.parameters,
            self.captures,
        ):
            typ = _ops._lookup(scope, name)
            if typ is not None:
                return typ
        return None

    def write(
        self,
        name: Symbol,
        typ: T.Type,
        *,
        block_local: bool = False,
        constant: bool = False,
        ctx: T.Context | None = None,
    ) -> VariableWrite:
        """Return variables after assigning ``name`` in this branch."""
        ctx = ctx or T.Context()
        existing_block_local = _ops._lookup(self.block_locals, name)
        if existing_block_local is not None:
            if name in self.block_constants:
                return VariableWrite(None, f"cannot assign to constant '{name}'")
            stored_type = _ops._assignment_stored_type(existing_block_local, typ, ctx)
            diagnostic = _ops._assignment_error(name, typ, existing_block_local, ctx)
            if diagnostic is not None:
                return VariableWrite(None, diagnostic)
            if T.same(stored_type, existing_block_local):
                return VariableWrite(self)
            return VariableWrite(
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=_ops._set_item(self.block_locals, name, stored_type),
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
            )
        if _ops._lookup(self.parameters, name) is not None:
            return VariableWrite(
                None,
                f"cannot assign to read-only parameter '{name}'",
            )
        existing_function_local = _ops._lookup(self.function_locals, name)
        if existing_function_local is not None:
            if name in self.function_constants:
                return VariableWrite(None, f"cannot assign to constant '{name}'")
            stored_type = _ops._assignment_stored_type(
                existing_function_local,
                typ,
                ctx,
            )
            diagnostic = _ops._assignment_error(
                name,
                typ,
                existing_function_local,
                ctx,
            )
            if diagnostic is not None:
                return VariableWrite(None, diagnostic)
            return VariableWrite(
                BranchVariables(
                    function_locals=_ops._set_item(
                        self.function_locals,
                        name,
                        stored_type,
                    ),
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
            )
        if _ops._lookup(self.captures, name) is not None:
            return VariableWrite(
                BranchVariables(
                    function_locals=_ops._set_item(self.function_locals, name, typ),
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
                    function_constants=_ops._set_symbol_flag(
                        self.function_constants, name, constant
                    ),
                    block_constants=self.block_constants,
                ),
            )
        if block_local or _ops._lookup(self.block_locals, name) is not None:
            return VariableWrite(
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=_ops._set_item(self.block_locals, name, typ),
                    function_constants=self.function_constants,
                    block_constants=_ops._set_symbol_flag(
                        self.block_constants, name, constant
                    ),
                ),
            )
        return VariableWrite(
            BranchVariables(
                function_locals=_ops._set_item(self.function_locals, name, typ),
                parameters=self.parameters,
                captures=self.captures,
                block_locals=self.block_locals,
                function_constants=_ops._set_symbol_flag(
                    self.function_constants, name, constant
                ),
                block_constants=self.block_constants,
            ),
        )

    def with_block_local(self, name: Symbol, typ: T.Type) -> BranchVariables:
        """Add or replace a temporary block-local variable."""
        return BranchVariables(
            function_locals=self.function_locals,
            parameters=self.parameters,
            captures=self.captures,
            block_locals=_ops._set_item(self.block_locals, name, typ),
            function_constants=self.function_constants,
            block_constants=self.block_constants,
        )

    def drop_block_locals(self) -> BranchVariables:
        """Return this frame after leaving a block-local scope."""
        return BranchVariables(
            function_locals=self.function_locals,
            parameters=self.parameters,
            captures=self.captures,
            function_constants=self.function_constants,
        )

    def refine_type(self, old: T.Type, new: T.Type) -> BranchVariables:
        """Replace one type fact wherever it appears in visible branch variables."""
        return BranchVariables(
            function_locals=_ops._refine_items(self.function_locals, old, new),
            parameters=_ops._refine_items(self.parameters, old, new),
            captures=_ops._refine_items(self.captures, old, new),
            block_locals=_ops._refine_items(self.block_locals, old, new),
            function_constants=self.function_constants,
            block_constants=self.block_constants,
        )

    def refine_input_requirement(
        self, old: T.Type, new: T.Type
    ) -> BranchVariables:
        """Refine parameter-backed variables with a call requirement."""
        return BranchVariables(
            function_locals=_ops._refine_input_requirement_items(
                self.function_locals, old, new
            ),
            parameters=_ops._refine_input_requirement_items(
                self.parameters, old, new
            ),
            captures=self.captures,
            block_locals=self.block_locals,
            function_constants=self.function_constants,
            block_constants=self.block_constants,
        )

    def merge_against(
        self,
        other: BranchVariables,
        before: BranchVariables,
        ctx: T.Context | None = None,
    ) -> BranchVariables:
        """Merge two branch outputs, preserving only variables visible before."""
        locals_by_name: dict[Symbol, T.Type] = {}
        before_names = {name for name, _ in before.function_locals}
        for name in before_names:
            left = _ops._lookup(self.function_locals, name) or _ops._lookup(
                before.function_locals, name
            )
            right = _ops._lookup(other.function_locals, name) or _ops._lookup(
                before.function_locals, name
            )
            if left is not None and right is not None:
                locals_by_name[name] = (
                    left if left == right else T.merge_types(left, right, ctx)
                )
        block_locals_by_name: dict[Symbol, T.Type] = {}
        before_block_names = {name for name, _ in before.block_locals}
        for name in before_block_names:
            left = _ops._lookup(self.block_locals, name) or _ops._lookup(
                before.block_locals, name
            )
            right = _ops._lookup(other.block_locals, name) or _ops._lookup(
                before.block_locals, name
            )
            if left is not None and right is not None:
                block_locals_by_name[name] = (
                    left if left == right else T.merge_types(left, right, ctx)
                )
        return BranchVariables(
            function_locals=_ops._sorted_items(locals_by_name.items()),
            parameters=before.parameters,
            captures=before.captures,
            block_locals=_ops._sorted_items(block_locals_by_name.items()),
            function_constants=tuple(
                name for name in before.function_constants if name in locals_by_name
            ),
            block_constants=tuple(
                name for name in before.block_constants if name in block_locals_by_name
            ),
        )
