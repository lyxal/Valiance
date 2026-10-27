"""Managed reflection metadata and descriptor validation for owner access."""

from __future__ import annotations

from dataclasses import dataclass, field

from valiance.runtime.bytecode import ReflectionFieldReference, ReflectionFieldsReference
from valiance.runtime.runtime_values import ObjectRuntimeType, ObjectValue, PanicSignal, RuntimeNumber
from valiance.runtime.transfer import IsolatedResource


@dataclass
class UninitValue(ObjectValue, IsolatedResource):
    """Managed partial storage whose own cleanup never runs the owner's destructor."""

    layout: ReflectionFieldsReference | None = None
    owner_runtime: ObjectRuntimeType | None = None
    owner_name: str = ""
    consumed: bool = False

    def missing_fields(self) -> tuple[str, ...]:
        """Return unset declared slots in declaration order."""
        return tuple(field.name for field in self.layout.fields if field.name not in self.fields)


class ReflectionValue(ObjectValue):
    """An ordinary managed metadata value whose public properties are read-only."""


@dataclass
class RuntimeTypeWitness(ObjectValue):
    """An existential type witness tied to the VM that checked its schema.

    A runtime witness never becomes a compiler-visible nominal declaration.
    Its registry identity cannot be supplied through Valiance object fields.
    """

    registry_identity: object | None = field(default=None, compare=False, repr=False)


class DynamicValue(ObjectValue):
    """A checked existential box owning its payload as a managed child value."""

    def __init__(self, payload: object):
        """Own the payload without exposing its storage as a static field."""
        super().__init__("Dynamic", {"#value": payload})


def make_type_metadata(recipe: tuple):
    """Allocate fresh metadata occurrences from a checked portable recipe."""
    from valiance.runtime.runtime_values import ListValue, RecordValue

    kind, *data = recipe
    if kind == "type":
        return ReflectionValue("Type", {}, type_args=(data[0],))
    if kind == "text":
        return data[0]
    if kind == "int":
        return RuntimeNumber(data[0])
    if kind == "list":
        return ListValue(make_type_metadata(item) for item in data[0])
    if kind == "record":
        return RecordValue(((name, make_type_metadata(item)) for name, item in data[1]), type_signature=data[0])
    if kind == "info":
        return ReflectionValue("TypeInfo", {name: make_type_metadata(item) for name, item in data[1]},
                               type_args=(data[0],))
    raise RuntimeError("invalid checked metadata recipe")


def make_fields_value(reference: ReflectionFieldsReference) -> ReflectionValue:
    """Allocate independent metadata occurrences for one concrete storage layout."""
    fields = tuple(
        _make_field_value(reference.owner, index, field)
        for index, field in enumerate(reference.fields)
    )
    return ReflectionValue("Fields", {"#fields": fields}, type_args=(reference.owner,))


def _make_field_value(owner: str, index: int, field: ReflectionFieldReference) -> ReflectionValue:
    """Construct one independently owned descriptor from storage evidence."""
    return ReflectionValue("Field", {
        "name": field.name,
        "type": ReflectionValue("Type", {}, type_args=(field.value_type,)),
        "owner": ReflectionValue("Type", {}, type_args=(owner,)),
        "index": RuntimeNumber(index),
        "visibility": ReflectionValue("Visibility", {"name": field.visibility}),
    }, type_args=(owner,))


def copy_field_value(field: ReflectionValue) -> ReflectionValue:
    """Materialize owner-free metadata without retaining its parent collection."""
    return _make_field_value(field.type_args[0], int(field.fields["index"]),
                             ReflectionFieldReference(
                                 field.fields["name"], field.fields["type"].type_args[0],
                                 field.fields["visibility"].fields["name"],
                             ))


def find_field(collection: ReflectionValue, name: str) -> ReflectionValue | None:
    """Find a declared field without specializing literal names or accessing values."""
    return next((field for field in collection.fields["#fields"]
                 if field.fields["name"] == name), None)


def lookup_field(collection: ReflectionValue, name: str) -> ReflectionValue:
    """Return a descriptor or raise the ordinary missing-key panic."""
    field = find_field(collection, name)
    if field is None:
        raise PanicSignal(ObjectValue("KeyFault", {
            "message": f"{collection.type_args[0]} has no stored field '{name}'",
        }))
    return field


def validate_field(field: object, layout: ReflectionFieldsReference) -> ReflectionFieldReference:
    """Validate complete descriptor identity before consulting access permissions."""
    if isinstance(field, ReflectionValue) and field.type_name == "Field":
        index = field.fields.get("index")
        if (isinstance(index, RuntimeNumber) and index == index.to_integral_value()
                and 0 <= index < len(layout.fields) and field.type_args == (layout.owner,)):
            reference = layout.fields[int(index)]
            if (field.fields.get("name") == reference.name
                    and field.fields["owner"].type_args == (layout.owner,)
                    and field.fields["type"].type_args == (reference.value_type,)
                    and field.fields["visibility"].fields["name"] == reference.visibility):
                return reference
    raise PanicSignal(ObjectValue("ReflectionFieldFault", {
        "message": "field descriptor does not match the receiver's storage layout",
    }))


# Host-registered types have no checked source-level owner in the closed compiler
# universe. The following operations provide reflection on their runtime values
# without inventing bytecode-side type evidence or unchecked field descriptors.

def checked_native_value(ffi_type: str, value: object) -> object:
    """Check a trusted native factory's result; never manufacture native storage."""
    from valiance.runtime.runtime_values import FFIHandleValue, FFIScalarValue, FFIStructValue

    if not isinstance(value, (FFIHandleValue, FFIScalarValue, FFIStructValue)) or value.ffi_type != ffi_type:
        raise ValueError(f"native constructor returned an invalid {ffi_type} value")
    if isinstance(value, FFIHandleValue) and (not value.alive or value.destroy_pending):
        raise ValueError(f"native constructor returned a non-live {ffi_type} handle")
    return value


def _instantiate_runtime_type(typ: object, substitutions: dict[str, object]) -> object:
    """Substitute checked runtime schema generic parameters without using JSON."""
    from dataclasses import replace
    import valiance.vtypes as T

    if isinstance(typ, T.NominalType):
        if not typ.args and typ.name.text in substitutions:
            return substitutions[typ.name.text]
        return replace(typ, args=tuple(_instantiate_runtime_type(arg, substitutions) for arg in typ.args))
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return replace(typ, inner=_instantiate_runtime_type(typ.inner, substitutions))
    if isinstance(typ, T.CollectionType):
        return replace(typ, base=_instantiate_runtime_type(typ.base, substitutions))
    if isinstance(typ, T.UnionType):
        return replace(typ, items=frozenset(_instantiate_runtime_type(arg, substitutions) for arg in typ.items))
    return typ


def _reflection_value_matches(value: object, expected: object, *, depth: int = 0) -> bool:
    """Conservatively check reified runtime values before host-side construction.

    This is deliberately stricter than a source-level cast: an unknown or erased
    type is *not* evidence that a value may safely inhabit that storage slot.
    """
    import valiance.vtypes as T
    from valiance.runtime.runtime_values import (
        FFIHandleValue, FFIScalarValue, FFIStructValue, RuntimeNumber, TaggedValue,
        ObjectValue, RecordValue, is_list_like,
    )

    if depth > 128:
        return False
    if isinstance(expected, (T.NoVecType, T.ExactType)):
        return _reflection_value_matches(value, expected.inner, depth=depth + 1)
    if isinstance(expected, T.TaggedType):
        boolean = any(tag.name == 'boolean' and tag.depth == 0 and not tag.absent for tag in expected.tags)
        if boolean:
            if isinstance(value, bool):
                return True
            if not isinstance(value, TaggedValue) or not any(
                tag.name == 'boolean' and tag.depth == 0 and not tag.absent for tag in value.tags
            ):
                return False
        if isinstance(value, TaggedValue):
            value = value.value
        # Source-depth collection tags are preserved by their actual values.
        return _reflection_value_matches(value, expected.inner, depth=depth + 1)
    if isinstance(value, TaggedValue):
        value = value.value
    if isinstance(expected, T.FFINamedType):
        try:
            checked_native_value(str(expected), value)
        except ValueError:
            return False
        return True
    if isinstance(expected, T.CollectionType):
        if not is_list_like(value) or isinstance(value, (str, dict)):
            return False
        if not isinstance(expected.rank, int) or expected.rank < 1:
            return False
        element_type = (expected.base if expected.rank == 1 else
                        type(expected)(expected.base, expected.rank - 1))
        # Refuse lazy iterables: forcing them here could never terminate.
        if not isinstance(value, (list, tuple)):
            return False
        return all(_reflection_value_matches(item, element_type, depth=depth + 1) for item in value)
    if isinstance(expected, T.TupleType):
        return (isinstance(value, tuple) and len(value) == len(expected.params)
                and all(_reflection_value_matches(item, typ, depth=depth + 1)
                        for item, typ in zip(value, expected.params, strict=True)))
    if isinstance(expected, T.RecordType):
        return (isinstance(value, RecordValue)
                and set(value) == {field.name.text for field in expected.fields}
                and all(_reflection_value_matches(value[field.name.text], field.typ, depth=depth + 1)
                        for field in expected.fields))
    if isinstance(expected, T.UnionType):
        return any(_reflection_value_matches(value, item, depth=depth + 1) for item in expected.items)
    if isinstance(expected, T.NoneTypeNode):
        return value is None or (isinstance(value, ObjectValue) and value.type_name == 'None')
    if isinstance(expected, T.NominalType):
        name = expected.name.text
        if name == 'Dynamic' and not expected.args:
            return isinstance(value, DynamicValue)
        if name == 'RuntimeType' and not expected.args:
            return isinstance(value, RuntimeTypeWitness)
        if name == 'String':
            return isinstance(value, str)
        if name in {'Int', 'Real', 'Number'}:
            if isinstance(value, bool):
                return False
            if not isinstance(value, (int, RuntimeNumber)):
                return False
            if name == 'Int':
                try:
                    return value == int(value)
                except (ValueError, TypeError, OverflowError):
                    return False
            return True
        if name == 'None':
            return value is None or (isinstance(value, ObjectValue) and value.type_name == 'None')
        if name == 'Some' and len(expected.args) == 1:
            return (isinstance(value, ObjectValue) and value.type_name == 'Some'
                    and 'value' in value.fields and
                    _reflection_value_matches(value.fields['value'], expected.args[0], depth=depth + 1))
        if name == 'Dict' and len(expected.args) == 2:
            return (isinstance(value, dict) and
                    all(_reflection_value_matches(key, expected.args[0], depth=depth + 1)
                        and _reflection_value_matches(item, expected.args[1], depth=depth + 1)
                        for key, item in value.items()))
        if (not isinstance(value, ObjectValue) or value.type_name != name or
                value.type_args != tuple(str(arg) for arg in expected.args)):
            return False
        runtime = value.runtime_type
        if runtime is None or runtime.json_fields is None or runtime.dup_error is not None:
            return False
        if len(runtime.json_generics) != len(expected.args):
            return False
        from valiance.parsing.parser import parse_type
        arguments = dict(zip(runtime.json_generics, expected.args, strict=True))
        if set(value.fields) != {key for key, _, _ in runtime.json_fields}:
            return False
        for key, _, declaration in runtime.json_fields:
            if declaration is None:
                return False
            inner = _instantiate_runtime_type(parse_type(declaration), arguments)
            if not _reflection_value_matches(value.fields[key], inner, depth=depth + 1):
                return False
        return True
    return False


def construct_runtime_object(constructor: object, target_type: str, supplied: dict[str, object], *,
                             unsafe: bool = False) -> ObjectValue:
    """Construct runtime-schema-managed storage without invoking a JSON parser.

    Unsafe permits host-supplied private fields; ordinary calls can only use
    public/readable slots and trusted defaults. Custom initializers are never
    bypassed here, unlike the explicitly unsafe compiled Uninit[T] workflow.
    """
    from valiance.parsing.parser import parse_type
    import valiance.vtypes as T

    parsed = parse_type(target_type)
    runtime = constructor.runtime_type
    if (not isinstance(parsed, T.NominalType) or parsed.name.text != constructor.type_name
            or runtime is None or runtime.json_fields is None):
        raise ValueError(f"no runtime reflection layout for {target_type}")
    if constructor.initializer is not None or runtime.dup_error is not None:
        raise ValueError(f"{target_type} cannot be reflectively constructed")
    if len(runtime.json_generics) != len(parsed.args):
        raise ValueError(f"generic arguments do not match {target_type}")
    args = dict(zip(runtime.json_generics, parsed.args, strict=True))
    fields = dict(constructor.defaults)
    entries = {key: (visibility, declaration) for key, visibility, declaration in runtime.json_fields}
    unknown = set(supplied) - set(entries)
    if unknown:
        raise ValueError(f"unknown fields for {target_type}: {sorted(unknown)}")
    for key, value in supplied.items():
        visibility, declaration = entries[key]
        if visibility == 'private' and not unsafe:
            raise ValueError(f"field {key!r} requires unsafe reflection")
        if declaration is None:
            raise ValueError(f"no checked field type for {key!r}")
        expected = _instantiate_runtime_type(parse_type(declaration), args)
        if not _reflection_value_matches(value, expected):
            raise ValueError(f"field {key!r} does not match {expected}")
        fields[key] = value
    for key, visibility, declaration in runtime.json_fields:
        if key not in fields:
            raise ValueError(f"missing required field {key!r} for {target_type}")
        if declaration is not None:
            expected = _instantiate_runtime_type(parse_type(declaration), args)
            if not _reflection_value_matches(fields[key], expected):
                raise ValueError(f"field {key!r} default does not match {expected}")
    return ObjectValue(constructor.type_name, fields, tuple(str(arg) for arg in parsed.args),
                       runtime_type=runtime)
