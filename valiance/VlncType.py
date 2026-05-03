from dataclasses import dataclass

@dataclass
class VType:
    pass

@dataclass
class VType_Number(VType):
    pass

@dataclass
class VType_String(VType):
    pass

@dataclass
class VType_List(VType):

    """
    The base list type in the Valiance programming language. If unspecialised, represents
    a rugged rank list.
    """

    base_type: VType
    rank: int

    def __post_init__(self):
        if self.rank < 0:
            # Indicative of a compile bug
            raise ValueError("VType_List rank cannot be less than 0")

@dataclass
class VType_List_Exact(VType_List):
    """
    Exact rank list type (T+)
    """
    pass

@dataclass
class VType_List_Minimum(VType_List):
    pass

@dataclass
class VType_Function(VType):
    inputs: list[VType] = []
    outputs: list[VType] = []
