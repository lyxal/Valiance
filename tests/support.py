from valiance.Environment import Environment
from valiance.Types import NamedType, Tag, make_symbol


def symbol(name: str):
    return make_symbol(name)


def tag(name: str) -> Tag:
    return Tag(symbol(name))


def named(
    name: str,
    *,
    generics: list | None = None,
    parameters: list | None = None,
    tags: set | None = None,
) -> NamedType:
    type_ = NamedType(
        symbol(name),
        tags or set(),
        generics or [],
    )
    for parameter in parameters or []:
        type_.add_parameter(parameter)
    return type_


def build_environment() -> Environment:
    env = Environment()
    env.add_trait(symbol("Number"), [symbol("Comparable"), symbol("Printable")])
    env.add_trait(symbol("Integer"), [symbol("Number"), symbol("Comparable"), symbol("Printable")])
    env.add_trait(symbol("String"), [symbol("Comparable"), symbol("Printable")])
    env.add_trait(symbol("Box"), [])
    env.add_trait(symbol("ReadonlyBox"), [])
    return env
