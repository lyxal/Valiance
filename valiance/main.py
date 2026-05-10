from valiance.Environment import Environment
from valiance.Types import ExactRankParameter, MinimumRankParameter, NamedType, make_symbol

def main():
    env = Environment()
    print(env.assignable(NamedType(make_symbol("B"), [], [ExactRankParameter(3)]), NamedType(make_symbol("B"), [], [MinimumRankParameter(4)])))

if __name__ == "__main__":
    main()
