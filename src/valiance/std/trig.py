"""Python-backed trigonometry helpers for the Valiance standard library."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import valiance.types as T
from valiance.analysis.builtins import RuntimeContext
from valiance.stdlib_native import stdlib_element


def _number(value: float) -> Decimal:
    return Decimal(str(value))


@stdlib_element("pi", (), (T.Number,))
def _pi(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_number(math.pi),)


@stdlib_element("sin", (T.Number,), (T.Number,), param_names=("n",))
def _sin(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_number(math.sin(float(args[0]))),)


@stdlib_element("cos", (T.Number,), (T.Number,), param_names=("n",))
def _cos(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_number(math.cos(float(args[0]))),)


@stdlib_element("tan", (T.Number,), (T.Number,), param_names=("n",))
def _tan(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_number(math.tan(float(args[0]))),)


@stdlib_element("asin", (T.Number,), (T.Number,), param_names=("n",))
def _asin(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_number(math.asin(float(args[0]))),)


@stdlib_element("acos", (T.Number,), (T.Number,), param_names=("n",))
def _acos(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_number(math.acos(float(args[0]))),)


@stdlib_element("atan", (T.Number,), (T.Number,), param_names=("n",))
def _atan(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_number(math.atan(float(args[0]))),)
