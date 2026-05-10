from valiance.Types import ExactRankParameter, IntersectionType, ListRankParameter, MinimumRankParameter, NamedType, OptionalTypeParameter, Symbol, Type, TypeParameter, UnionType


class Environment:
    def __init__(self):
        self.traits: dict[Symbol, list[Symbol]] = {} # Mapping of Trait/Object -> Implemented Traits

    def assignable(self, query: Type, to: Type):
        print("Base match:", self.base_assignable(query, to))
        print("Generics match:", self.generics_assignable(query.generics, to.generics))
        print("Parameters match:", self.params_assignable(query.parameters, to.parameters))
        return self.base_assignable(query, to) and self.generics_assignable(query.generics, to.generics) and self.params_assignable(query.parameters, to.parameters)

    def base_assignable(self, query: Type, to: Type) -> bool:
        print(f"Checking base assignability: {query} -> {to}")
        match (query, to):
            case (NamedType(qname), NamedType(tname)):
                if qname == tname: return True
                if to.name in self.traits.get(query.name, []): return True
            case (NamedType(), IntersectionType(types)):
                flattened_types = to.flatten().types
                for t in flattened_types:
                    if self.base_assignable(query, t):
                        return True
            case (NamedType(), UnionType(types)):
                for t in types:
                    if self.base_assignable(query, t):
                        return True
            case (_, _):
                return query == to
        return False

    def generics_assignable(self, query: list[Type], to: list[Type]) -> bool:
        if len(query) != len(to):
            return False
        for q, t in zip(query, to):
            if not self.assignable(q, t):
                return False
        return True

    def params_assignable(self, query: list[TypeParameter], to: list[TypeParameter]) -> bool:
        if query == to:
            return True

        for q, t in zip(query, to):
            match (q, t):
                # Check upstream rank subsumption.
                case (ExactRankParameter(q_rank), ExactRankParameter(t_rank)):
                    if q_rank != t_rank:
                        return False
                case (ExactRankParameter(q_rank), ListRankParameter(t_rank)):
                    if q_rank > t_rank:
                        return False
                case (MinimumRankParameter(q_rank), ListRankParameter(t_rank)):
                    if q_rank > t_rank:
                        return False

                # Immediately fail on downstream rank subsumption.
                case (ListRankParameter(_), ExactRankParameter(_)):
                    return False
                case (ListRankParameter(q_rank), MinimumRankParameter(t_rank)):
                    return False

                # Default list rank check
                case (ListRankParameter(q_rank), ListRankParameter(t_rank)):
                    if q_rank > t_rank:
                        return False

                # Optional rank - ?n can be assigned to ?m if n <= m. ?n will be wrapped
                # in (m - n) levels of Some[...] to match the target type.
                case (OptionalTypeParameter(q_rank), OptionalTypeParameter(t_rank)):
                    if q_rank > t_rank:
                        return False
                case (_, _):
                    if q != t:
                        return False
        return True
