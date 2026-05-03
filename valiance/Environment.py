from valiance.AST import ASTNode
from valiance.Overload import Overload
from valiance.VlncType import VType


class Branch:
    def __init__(self):
        self.__stack: list[VType] = []

    def apply(self, overload_set: list[Overload]):
        ...

class Environment:
    def __init__(self, branches: list[Branch] | None = None):
        self.__branches = branches or []

    def analyse(self, nodes: list[ASTNode]) -> tuple[list[Branch], list[ASTNode]]:
        ...
