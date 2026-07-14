"""Python-backed grid helpers for the Valiance standard library."""

from __future__ import annotations

from typing import Any

import valiance.types as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.elements.stdlib_native import stdlib_element


@stdlib_element(
    "allNeighbors",
    (T.ExactList(T.V("Cell"), 2), T.Boolean),
    (T.ExactList(T.V("Cell"), 3),),
    param_names=("board", "wrapping"),
    documentation=element_documentation(
        "Collect each cell together with its surrounding grid neighbors.",
        description=(
            "Each output position contains values in top-left, top-middle, "
            "top-right, middle-left, cell, middle-right, bottom-left, "
            "bottom-middle, bottom-right order.",
            "Without wrapping, positions outside the grid are omitted.",
        ),
        parameters=(
            ("board", "Rectangular two-dimensional input grid."),
            ("wrapping", "Whether opposite edges are adjacent."),
        ),
        returns="A grid whose cells are ordered neighborhood lists.",
        category="Grids",
    ),
)
def _all_neighbors(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return ordered neighborhoods for every cell in a rectangular grid."""
    raw_board, raw_wrapping = args
    board = [list(row) for row in raw_board]
    if not board:
        return ([],)

    width = len(board[0])
    if any(len(row) != width for row in board):
        raise RuntimeError("allNeighbors requires a rectangular grid")
    if width == 0:
        return ([[] for _ in board],)

    height = len(board)
    wrapping = bool(raw_wrapping)
    offsets = (
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 0),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    )

    neighborhoods: list[list[list[Any]]] = []
    for row_index in range(height):
        output_row: list[list[Any]] = []
        for column_index in range(width):
            cells: list[Any] = []
            for row_offset, column_offset in offsets:
                neighbor_row = row_index + row_offset
                neighbor_column = column_index + column_offset
                if wrapping:
                    neighbor_row %= height
                    neighbor_column %= width
                elif not (
                    0 <= neighbor_row < height
                    and 0 <= neighbor_column < width
                ):
                    continue
                cells.append(board[neighbor_row][neighbor_column])
            output_row.append(cells)
        neighborhoods.append(output_row)
    return (neighborhoods,)
