from valiance.Types import ExactRankParameter, IntersectionType, ListRankParameter, MinimumRankParameter, NamedType, OptionalTypeParameter, Symbol, Type, TypeParameter, UnionType

"""
A note on type notation:

<T#, t, p> represents a type T with tags #T, base type t, and parameters p.
[t, [g...]] represents an atomic type with name t and generic parameters g.
$t represents an atomic type with name t, and any number of generic parameters.
union[...] represents a union type with parameters ...
inter[...] represents an intersection type with parameters ...
"""

class Environment:
    def __init__(self):
        self.traits: dict[Symbol, list[Symbol]] = {} # Mapping of Trait/Object -> Implemented Traits

    def add_trait(self, trait: Symbol, implements: list[Symbol]):
        self.traits[trait] = implements

    def assignable(self, _type: Type, to: Type) -> bool:
        """
        Type T <T#, t, p> is assignable to U <U#, u, q> if:
        1. #T is a superset of #U (T has all the tags of U) AND
        2. t <: u (T's base type is a subtype of U's base type) AND
        3. forall i, p[i] <: q[i] (T's parameters are subtypes of U's parameters)
        """

        if not (_type.tags.issuperset(to.tags) or to.tags == _type.tags):
            return False

        base = self.base_assignable(_type, to)
        params = self.parameters_assignable(_type.parameters, to.parameters)

        return self.base_assignable(_type, to) and self.parameters_assignable(_type.parameters, to.parameters)

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
        """

        if t == u: return True
        if isinstance(t, NamedType) and isinstance(u, NamedType):
            if not t.generics and not u.generics:
                if t.name == u.name:
                    return True
                return t.name in self.traits and u.name in self.traits[t.name]
            if t.generics and u.generics:
                # Return if t's name equals or implements u's name, and t's generics are assignable to u's generics
                if t.name == u.name or (t.name in self.traits and u.name in self.traits[t.name]):
                    return all(self.assignable(tg, ug) for tg, ug in zip(t.generics, u.generics))
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
            return set(t.types).issubset(set(u.types)) or all(self.assignable(ti, u) for ti in t.types)

        if isinstance(t, UnionType) and isinstance(u, IntersectionType):
            return all(any(self.assignable(ti, ui) for ui in u.types) for ti in t.types)

        if isinstance(t, IntersectionType) and isinstance(u, UnionType):
            return self.assignable(u, t)

        return False

    def parameters_assignable(self, p: list[TypeParameter], q: list[TypeParameter]) -> bool:
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
            # May still be assignable if:
            # if $|p| = |q| - 1$, and the last item of $q$ is #opt("n").
            if P_LEN == Q_LEN - 1 and isinstance(q[-1], OptionalTypeParameter):
                return self.parameters_assignable(p, q[:-1])
            return False

        for pi, qi in zip(p, q):
            if isinstance(pi, ExactRankParameter) and isinstance(qi, MinimumRankParameter):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, ExactRankParameter) and isinstance(qi, ListRankParameter):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, MinimumRankParameter) and isinstance(qi, MinimumRankParameter):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, MinimumRankParameter) and isinstance(qi, ListRankParameter):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, ListRankParameter) and isinstance(qi, ListRankParameter):
                if pi.rank < qi.rank:
                    return False
            elif isinstance(pi, OptionalTypeParameter) and isinstance(qi, OptionalTypeParameter):
                if pi.rank > qi.rank:
                    return False
            elif type(pi) != type(qi):
                return False

        return True
