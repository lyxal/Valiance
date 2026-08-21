"""Shared effect accounting for compiler-modelled ownership release sites."""

from __future__ import annotations

from enum import Enum, auto
from typing import Iterable

from valiance import vtypes as T


class OwnershipDisposition(Enum):
    """How one analysed occurrence leaves its current ownership location."""

    BORROWED = auto()
    RELEASED = auto()
    TRANSFERRED = auto()
    RETAINED = auto()
    UNKNOWN = auto()


def release_effects(
    env: T.Environment,
    typ: T.Type,
    disposition: OwnershipDisposition = OwnershipDisposition.RELEASED,
) -> frozenset[T.ElementTag]:
    """Return destructor effects caused by one proven or possible final release."""
    if disposition in {
        OwnershipDisposition.BORROWED,
        OwnershipDisposition.TRANSFERRED,
        OwnershipDisposition.RETAINED,
    }:
        return frozenset()
    return frozenset(env.destructor_effects(typ))


def released_types_effects(
    env: T.Environment,
    types: Iterable[T.Type],
) -> frozenset[T.ElementTag]:
    """Combine destructor effects for a deterministic set of released values."""
    return frozenset(
        tag
        for typ in types
        for tag in release_effects(env, typ)
    )


def scope_exit_effects(
    env: T.Environment,
    owned_items: Iterable[tuple[object, T.Type]],
    transferred_names: Iterable[object] = (),
) -> frozenset[T.ElementTag]:
    """Return cleanup effects after provenance-backed ownership transfers."""
    transferred = set(transferred_names)
    return released_types_effects(
        env,
        (typ for name, typ in owned_items if name not in transferred),
    )
