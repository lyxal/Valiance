from valiance.analysis import analyse
import valiance.ast as ast
from valiance.types import Number


def main():
    program: list[ast.AST] = [
        ast.Number("1"),
        ast.AssignVariable("x", Number()),
        ast.String("This is a string"),
        ast.AssignVariable("x", None),
    ]
    print(analyse(program))


if __name__ == "__main__":
    main()
