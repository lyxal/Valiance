import enum
from copy import deepcopy
from dataclasses import dataclass

from valiance.Types import (
    ExactRankParameter,
    IntersectionType,
    ListRankParameter,
    MinimumRankParameter,
    NamedType,
    OptionalTypeParameter,
    Symbol,
    Type,
    TypeParameter,
    TypeVar,
    UnionType,
    make_symbol,
)

"""
A note on type notation:

<T#, t, p> represents a type T with tags #T, base type t, and parameters p.
[t, [g...]] represents an atomic type with name t and generic parameters g.
$t represents an atomic type with name t, and any number of generic parameters.
union[...] represents a union type with parameters ...
inter[...] represents an intersection type with parameters ...
"""


@dataclass
class TraitDefinition:
    name: Symbol
    generics: list[Symbol]  # List of generic parameters for this trait
    implements: list[
        NamedType
    ]  # List of traits this trait implements (i.e. supertraits)
    required_elements: dict[Symbol, Type]
    default_elements: dict[Symbol, Type]


@dataclass
class ObjectDefinition:
    name: Symbol
    fields: dict[Symbol, Type]
    generics: list[Symbol]  # List of generic parameters for this object
    implements: list[NamedType]  # List of traits this object implements
    elements: dict[Symbol, Type]


class TypeSpecificity(enum.Enum):
    MORE_SPECIFIC = 1
    LESS_SPECIFIC = 2
    EQUIVALENT = 3


class Environment:
    def __init__(self):
        self.traits: dict[Symbol, TraitDefinition] = {}
        self.objects: dict[Symbol, ObjectDefinition] = {}

        self.add_trait(TraitDefinition(make_symbol("Error"), [], [], {}, {}))
        self.add_object(
            ObjectDefinition(
                make_symbol("Result"),
                {},
                [make_symbol("T"), make_symbol("E")],
                [],
                {},
            )
        )

    def add_trait(self, trait_def: TraitDefinition):
        self.traits[trait_def.name] = trait_def

    def add_object(self, object_def: ObjectDefinition):
        self.objects[object_def.name] = object_def

    def add_implementation(self, object_name: Symbol, trait_name: NamedType):
        if object_name in self.objects:
            self.objects[object_name].implements.append(trait_name)
        elif object_name in self.traits:
            self.traits[object_name].implements.append(trait_name)
        else:
            raise ValueError(
                f"Neither object {object_name} nor trait {object_name} found in environment"  # noqa: E501
            )

    def _does_implement(self, implementer_name: Symbol, target_name: Symbol) -> bool:
        """Helper to recursively check if a trait or object implements a target trait."""  # noqa: E501
        if implementer_name in self.objects:
            for impl in self.objects[implementer_name].implements:
                if impl.name == target_name or self._does_implement(
                    impl.name, target_name
                ):
                    return True

        if implementer_name in self.traits:
            for impl in self.traits[implementer_name].implements:
                if impl.name == target_name or self._does_implement(
                    impl.name, target_name
                ):
                    return True

        return False

    # Utility functions to power the static analysis system.

    def assignable(self, _type: Type, to: Type) -> bool:
        """
        Type T <T#, t, p> is assignable to U <U#, u, q> if:
        1. #T is a superset of #U (T has all the tags of U) AND
        2. t <: u (T's base type is a subtype of U's base type) AND
        3. forall i, p[i] <: q[i] (T's parameters are subtypes of U's parameters)
        """

        if not (_type.tags.issuperset(to.tags) or to.tags == _type.tags):
            return False

        return self.base_assignable(_type, to) and self.parameters_assignable(
            _type.parameters, to.parameters
        )

    def base_assignable(self, t: Type, u: Type) -> bool:
        """
        1. t <: u if t = u
        2. [t, []] <: [u, []] if t impl u
        3. $t <: $u if t <: u AND forall i, g[i] <: h[i] where g and h are the generic parameters of t and u respectively.
        4. $t <: union[u, u2, ..., un] if exists i, t <: ui
        5. $t <: inter[u1, u2, ..., un] if forall i, t <: ui
        6. inter[t1, t2, ..., tn] <: $u if u in {t1, t2, ..., tn}
        7. inter[t1, t2, ..., tn] <: inter[u1, u2, ..., um] if forall i, inter[t1, t2, ..., tn] <: ui
        8. union[t1, t2, ..., tn] <: union[u1, u2, ..., um] if [t...] is a subset of [u...]
        9. union[t1, t2, ..., tn] <: union[u1, u2, ..., um] if forall i, ti <: union[u1, u2, ..., um]
        10. union[t1, t2, ..., tn] <: inter[u1, u2, ..., um] if forall (r, s) in [t...] full_outer_join [u...], r <: s
        11. inter[t1, t2, ..., tn] <: union[u1, u2, ..., um] if u <: t (to handle right-directional subtyping)
        """  # noqa: E501

        if t == u:
            return True
        if isinstance(t, NamedType) and isinstance(u, NamedType):
            if not t.generics and not u.generics:
                if t.name == u.name:
                    return True
                return self._does_implement(t.name, u.name)
            if t.generics and u.generics:
                # Return if t's name equals or implements u's name, and t's
                # generics are assignable to u's generics
                if t.name == u.name or self._does_implement(t.name, u.name):
                    return all(
                        self.assignable(tg, ug)
                        for tg, ug in zip(t.generics, u.generics, strict=False)
                    )
            return False

        if isinstance(t, NamedType) and isinstance(u, UnionType):
            return any(self.assignable(t, ui) for ui in u.types)

        if isinstance(t, NamedType) and isinstance(u, IntersectionType):
            return all(self.assignable(t, ui) for ui in u.types)

        if isinstance(t, IntersectionType) and isinstance(u, NamedType):
            return any(self.assignable(ti, u) for ti in t.types)

        if isinstance(t, IntersectionType) and isinstance(u, IntersectionType):
            return all(self.assignable(t, ui) for ui in u.types)

        if isinstance(t, UnionType) and isinstance(u, UnionType):
            return set(t.types).issubset(set(u.types)) or all(
                self.assignable(ti, u) for ti in t.types
            )

        if isinstance(t, UnionType) and isinstance(u, IntersectionType):
            return all(any(self.assignable(ti, ui) for ui in u.types) for ti in t.types)

        if isinstance(t, IntersectionType) and isinstance(u, UnionType):
            return self.assignable(u, t)

        return False

    def parameters_assignable(
        self, p: list[TypeParameter], q: list[TypeParameter]
    ) -> bool:
        """
        1. $p <: q$ if $p = q$
        2. $exact(n) <: min(m)$ if $n >= m$
        3. $exact(n) <: rugged(m)$ if $n >= m$
        4. $min(n) <: min(m)$ if $n >= m$
        5. $min(n) <: rugged(m)$ if $n >= m$
        6. $rugged(n) <: rugged(m)$ if $n >= m$
        7. $exact(n) <: exactarr(m)$ if $n = m$
        8. $exact(n) <: minarr(m)$ if $n >= m$
        9. $min(n) <: minarr(m)$ if $n >= m$
        10. $exactarr(n) <: min(m)$ if $n >= m$
        11. $exactarr(n) <: rugged(m)$ if $n >= m$
        12. $exactarr(n) <: minarr(m)$ if $n >= m$
        13. $minarr(n) <: min(m)$ if $n >= m$
        14. $minarr(n) <: rugged(m)$ if $n >= m$
        15. $minarr(n) <: minarr(m)$ if $n >= m$
        16. $opt(n) <: opt(m)$ if $n <= m$
        """

        P_LEN = len(p)
        Q_LEN = len(q)

        if P_LEN != Q_LEN:
            # May still be assignable if: if $|p| = |q| - 1$, and the last item
            # of $q$ is #opt("n").
            if P_LEN == Q_LEN - 1 and isinstance(q[-1], OptionalTypeParameter):
                return self.parameters_assignable(p, q[:-1])
            return False

        for pi, qi in zip(p, q, strict=False):
            if isinstance(pi, ExactRankParameter) and isinstance(
                qi, MinimumRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, ExactRankParameter) and isinstance(
                qi, ListRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, MinimumRankParameter) and isinstance(
                qi, MinimumRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, MinimumRankParameter) and isinstance(
                qi, ListRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, ListRankParameter) and isinstance(
                qi, ListRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, OptionalTypeParameter) and isinstance(
                qi, OptionalTypeParameter
            ):
                if pi.rank > qi.rank:
                    return False
            elif type(pi) is type(qi):
                return False

        return True

    def compatible(self, _type: Type, _with: Type) -> bool:
        """
        Type T <T#, t, p> is compatible with U <U#, u, q> if
        1. t <: u
        2. forall i : (p_i compat q_i)
        3. U# supset T#
        """

        if not self.base_assignable(_type, _with):
            return False

        if not _with.tags.issuperset(_type.tags):
            return False

        return self.parameters_compatible(_type.parameters, _with.parameters)

    def parameters_compatible(
        self, p: list[TypeParameter], q: list[TypeParameter]
    ) -> bool:
        """
        1. p compat q if p <: q
        2. exact(n) compat exact(m) if n > m (vectorises)
        3. exact(n) compat exactarr(m) if n > m (vectorises)
        4. min(n) compat exact(m) if n >= m (potentially vectorises)
        5. min(n) compat exactarr(m) if n >= m (potentially vectorises)
        6. exactarr(n) compat exact(m) if n > m (vectorises)
        7. exactarr(n) compat exactarr(m) if n > m (vectorises)
        8. minarr(n) compat exact(m) if n >= m (potentially vectorises)
        9. minarr(n) compat exactarr(m) if n >= m (potentially vectorises)
        """

        if self.parameters_assignable(p, q):
            return True  # Also covers the case of q having trailing optional.

        if len(p) != len(q):
            # Compatibility must have same number of parameters (ignoring
            # optional trailing)
            return False

        for pi, qi in zip(p, q, strict=False):
            if not self.parameters_assignable([pi], [qi]):
                return False
            elif isinstance(pi, ExactRankParameter) and isinstance(
                qi, ExactRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, ExactRankParameter) and isinstance(
                qi, ListRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, MinimumRankParameter) and isinstance(
                qi, ExactRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, MinimumRankParameter) and isinstance(
                qi, ListRankParameter
            ):
                if pi.rank < qi.rank:
                    return False
            elif type(pi) is type(qi):
                return False

        return True

    def more_specific_to(self, reference: Type, t: Type, u: Type) -> TypeSpecificity:
        """
        Type T<T#, t, p> is more specific to U<U#, u, q> with respect to reference type
        R<R#, r, r'> if

        1. |T# inter R#| > |U# inter X#| or, if equal,
        2. |t "traits shared with" r| > |u "traits shared with" r| or, if equal,
        3. forall i: (p_i >_r'_i q_i)

        Number of shared traits is defined as:

        1. Exact Match => infinity shared traits
        2. Exact Match after Generics => infinity shared traits
        3. Intersection => 2 < n < N shared traits, where n is the number of traits
        in the intersection that are implemented by r, and N is the total number
        of traits in the intersection.
        4. Single Trait Implementation => 1 shared trait
        5. Union => 0 shared traits
        """

        # 1. Compare number of shared tags
        reference_tags = reference.tags
        t_shared_tags = len(t.tags.intersection(reference_tags))
        u_shared_tags = len(u.tags.intersection(reference_tags))

        if t_shared_tags > u_shared_tags:
            return TypeSpecificity.MORE_SPECIFIC
        elif t_shared_tags < u_shared_tags:
            return TypeSpecificity.LESS_SPECIFIC

        # 2. Compare number of shared traits
        def count_shared_traits(type_: Type) -> float:
            if isinstance(type_, NamedType) and isinstance(reference, NamedType):
                if type_.name == reference.name:
                    return float("inf")
                elif self._does_implement(type_.name, reference.name):
                    return float("inf")
                else:
                    return sum(
                        1
                        for trait in self.traits.values()
                        if self._does_implement(type_.name, trait.name)
                        and self._does_implement(trait.name, reference.name)
                    )
            elif isinstance(type_, IntersectionType):
                return sum(count_shared_traits(ti) for ti in type_.types)
            else:
                return 0

        t_shared_traits = count_shared_traits(t)
        u_shared_traits = count_shared_traits(u)

        if t_shared_traits > u_shared_traits:
            return TypeSpecificity.MORE_SPECIFIC
        elif t_shared_traits < u_shared_traits:
            return TypeSpecificity.LESS_SPECIFIC
        else:
            # 3. Compare parameters with respect to reference parameters
            return self.parameters_more_specific_to(
                reference.parameters, t.parameters, u.parameters
            )

    def parameters_more_specific_to(
        self,
        reference: list[TypeParameter],
        p: list[TypeParameter],
        q: list[TypeParameter],
    ) -> TypeSpecificity:
        """
        If $r_i$ is a rank type:

        $
        exact(...) >_r_i exactarr(...) >_r_i min(...) >_r_i minarr(...) >_r_i rugged(...)
        $

        Lower rank for min/rugged rank/optional type is more specific.

        Trailing optional is less specific to a guaranteed type than a non-optional type.

        Note that pi and qi are always compatible, as specifity is only checked when multiple overloads
        are deemed compatible. Thus, pi and qi are by definition compatible.
        """  # noqa: E501

        for pi, qi, ri in zip(p[::-1], q[::-1], reference[::-1], strict=False):
            if isinstance(ri, ListRankParameter):
                # These asserts are for the type checker. They're guaranteed to
                # be ListRankParameters since otherwise they wouldn't be
                # compatible.
                assert isinstance(pi, ListRankParameter)
                assert isinstance(qi, ListRankParameter)
                ORDER = [
                    ExactRankParameter,
                    MinimumRankParameter,
                    ListRankParameter,
                ]
                pi_index = next(i for i, cls in enumerate(ORDER) if isinstance(pi, cls))
                qi_index = next(i for i, cls in enumerate(ORDER) if isinstance(qi, cls))
                if pi_index < qi_index:
                    return TypeSpecificity.MORE_SPECIFIC
                elif pi_index > qi_index:
                    return TypeSpecificity.LESS_SPECIFIC
                else:
                    # If same rank type, and both are Exact, and one rank
                    # exactly matches the reference rank, then that one is more
                    # specific. Otherwise, we consider them equally specific and
                    # move on to the next parameter.
                    if isinstance(pi, ExactRankParameter) and isinstance(
                        qi, ExactRankParameter
                    ):
                        if pi.rank == ri.rank and qi.rank != ri.rank:
                            return TypeSpecificity.MORE_SPECIFIC
                        elif qi.rank == ri.rank and pi.rank != ri.rank:
                            return TypeSpecificity.LESS_SPECIFIC
            elif isinstance(ri, OptionalTypeParameter):
                # These asserts are for the type checker. They're guaranteed to
                # be OptionalTypeParameters since otherwise they wouldn't be
                # compatible.
                assert isinstance(pi, OptionalTypeParameter)
                assert isinstance(qi, OptionalTypeParameter)
                if pi.rank < qi.rank:
                    return TypeSpecificity.MORE_SPECIFIC
                elif pi.rank > qi.rank:
                    return TypeSpecificity.LESS_SPECIFIC
        # If we get here, then all parameters are equally specific. However, if
        # p has trailing optional parameters and q does not, then q is more
        # specific.
        if len(p) < len(q) and isinstance(q[-1], OptionalTypeParameter):
            return TypeSpecificity.LESS_SPECIFIC
        elif len(p) > len(q) and isinstance(p[-1], OptionalTypeParameter):
            return TypeSpecificity.MORE_SPECIFIC
        else:
            return TypeSpecificity.EQUIVALENT
            # This will cause a compile error upstream. But it is not the job of
            # this function to error.

    def solve_type_var(
        self,
        parameter_type: Type,  # e.g. Dict[T, U] - may contain other type vars too
        input_type: Type,  # e.g. Dict[str, int]
    ) -> dict[Symbol, Type]:
        """
        Given a parameter type containing a type variable and a concrete input type,
        determine what the specified type variable resolves to.

        Example: solve_type_var(Dict[K, V], Dict[str, int]) -> {K: str, V: int}
        """

        output: dict[Symbol, Type] = {}

        if not isinstance(parameter_type, NamedType):
            return {}

        if isinstance(parameter_type, TypeVar):
            output[parameter_type.name] = deepcopy(input_type)
            output[parameter_type.name].parameters = []
            for param in self.solve_parameters(
                parameter_type.parameters, input_type.parameters
            ):
                output[parameter_type.name].add_parameter(param)

        if isinstance(input_type, NamedType):
            for parameter_generic, input_generic in zip(
                parameter_type.generics, input_type.generics, strict=False
            ):
                output.update(self.solve_type_var(parameter_generic, input_generic))

        return output

    def solve_parameters(
        self,
        parameter_parameters: list[TypeParameter],
        input_parameters: list[TypeParameter],
    ) -> list[TypeParameter]:
        """
        `solve_type_parameter(parameter, input)` is defined as:

        1. If `parameter` is $opt(n)$ and `input` is $opt(m)$, return $opt(m - n)$
        2. Else If `parameter` is $exact(n) or exactarr(n)$, and `input` has rank $m$,
         return `input.rank_type(m - n)`. This is the rank that vectorisation will occur at.
        3. Else If `parameter` is $opt(n)$, return $opt(n)$. Trailing $opt(n)$ in the full
           $p$ list will be stripped.
        4. Else, return `parameter` - min rank/rugged rank must unify @ parameter rank,
           not input rank.
        """  # noqa: E501

        output: list[TypeParameter] = []

        for parameter, input_ in zip(
            parameter_parameters, input_parameters, strict=False
        ):
            if isinstance(parameter, OptionalTypeParameter) and isinstance(
                input_, OptionalTypeParameter
            ):
                output.append(OptionalTypeParameter(input_.rank - parameter.rank))
            elif isinstance(parameter, ListRankParameter) and isinstance(
                input_, ListRankParameter
            ):
                rank_diff = input_.rank - parameter.rank
                if isinstance(parameter, ExactRankParameter):
                    output.append(ExactRankParameter(rank_diff))
                else:
                    output.append(ListRankParameter(rank_diff))
            elif isinstance(parameter, OptionalTypeParameter):
                output.append(parameter)
            else:
                output.append(parameter)

        return output

    def combine(self, left: Type, right: Type) -> Type:
        """
        `combine(T, U)` returns the most general type between two generic solutions.

        Note that if a case is not listed below, the two types cannot be combined. Also note that `combine` is commutative.

        1. $tbp("", "t", "p"), tbp("", "t", "p") -> tbp("", "t", "p")$
        2. $tbp("", "t", min(n)), tbp("", "t", min(m)) -> tbp("", "t", min("min"(n, m)))$
        3. $tbp("", "t", minarr(n)), tbp("", "t", minarr(m)) -> tbp("", "t", minarr("min"(n, m)))$
        4. $tbp("", "t", min(n)), tbp("", "t", exact(m)) -> tbp("", "t", min("min"(n, m)))$
        5. $tbp("", "t", minarr(n)), tbp("", "t", exactarr(m)) -> tbp("", "t", minarr("min"(n, m)))$
        6. $tbp("", "t", exact(n)), tbp("", "t", exactarr(m)) -> tbp("", "t", exact("min"(n, m)))$
        7. $tbp("", "t", min(n)), tbp("", "t", minarr(m)) -> tbp("", "t", min("min"(n, m)))$
        8. $tbp("", "t", exact(n)), tbp("", "t", "") -> tbp("", "t", exact(n))$
        9. $tbp("", "t", min(n)), tbp("", "t", "") -> tbp("", "t", min(n))$
        10. $tbp("", "t", rugged(n)), tbp("", "t", "") -> tbp("", "t", rugged(n))$
        11. $tbp("", "t", exactarr(n)), tbp("", "t", "") -> tbp("", "t", exactarr(n))$
        12. $tbp("", "t", minarr(n)), tbp("", "t", "") -> tbp("", "t", minarr(n))$
        """  # noqa: E501

        # base = lowest common ancestor of left.base and right.base, relative to this
        # env's trait set. Note that left.base may implement right.base

        ...
