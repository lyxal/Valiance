from abc import ABC
from dataclasses import dataclass


@dataclass
class Type(ABC):
    pass


@dataclass
class Number(Type):
    def __hash__(self):
        return hash("number")


@dataclass
class String(Type):
    def __hash__(self):
        return hash("string")


@dataclass
class FunctionType(Type):
    parameters: tuple[Type]
    returns: tuple[Type]
