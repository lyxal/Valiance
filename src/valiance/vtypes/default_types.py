"""Canonical names for the built-in types used throughout the compiler."""

from valiance.vtypes.symbols import Symbol
from valiance.vtypes.builders import N, Tagged

Number = N(Symbol("Number"))
Real = N(Symbol("Real"))
Integer = N(Symbol("Integer"))
String = N(Symbol("String"))
Boolean = Tagged(Integer, "boolean")
