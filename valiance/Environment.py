from valiance.AST import ASTNode
from valiance.Overload import Overload
from valiance.Symbols import Name
from valiance.VlncType import VType, Vlnc_ExactRankList, Vlnc_ListType, Vlnc_MinimumRankList, Vlnc_NamedType, Vlnc_OptionalType


class Branch:
    def __init__(self, inputs: list[VType] | None):
        self.__stack: list[VType] = []
        self.__vars: dict[Name, VType] = {}
        self.__inputs = inputs
        self.__input_counter = 0 # Used for tracking the current input when doing input cycling.
        self.__inferred_inputs: list[VType] = [] # Used for branches that do not have any inputs

    def apply(self, overload_set: list[Overload]):
        ...

class Environment:
    def __init__(self, branches: list[Branch] | None = None):
        self.__branches = branches or []
        self.__traits: dict[Name, list[Name]] = {}

    def analyse(self, nodes: list[ASTNode]) -> tuple[list[Branch], list[ASTNode]]:
        ...


    def subtypes(self, parent: VType, child: VType, env: Environment) -> bool:
        if parent == child:
            # Exact match is always a subtype
            return True

        match (parent, child):
            case (Vlnc_NamedType(), Vlnc_NamedType()):
                if parent.name != child.name:
                    # If the names don't match, check if the child implements the parent as a trait
                    # If not, then the child cannot be a subtype of the parent
                    if not env.__traits.get(child.name, []):
                        return False
                # Check that all cooresponding generic parameters are in a subtype relationship
                if len(parent.generics) != len(child.generics):
                    return False # Differing number of generic parameters means they cannot be in a subtype relationship
                return all(self.subtypes(p, c, env) for p, c in zip(parent.generics, child.generics))
            case (Vlnc_ExactRankList(), Vlnc_ExactRankList()):
                # This case exists to make the next case impossible to have child be exact rank list
                # Ranks must match.
                return self.subtypes(parent.base, child.base, env) and parent.rank == child.rank
            case (Vlnc_ExactRankList(), Vlnc_ListType()):
                # U+n :> T*m IF U :> T AND n >= m
                return self.subtypes(parent.base, child.base, env) and parent.rank >= child.rank
            case (Vlnc_MinimumRankList(), Vlnc_ExactRankList()):
                # TODO: Evaluate whether this should ever be true.
                return False
            case (Vlnc_MinimumRankList(), Vlnc_ListType()):
                # U*n :> T*m IF U :> T AND n >= m
                return self.subtypes(parent.base, child.base, env) and parent.rank >= child.rank
            case (Vlnc_ListType(), Vlnc_ListType()):
                return self.subtypes(parent.base, child.base, env) and parent.rank >= child.rank
            case (_, Vlnc_OptionalType()):
                # T :> U? IF T :> U
                return self.subtypes(parent, child.generics[0], env)
            case _:
                # No other cases are valid, so return False
                return False
