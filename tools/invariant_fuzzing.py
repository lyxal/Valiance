"""Independent, reproducible models for source typing and VM ownership.

Source cases are well typed by construction, with a deliberately ill-typed
variant.  Values and ownership counts are tracked outside the Valiance runtime.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.runtime_values import (
    BorrowedValue,
    ObjectRuntimeType,
    ObjectValue,
    PanicSignal,
    RuntimeNumber,
)
from valiance.runtime.vm import (
    VirtualMachine,
    _duplicate_occurrence,
    _release_value,
    _retain_runtime_reference,
    _set_field,
)


@dataclass(frozen=True, slots=True)
class TypedSourceCase:
    """Source, expected result, and a type-invalid mutation of the same world."""

    family: str
    values: tuple[int, ...]
    source: str
    expected: tuple[Any, ...]
    invalid_source: str


def typed_case(family: str, values: tuple[int, ...]) -> TypedSourceCase:
    """Construct examples from the model's typed grammar, never from VM output."""
    if family == "generic-call":
        value, delta = values
        declaration = (
            "define[T] identity(value: T) -> T => $value end\n"
            f"define shift(value: Int) -> Int => $value {delta} + end\n"
        )
        source = declaration + f"identity[Int](shift({value})) as[Number] as![Int]"
        invalid = declaration + 'identity[Int](shift("not an Int"))'
        expected: tuple[Any, ...] = (value + delta,)
    elif family == "branch":
        value, threshold, left, right = values
        declaration = (
            "define select(value: Int) -> Int =>\n"
            f"  if ($value > {threshold}) => {left}\n"
            f"  else => {right}\n"
            "end\n"
        )
        source = declaration + f"select({value})"
        invalid = declaration + 'select("not an Int")'
        expected = (left if value > threshold else right,)
    elif family == "match":
        value, sentinel, left, right = values
        declaration = (
            "define select(value: Int) -> Int =>\n"
            "  $value |\n"
            "  match =>\n"
            f"    {sentinel} => {left}\n"
            f"    _ => {right}\n"
            "  end\n"
            "end\n"
        )
        source = declaration + f"select({value})"
        invalid = declaration + 'select("not an Int")'
        expected = (left if value == sentinel else right,)
    elif family == "composition":
        value, delta, first, second = values
        declaration = (
            "define[T] identity(value: T) -> T => $value end\n"
            f"define shift(value: Int) -> Int => $value {delta} + end\n"
        )
        source = (
            declaration
            + f"$items = [{first}, {second}]\n"
            + f"$items := append identity[Int](shift({value}))\n"
            + "$items[2] as[Number] as![Int]"
        )
        invalid = source + '\nidentity[Int]("not an Int")'
        expected = (value + delta,)
    elif family == "mutation":
        # A separate Python list is the state machine's oracle, not a Valiance
        # collection or a value derived from executing the compiler.
        first, second, extra, extra2 = values
        model = [first, second]
        lines = [f"$items = [{first}, {second}]"]
        for addition in (extra, extra2):
            lines.append(f"$items := append {addition}")
            model.append(addition)
        lines.extend(("$items := dropLast", "$items"))
        model.pop()
        source = "\n".join(lines)
        invalid = source + '\ndefine take(value: Int) -> Int => $value end\ntake("bad")'
        expected = (model,)
    else:
        raise ValueError(f"unknown typed source family: {family}")
    return TypedSourceCase(family, values, source, expected, invalid)


SOURCE_FAMILIES = ("generic-call", "branch", "match", "mutation", "composition")


def random_typed_case(rng: random.Random) -> TypedSourceCase:
    """Choose bounded source parameters without consulting analyser semantics."""
    family = rng.choice(SOURCE_FAMILIES)
    width = 2 if family == "generic-call" else 4
    return typed_case(family, tuple(rng.randint(-12, 12) for _ in range(width)))


def mutate_typed_case(rng: random.Random, parent: TypedSourceCase) -> TypedSourceCase:
    """Perturb a corpus winner while preserving its independent type model."""
    if rng.randrange(7) == 0:
        return random_typed_case(rng)
    values = list(parent.values)
    if parent.family in ("branch", "match") and rng.randrange(2) == 0:
        # Drive the input to both sides of the recorded predicate/pattern.
        values[0] = values[1] + rng.choice((-1, 0, 1))
    else:
        index = rng.randrange(len(values))
        values[index] += rng.choice((-3, -1, 1, 3))
    return typed_case(parent.family, tuple(values))


def check_typed_case(case: TypedSourceCase, *, all_modes: bool = True) -> None:
    """Assert progress, output types/values, and analysis rejection of bad calls."""
    analyser = Analyser()
    typed = analyser.analyse(parse(case.source))
    if analyser.diagnostics:
        raise AssertionError(f"valid {case.family} rejected: {analyser.diagnostics}")
    modes = (
        ((False, False), (False, True), (True, False), (True, True))
        if all_modes else ((False, False),)
    )
    for optimize, serialized in modes:
        program = compile_program(typed, optimize=optimize)
        if serialized:
            program = loads(dumps(program))
        results = run(program)
        if len(results) != len(case.expected):
            raise AssertionError(
                (case.source, optimize, serialized, results, case.expected)
            )
        for actual, expected in zip(results, case.expected, strict=True):
            if isinstance(expected, list):
                if (
                    not isinstance(actual, list)
                    or len(actual) != len(expected)
                    or any(
                        not isinstance(item, RuntimeNumber) or item != target
                        for item, target in zip(actual, expected, strict=True)
                    )
                ):
                    raise AssertionError((case.source, actual, expected))
            elif not isinstance(actual, RuntimeNumber) or actual != expected:
                raise AssertionError((case.source, actual, expected))
    rejected = Analyser()
    rejected.analyse(parse(case.invalid_source))
    if not rejected.diagnostics:
        raise AssertionError(f"ill-typed program was accepted: {case.invalid_source}")


def check_reconstruction_transition(shared: bool) -> None:
    """Check unique transfer and shared-version retain/release independently."""
    leaf = ObjectValue("Leaf", {})
    old = ObjectValue("Parent", {"leaf": leaf, "count": 1})
    borrow = BorrowedValue(old)
    with VirtualMachine() as vm:
        if shared:
            _retain_runtime_reference(old)  # Two owning occurrences of old.
        new = _set_field(old, "count", 2, vm=vm)
        if new is old or new.fields != {"leaf": leaf, "count": 2}:
            raise AssertionError("reconstruction failed to create an updated version")
        if new.refcount != 1 or old.refcount != 1:
            raise AssertionError("reconstruction counted owners incorrectly")
        if shared:
            if old.ownership_transferred or leaf.refcount != 2:
                raise AssertionError("shared reconstruction lost a field owner")
            if borrow.read() is not old:
                raise AssertionError("borrow lost access to retained old version")
        else:
            if not old.ownership_transferred or leaf.refcount != 1:
                raise AssertionError("unique reconstruction failed to transfer ownership")
            try:
                borrow.read()
            except RuntimeError:
                pass
            else:
                raise AssertionError("borrow survived a transferred owner")
        _release_value(old, vm)
        if leaf.refcount != 1 or leaf.destroyed:
            raise AssertionError("old version released fields still owned by replacement")
        _release_value(new, vm)
        if not leaf.destroyed or leaf.refcount != 0 or not new.destroyed:
            raise AssertionError("reconstructed version leaked its field")


def check_cleanup_during_unwind() -> None:
    """A pending panic survives a must-call cleanup fault while fields die."""
    child = ObjectValue("Child", {})
    parent = ObjectValue(
        "Parent", {"child": child},
        runtime_type=ObjectRuntimeType(
            mustcall_mode="all", mustcall_methods=("close",)
        ),
    )
    original_fault = PanicSignal(ObjectValue("ValueFault", {}))
    with VirtualMachine() as vm:
        with vm._unwinding(original_fault):
            # A protocol violation is attached to the active unwind rather
            # than hiding it, while child owners are still released.
            _release_value(parent, vm)
        if not parent.destroyed or not child.destroyed:
            raise AssertionError("panic unwind leaked an owned field")
        secondary = getattr(original_fault, "secondary_faults", ())
        if len(secondary) != 1 or secondary[0].value.type_name != "MustCallFault":
            raise AssertionError("cleanup fault did not preserve pending panic")


def check_ownership_sequence(rng: random.Random, steps: int = 30) -> list[str]:
    """Compare VM reference transitions with an independent owner-count model.

    The child is owned by its parent. An optional must-call fault verifies that
    failure during final release cannot leave fields alive. A failed @nodup
    attempt must not retain any additional occurrence.
    """
    require_close = bool(rng.randrange(2))
    forbid_duplicate = bool(rng.randrange(2))
    satisfied = bool(rng.randrange(2))
    child = ObjectValue("Child", {})
    runtime = ObjectRuntimeType(
        dup_error="cannot duplicate" if forbid_duplicate else None,
        mustcall_mode="all" if require_close else None,
        mustcall_methods=("close",) if require_close else (),
    )
    parent = ObjectValue("Parent", {"child": child}, runtime_type=runtime)
    if satisfied:
        parent.mustcall_called = frozenset(("close",))
    owners = 1
    borrows = [BorrowedValue(parent)]
    operations: list[str] = []

    with VirtualMachine() as vm:
        for _ in range(steps):
            action = rng.choice(
                ("borrow", "read", "retain", "duplicate", "release", "unwind")
            )
            if action == "borrow":
                borrows.append(BorrowedValue(parent))
            elif action == "read":
                if borrows and borrows[rng.randrange(len(borrows))].read() is not parent:
                    raise AssertionError("borrow changed identity")
            elif action == "retain":
                _retain_runtime_reference(parent)
                owners += 1
            elif action == "duplicate":
                if forbid_duplicate:
                    try:
                        _duplicate_occurrence(parent)
                    except PanicSignal as fault:
                        if fault.value.type_name != "DuplicationFault":
                            raise
                    else:
                        raise AssertionError("@nodup occurrence was duplicated")
                else:
                    _duplicate_occurrence(parent)
                    owners += 1
            elif action == "release" and owners > 1:
                _release_value(parent, vm)
                owners -= 1
            elif action == "unwind":
                # Releasing an alias during fault propagation must preserve the
                # final owner's lifetime, regardless of a pending panic.
                if owners > 1:
                    with vm._unwinding(PanicSignal(ObjectValue("Fault", {}))):
                        _release_value(parent, vm)
                    owners -= 1
            operations.append(action)
            if parent.refcount != owners or parent.destroyed or child.destroyed:
                raise AssertionError((operations, owners, parent.refcount))
            if child.refcount != 1:
                raise AssertionError("child released before last parent owner")

        for _ in range(owners - 1):
            _release_value(parent, vm)
        must_fault = require_close and not satisfied
        try:
            _release_value(parent, vm)
        except PanicSignal as exc:
            if not must_fault or exc.value.type_name != "MustCallFault":
                raise
        else:
            if must_fault:
                raise AssertionError("missing must-call fault on final release")
        if (
            not parent.destroyed or not child.destroyed
            or parent.refcount != 0 or child.refcount != 0
        ):
            raise AssertionError("final owner failed to tear down its owned graph")
        for borrow in borrows:
            try:
                borrow.read()
            except RuntimeError as exc:
                if "outlived" not in str(exc):
                    raise
            else:
                raise AssertionError("stale borrow remained readable")
        try:
            _retain_runtime_reference(parent)
        except RuntimeError:
            pass
        else:
            raise AssertionError("destroyed object was retained")
    check_reconstruction_transition(bool(rng.randrange(2)))
    return operations
