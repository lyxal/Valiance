"""Context objects supplied to independently registered lint rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import valiance.vtypes as T
from valiance.asts import ASTNode, MatchNode


@dataclass(frozen=True)
class BlockLintContext:
    """Information available while linting a lexical block."""

    nodes: tuple[ASTNode, ...]
    env: T.Environment


@dataclass(frozen=True)
class NodeLintContext:
    """Information available after one AST node has been analysed."""

    node: ASTNode
    branch: Any
    outputs: Any
    env: T.Environment


@dataclass(frozen=True)
class MatchLintContext:
    """Information available after match patterns pass semantic validation."""

    node: MatchNode
    branch: Any
    env: T.Environment
