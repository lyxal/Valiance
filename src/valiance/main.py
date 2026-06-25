from __future__ import annotations

import sys
from collections.abc import Sequence

from valiance.analysis import analyser
from valiance.asts import ASTNode, FieldAccessNode, FunctionNode, Symbol, pretty_ast
from valiance.asts.nodes import (
    ElementNode,
    FunctionParam,
    GetVariableNode,
    GetVariableNode,
    IfNode,
    NumberLiteralNode,
    StringLiteralNode,
)

HELP = """usage: valiance [command]

commands:
  analyse-demo   run a small built-in analyser demo
"""


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(HELP)
        return 0
    if args != ["analyse-demo"]:
        print(HELP)
        return 2

    program: list[ASTNode] = [
        IfNode(
            condition=(
                NumberLiteralNode(value="1"),
                NumberLiteralNode(value="2"),
                ElementNode(name=Symbol("==")),
            ),
            then_branch=(NumberLiteralNode(value="42"),),
            else_branch=(StringLiteralNode(value="3.14"),),
        ),
        ElementNode(name=Symbol("dup")),
    ]

    print(pretty_ast(analyser.analyse(program)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
