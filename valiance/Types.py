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

@dataclass
class Tag:
    name: Symbol

    def __str__(self) -> str:
        return f"#{self.name}"

    def __hash__(self) -> int:
        return hash(self.name)

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
class ExactRankParameter(MinimumRankParameter):
    def __str__(self) -> str:
        return f"+{self.rank}"

@dataclass
class MinimumArrayParameter(TypeParameter):
    rank: int
    def __str__(self) -> str:
        return f">{self.rank}"

@dataclass
class ExactArrayParameter(MinimumArrayParameter):
    def __str__(self) -> str:
        return f"^{self.rank}"

@dataclass
class OptionalTypeParameter(TypeParameter):
    rank: int # Number of levels of nesting.
    def __post_init__(self):
        if self.rank <= 0:
            raise ValueError("OptionalTypeParameter rank must be greater than 0")

    def __str__(self) -> str:
            return f"?{self.rank}"

class Type:
    def __init__(self, tags: set[Tag]):
        self.parameters: list[TypeParameter] = []
        self.tags = tags

    def is_base(self):
        return not self.parameters

    def __eq__(self, other: object):
        if not isinstance(other, Type):
            return False
        return self.__class__ == other.__class__ and self.parameters == other.parameters and self.tags == other.tags

    def __str__(self) -> str:
        generics_str = ""
        parameters_str = ""
        if self.parameters:
            parameters_str = "".join(str(p) for p in self.parameters)
            parameters_str = f"{parameters_str}"
        return f"{self.__class__.__name__}{generics_str}{parameters_str}"

    def add_parameter(self, parameter: TypeParameter):
        if not self.parameters:
            self.parameters.append(parameter)
        else:
            if isinstance(parameter, OptionalTypeParameter) and isinstance(self.parameters[-1], OptionalTypeParameter):
                # (X?n)?m == X?(n+m)
                self.parameters[-1].rank = self.parameters[-1].rank + parameter.rank
            elif isinstance(parameter, ListRankParameter) and isinstance(self.parameters[-1], ListRankParameter):
                # Get the Lowest Common Ancestor
                mro_parameter = parameter.__class__.mro()
                mro_last = self.parameters[-1].__class__.mro()
                lca = None
                for c in mro_parameter:
                    if c in mro_last:
                        lca = c
                        break
                if lca is not None:
                    # Then, create a new parameter of the LCA type with the maximum rank of the two parameters
                    new_rank = parameter.rank + self.parameters[-1].rank
                    new_parameter = lca(new_rank)
                    self.parameters[-1] = new_parameter
                else:
                    self.parameters.append(parameter)
            else:
                self.parameters.append(parameter)

class NamedType(Type):
    __match_args__ = ("name",)
    def __init__(self, name: Symbol, tags: set[Tag], generics: list[Type]):
        super().__init__(tags)
        self.name = name
        self.generics = generics

    def __eq__(self, other: object):
        if not isinstance(other, NamedType): return False
        # Do not compare tags or parameters for equality since they are not part of the base type, but rather modifiers on the type. Only compare name and generics.
        return self.name == other.name and self.generics == other.generics

    def __hash__(self) -> int:
        return hash((self.name, tuple(self.generics)))

    def __str__(self) -> str:
        generics_str = ""
        parameters_str = ""
        if self.generics:
            generics_str = "[" + ", ".join(str(g) for g in self.generics) + "]"
        if self.parameters:
            parameters_str = "".join(str(p) for p in self.parameters)
            parameters_str = f"{parameters_str}"
        return f"{self.name}{generics_str}{parameters_str}"

class IntersectionType(Type):
    __match_args__ = ("types",)
    def __init__(self, types: list[Type]):
        super().__init__(set())
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

    def __eq__(self, other: object):
        if not isinstance(other, IntersectionType): return False
        return set(self.flatten().types) == set(other.flatten().types)

    def __hash__(self) -> int:
        return hash(frozenset(self.flatten().types))

class UnionType(Type):
    __match_args__ = ("types",)
    def __init__(self, types: list[Type]):
        super().__init__(set())
        self.types = types

    def __eq__(self, other: object):
        if not isinstance(other, UnionType): return False
        return set(self.types) == set(other.types)

    def __hash__(self) -> int:
        return hash(frozenset(self.types))

# Some pre-built named types that can be utilised in analysis
class FunctionType(NamedType):
    def __init__(self, input_type: Type, output_type: Type):
        super().__init__(make_symbol("Function"), set(), [input_type, output_type])

class SomeType(NamedType):
    # Like "Some[T]", not just "oh yeah, it's some type, we don't know which one"
    def __init__(self):
        super().__init__(make_symbol("Some"), set(), [])

class NoneType(NamedType):
    def __init__(self):
        super().__init__(make_symbol("None"), set(), [])
