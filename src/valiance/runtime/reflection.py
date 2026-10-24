"""Managed reflection metadata and descriptor validation for owner access."""

from __future__ import annotations

from dataclasses import dataclass

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
