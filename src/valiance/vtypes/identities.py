"""Deterministic identities shared by declaration analysis and bytecode lowering.

These names encode source declarations, not their ordinal in an overload table.
"""

from __future__ import annotations

from hashlib import sha256


def overload_implementation_id(overload: object) -> str:
    """Identify a callable alternative by its signature, scoped to its callee.

    Source declarations and intrinsics retain their explicit identities. Inferred
    alternatives use a deterministic signature fingerprint, never a table slot.
    """
    identity = getattr(overload, "implementation_id", None)
    if identity is not None:
        return identity
    from .builders import Fn, show

    # Results and inferred effects can be refined after recursive calls have
    # already been checked. Input alternatives identify the executable body.
    signature = show(Fn(overload.params, ()))
    constraints = repr(tuple((rule.name, show(rule.bound), rule.variance)
                             for rule in overload.generic_constraints))
    contract = signature + constraints + repr(overload.where_clause)
    return "callable:" + sha256(contract.encode("utf-8")).hexdigest()


def method_variant_id(overload: object, receiver_index: int = 0) -> str:
    """Identify a friendly method's input shape independently of its receiver name."""
    from .builders import Fn, show
    params = overload.params[:receiver_index] + overload.params[receiver_index + 1:]
    signature = show(Fn(params, ()))
    if receiver_index:
        signature = f"receiver:{receiver_index}:{signature}"
    return sha256(signature.encode("utf-8")).hexdigest()


def constructor_implementation_id(owner: str, location: object) -> str:
    """Identify an explicit constructor independent of other constructors."""
    line = getattr(location, "line", None)
    column = getattr(location, "column", None)
    if line is None or column is None:
        raise ValueError("explicit constructors need source locations")
    return f"constructor:{owner}:{line}:{column}"
