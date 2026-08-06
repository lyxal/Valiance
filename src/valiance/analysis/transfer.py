"""Static task-transfer checks for types known during spawn analysis."""

from __future__ import annotations

from dataclasses import dataclass

import valiance.vtypes as T
from valiance.vtypes import Environment
from valiance.vtypes.symbols import Symbol


@dataclass(frozen=True, slots=True)
class TransferTypeViolation:
    """One statically known isolated resource path."""

    path: str
    typ: T.Type

    def render(self) -> str:
        """Render a source-facing transfer diagnostic."""
        return (
            f"cannot spawn because {self.path} has isolated type {T.show(self.typ)}\n"
            "help: move the resource into a task-safe shared abstraction"
        )


def render_transfer_type_violations(
    violations: tuple[TransferTypeViolation, ...],
) -> str:
    """Render several static transfer failures as one focused diagnostic."""
    if len(violations) == 1:
        return violations[0].render()
    details = "\n".join(
        f"  - {violation.path}: isolated type {T.show(violation.typ)}"
        for violation in violations
    )
    return (
        "cannot spawn because the function captures isolated values:\n"
        f"{details}\n"
        "help: move each resource into a task-safe shared abstraction"
    )


def validate_task_transfer_type(
    typ: T.Type,
    env: Environment,
    *,
    path: str,
) -> TransferTypeViolation | None:
    """Return the first statically known isolated value nested in one type."""
    visiting: set[tuple[str, str]] = set()

    def visit(item: T.Type, item_path: str) -> TransferTypeViolation | None:
        """Traverse one normalized type without recursing through cycles."""
        item = T.normalize(item)
        if isinstance(item, T.TaggedType):
            return visit(item.inner, item_path)
        if isinstance(item, (T.NoVecType, T.ExactType)):
            return visit(item.inner, item_path)
        if isinstance(item, T.TaskType):
            return None
        if isinstance(item, T.NominalType):
            if item.name == Symbol("Channel"):
                return None
            definition = env.lookup_object(item.name)
            if definition is not None and definition.task_isolated:
                return TransferTypeViolation(item_path, item)
            for index, arg in enumerate(item.args):
                violation = visit(arg, f"{item_path}.type[{index}]")
                if violation is not None:
                    return violation
            if definition is None:
                return None
            key = (str(item.name), T.show(item))
            if key in visiting:
                return None
            visiting.add(key)
            try:
                for field in definition.attributes:
                    violation = visit(field.typ, f"{item_path}.{field.name}")
                    if violation is not None:
                        return violation
            finally:
                visiting.remove(key)
            return None
        if isinstance(item, T.CollectionType):
            return visit(item.base, f"{item_path}[]")
        if isinstance(item, T.TupleType):
            for index, child in enumerate(item.params):
                violation = visit(child, f"{item_path}[{index}]")
                if violation is not None:
                    return violation
            return None
        if isinstance(item, T.VariadicTupleType):
            for index, child in enumerate(item.items):
                violation = visit(child.typ, f"{item_path}[{index}]")
                if violation is not None:
                    return violation
            return None
        if isinstance(item, (T.UnionType, T.IntersectionType)):
            for child in item.items:
                violation = visit(child, item_path)
                if violation is not None:
                    return violation
            return None
        if isinstance(item, T.RowType):
            violation = visit(item.base, item_path)
            if violation is not None:
                return violation
            for field in item.fields:
                violation = visit(field.typ, f"{item_path}.{field.name}")
                if violation is not None:
                    return violation
        return None

    return visit(typ, path)
