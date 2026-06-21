from valiance.asts import ASTNode, TypedNode


def analyse(program: list[ASTNode]) -> list[TypedNode]:
    """Analyse the given program and return a list of typed nodes."""
    return [TypedNode(node) for node in program]
