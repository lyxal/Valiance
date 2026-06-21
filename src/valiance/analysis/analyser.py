from valiance.asts import ASTNode, ElementNode, NumberLiteralNode, TypedNode
from valiance.analysis.builtins import default_environment
import valiance.types as T


def analyse(
    program: list[ASTNode], env: T.Environment | None = None
) -> list[TypedNode]:
    env = env or default_environment()
    stack = T.TypeStack()
    typed_program: list[TypedNode] = []
    for node in program:
        match node:
            case NumberLiteralNode(_):
                typed_program.append(TypedNode(node, T.Number))
                stack = stack.push(T.Number)
            case ElementNode(name):
                applied = env.apply(name, stack)
                if applied is None:
                    typed_program.append(TypedNode(node, None))
                    continue
                typed_program.append(TypedNode(node, _element_result_type(applied)))
                stack = applied.stack
            case _:
                typed_program.append(TypedNode(node, None))
    return typed_program


def _element_result_type(applied: T.StackApplication) -> T.Type | None:
    if len(applied.actual_returns) == 1:
        return applied.actual_returns[0]
    return None
