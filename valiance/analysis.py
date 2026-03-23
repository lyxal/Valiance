import valiance.ast as ast
from valiance.types import String, Type, Number
import valiance.elements as elements


class Environment:
    stack: list[Type] = []
    variables: dict[str, Type] = {}
    elements = elements.ELEMENT_MAP

    def push(self, ty: Type):
        self.stack.append(ty)

    def pop(self):
        return self.stack.pop()

    def get_var(self, var_name: str) -> Type | None:
        return self.variables.get(var_name, None)

    def set_var(self, var_name: str, ty: Type):
        self.variables[var_name] = ty

    def get_element(self, symbol: str):
        return self.elements[symbol]

    def __str__(self) -> str:
        return f"Env<stack = {self.stack}, variables = {self.variables}>"


def analyse(program: list[ast.AST]):
    env = Environment()
    for node in program:
        match node:
            case ast.Number(_):
                env.push(Number())
            case ast.String(_):
                env.push(String())
            case ast.Variable(name):
                env.push(env.variables[name])
            case ast.AssignVariable(name, declared_type):
                if declared_type:
                    if env.get_var(name):
                        raise ValueError(
                            f"Variable named {name} already has a declared type"
                        )
                    if not type_compatible((stack_type := env.pop()), declared_type):
                        raise ValueError(
                            f"Item with {stack_type} cannot be assigned to {name}"
                        )
                    env.set_var(name, declared_type)
                else:
                    target_type = env.pop()
                    var_type = env.get_var(name)
                    if not var_type:
                        env.set_var(name, target_type)
                    elif not type_compatible(var_type, target_type):
                        raise ValueError(
                            f"Cannot assign {target_type} to {name} with type {env.get_var(name)}"
                        )
            case ast.Element(symbol):
                overloads = env.get_element(symbol)
                # Thing that selects the 'best' overload
                # Thing that applies the 'best' overload
            case _:
                raise NotImplementedError("Nuh uh")
    return env


def type_compatible(left: Type, right: Type) -> bool:
    # Simple check for now. No subtyping nor rank subsumption.
    return left == right
