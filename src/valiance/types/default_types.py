from valiance.symbols import Symbol
from valiance.types.builders import N, Tagged

Number = N(Symbol("Number"))
String = N(Symbol("String"))
Boolean = Tagged(Number, "boolean")
