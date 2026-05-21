from valiance.Environment import Environment, ObjectDefinition, TraitDefinition
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

    # Base Traits
    env.add_trait(TraitDefinition(symbol("Comparable"), [], [], {}, {}))
    env.add_trait(TraitDefinition(symbol("Printable"), [], [], {}, {}))

    # Traits/Objects implementing others
    env.add_trait(TraitDefinition(symbol("Number"), [], [named("Comparable"), named("Printable")], {}, {}))
    env.add_object(ObjectDefinition(symbol("Integer"), {}, [], [named("Number"), named("Comparable"), named("Printable")], {}))
    env.add_object(ObjectDefinition(symbol("String"), {}, [], [named("Comparable"), named("Printable")], {}))

    # Generics
    env.add_object(ObjectDefinition(symbol("Box"), {}, [symbol("T")], [], {}))
    env.add_object(ObjectDefinition(symbol("ReadonlyBox"), {}, [symbol("T")], [], {}))

    return env
