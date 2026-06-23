from __future__ import annotations

import sys
from collections.abc import Sequence

from valiance.analysis import analyser
from valiance.asts import ASTNode, ElementNode, FunctionNode, Symbol

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
        FunctionNode(
            params=None,
            body=(
                ElementNode(name=Symbol("+")),
                ElementNode(name=Symbol("/")),
            ),
            returns=None,
        )
    ]
    print(analyser.analyse(program))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
