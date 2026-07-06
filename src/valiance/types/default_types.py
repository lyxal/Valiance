from valiance.symbols import Symbol
from valiance.types.builders import N, Tagged

Number = N(Symbol("Number"))
Real = N(Symbol("Real"))
Integer = N(Symbol("Integer"))
String = N(Symbol("String"))
Boolean = Tagged(Integer, "boolean")
