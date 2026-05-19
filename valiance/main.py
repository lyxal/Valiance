from valiance.Environment import Environment
from valiance.Types import ExactRankParameter, MinimumRankParameter, NamedType, make_symbol

def named(
    name: str,
    *,
    generics: list | None = None,
    parameters: list | None = None,
    tags: set | None = None,
) -> NamedType:
    return NamedType(
        make_symbol(name),
        tags or set(),
        generics or [],
        parameters or [],
    )

def main():
    env = Environment()
    type_T = named("Number", parameters=[ExactRankParameter(3)])
    type_U = named("Number", parameters=[MinimumRankParameter(2)])
    print(env.assignable(type_T, type_U)) # Should print True since 3 >= 2

if __name__ == "__main__":
    main()
