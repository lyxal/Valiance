from abc import ABC
from dataclasses import dataclass

from valiance.types import Type


@dataclass
class AST(ABC):
    pass


@dataclass
class Element(AST):
    name: str


@dataclass
class Variable(AST):
    # Push variable's value to stack
    name: str


@dataclass
class AssignVariable(AST):
    name: str
    declared_type: Type | None


@dataclass
class Number(AST):
    value: str


@dataclass
class String(AST):
    value: str  # I used the str to implement the str
