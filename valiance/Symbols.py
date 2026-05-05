from dataclasses import dataclass


@dataclass
class Name:
    """
    Representative of a name, or a chained access
    """
    parts: tuple[str, ...]

    def __str__(self):
        return ".".join(self.parts)

    def __eq__(self, other):
        if not isinstance(other, Name):
            return NotImplemented
        return self.parts == other.parts

    @property
    def is_simple(self) -> bool:
        return len(self.parts) == 1

    @property
    def head(self) -> str:
        return self.parts[0]

    @property
    def tail(self) -> Name | None:
        if len(self.parts) > 1:
            return Name(self.parts[1:])
        else:
            return None
