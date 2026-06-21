from __future__ import annotations

from valiance.analysis import analyser
from valiance.asts import *


def main():
    program: list[ASTNode] = [
        FunctionNode(
            params=None,
            body=(
                ElementNode(name="+"),
                ElementNode(name="/"),
            ),
            returns=None,  # type: ignore
        )
    ]
    print(analyser.analyse(program))


if __name__ == "__main__":
    main()
