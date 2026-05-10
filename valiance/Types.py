from dataclasses import dataclass

"""
The idea here is to have a normalised type representation based on:

1. The atomic base type (e.g. Number, String, etc.)
2. Any generic type arguments (e.g. Box[Number] would have Number as a generic argument)
3. Any parameters (e.g Number+3 would have a parameter of ExactRankParameter(3))
"""

@dataclass
class Symbol:
    base: str
    parts: list[Symbol]

    def __hash__(self) -> int:
        # Hash this type based on base and parts so that it can be stored in dicts
        return hash((self.base, tuple(self.parts)))

    def __eq__(self, other: object):
        if not isinstance(other, Symbol): return False
        return self.base == other.base and all(p1 == p2 for p1, p2 in zip(self.parts, other.parts))

    def __str__(self) -> str:
        if self.parts:
            return ".".join(str(part) for part in self.parts) + "." + self.base
        else:
            return self.base

def make_symbol(name: str) -> Symbol:
    parts = name.split(".")
    return Symbol(parts[-1], [Symbol(part, []) for part in parts[:-1]])

class TypeParameter:
    def __init__(self): ...

@dataclass
class ListRankParameter(TypeParameter):
    rank: int
    def __str__(self) -> str:
        return f"~{self.rank}"

@dataclass
class MinimumRankParameter(ListRankParameter):
    def __str__(self) -> str:
        return f"*{self.rank}"

@dataclass
class ExactRankParameter(ListRankParameter):
    def __str__(self) -> str:
        return f"+{self.rank}"

@dataclass
class OptionalTypeParameter(TypeParameter):
    rank: int # Number of levels of nesting.
    def __post_init__(self):
        if self.rank <= 0:
            raise ValueError("OptionalTypeParameter rank must be greater than 0")

    def __str__(self) -> str:
            return f"?{self.rank}"

class Type:
    def __init__(self, generics: list[Type], parameters: list[TypeParameter]):
        self.base = self.__class__
        self.generics = generics
        self.parameters = parameters

    def is_base(self):
        return not (self.generics or self.parameters)

    def __eq__(self, other: object):
        if not isinstance(other, Type):
            return False
        return self.generics == other.generics and self.parameters == other.parameters

    def __str__(self) -> str:
        generics_str = ""
        parameters_str = ""
        if self.generics:
            generics_str = ", ".join(str(g) for g in self.generics)
            generics_str = f"[{generics_str}]"
        if self.parameters:
            parameters_str = "".join(str(p) for p in self.parameters)
            parameters_str = f"{parameters_str}"
        return f"{self.base.__name__}{generics_str}{parameters_str}"

class NamedType(Type):
    __match_args__ = ("name",)
    def __init__(self, name: Symbol, generics: list[Type], parameters: list[TypeParameter]):
        super().__init__(generics, parameters)
        self.name = name

class IntersectionType(Type):
    __match_args__ = ("types",)
    def __init__(self, types: list[Type]):
        super().__init__(self, [], [])
        self.types = types

    def flatten(self):
        # If an intersection contains other intersections, flatten them into a single self.types list
        flattened_types = []
        for t in self.types:
            if isinstance(t, IntersectionType):
                flattened_types.extend(t.flatten().types)
            else:
                flattened_types.append(t)
        return IntersectionType(flattened_types)

class UnionType(Type):
    __match_args__ = ("types",)
    def __init__(self, types: list[Type]):
        super().__init__(self, [], [])
        self.types = types
