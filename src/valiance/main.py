from __future__ import annotations

from valiance.analysis import analyser
from valiance.asts import ASTNode, NumberLiteralNode, ElementNode


def main():
    program: list[ASTNode] = [
        NumberLiteralNode("67"),
        NumberLiteralNode("42"),
        ElementNode("+"),
    ]
    print(analyser.analyse(program))


if __name__ == "__main__":
    main()
