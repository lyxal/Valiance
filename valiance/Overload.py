from dataclasses import dataclass

from valiance.VlncType import VType


@dataclass
class Overload:
    inputs: list[VType]
    outputs: list[VType]
