from dataclasses import dataclass

from valiance.Symbols import Name

@dataclass
class Tag:
    name: Name

@dataclass
class ConstructedTag(Tag): pass

@dataclass
class ComputedTag(Tag): pass

@dataclass
class Vlnc_Type:
    pass

"""
Types:

Named type -> Specialised Number/String/Function types
List types -> Exact Rank :> Min Rank :> Rugged Rank
Optional type
Union type
Intersection type
Result type -> Some type
"""

@dataclass
class Vlnc_NamedType(Vlnc_Type):
    name: Name
    generics: list[Vlnc_Type]
    is_trait: bool = False

@dataclass
class Vlnc_NumberType(Vlnc_NamedType):
    name = Name(("Number",))
    generics = []

@dataclass
class Vlnc_StringType(Vlnc_NamedType):
    name = Name(("String",))
    generics = []

@dataclass
class Vlnc_SomeType(Vlnc_NamedType):
    name = Name(("Some",))
    generics: list[Vlnc_Type]

    def __post_init__(self):
        if len(self.generics) != 1:
            raise ValueError("Some type must have exactly one generic parameter")

@dataclass
class Vlnc_NoneType(Vlnc_NamedType):
    name = Name(("None",))
    generics = []

@dataclass
class Vlnc_ErrorTrait(Vlnc_NamedType):
    name = Name(("Error",))
    generics = []
    is_trait = True

@dataclass
class Vlnc_ResultType(Vlnc_NamedType):
    name = Name(("Result",))
    generics: list[Vlnc_Type]

    def __post_init__(self):
        if len(self.generics) != 2:
            raise ValueError("Result type must have exactly two generic parameters")
        # Validation that the Error value implements the Error trait has to be done
        # at ResultType creation site - it very much depends on the Environment trait
        # dictionary.

@dataclass
class Vlnc_FunctionType(Vlnc_Type):
    inputs: list[Vlnc_Type]
    outputs: list[Vlnc_Type]

    def __post_init__(self):
        self.generics = self.inputs + self.outputs

@dataclass
class Vlnc_ListType(Vlnc_Type):
    # Without specialisation, this represents a rugged rank list
    base: Vlnc_Type
    rank: int

    def __post_init__(self):
        if self.rank < 0:
            raise ValueError("Rank cannot be negative")

@dataclass
class Vlnc_MinimumRankList(Vlnc_ListType): pass

@dataclass
class Vlnc_ExactRankList(Vlnc_ListType): pass

@dataclass
class Vlnc_OptionalType(Vlnc_NamedType):
    name = Name(("Optional",))

    def __post_init__(self):
        if len(self.generics) != 1:
            raise ValueError("Optional type must have exactly one generic parameter")

@dataclass
class Vlnc_UnionType(Vlnc_Type):
    left: Vlnc_Type
    right: Vlnc_Type

    # Simplification of union types must be done at the point of creation.
    # Typically, avoid manually instantiating this class, and instead use a helper
    # function that applies simplification rules.

@dataclass
class Vlnc_IntersectionType(Vlnc_Type):
    left: Vlnc_Type
    right: Vlnc_Type
