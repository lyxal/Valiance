from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from valiance.types import (
    C,
    Coll,
    Context,
    Fn,
    I,
    Kind,
    N,
    NoneType,
    Overload,
    Overloads,
    Tagged,
    Type,
    U,
    V,
    _combine_all,
    _match_specificity,
    _solve,
    apply_overload_to_stack,
    assignable,
    compatible,
    optional,
    resolve_overload_result,
)


HELP = """Commands:
  assignable <source> -> <target>
  compatible <argument> -> <parameter>
  solve <pattern> <- <actual>
  combine <type> , <type> [, <type> ...]
  overload <name> (<arg>, ...)       choose from a named overload set
  defover <name> (<param>, ...) -> <return>[, <return> ...]
  infer fn => <body> end            infer a simple function body
  overloads [name]                  show defined overloads
  impl <type> <trait>               declare that a type implements a trait
  trait <trait> <parent>             declare that a trait implements another trait
  traits                            show trait declarations
  clear                             clear the terminal
  show <type>
  help
  quit

Type syntax:
  Number, String, None, T
  T?, Number|String, Shape&Drawable, #sorted Number+, #!infinite Number+
  Number+, Number++ or Number+2
  Number*, Number~2, Number^, Number>2
  Function[Number, Number -> Number]

Examples:
  assignable Number^ -> Number+
  compatible Number+ -> Number
  solve T+ <- Number++
  compatible + -> Function[Number+, Number+ -> Number+]
  overload + (Number, Number)
  infer fn => + end
  infer fn => + 2 / end
  overloads +
  impl Circle Shape
  impl Circle Drawable
  compatible Circle -> Shape&Drawable
  clear
"""


@dataclass
class Parser:
    text: str
    pos: int = 0

    def parse(self) -> Type:
        result = self.parse_union()
        self.skip_ws()
        if self.pos != len(self.text):
            raise ValueError(f"unexpected text at {self.text[self.pos:]!r}")
        return result

    def parse_union(self) -> Type:
        parts = [self.parse_intersection()]
        while True:
            self.skip_ws()
            if not self.consume("|"):
                break
            parts.append(self.parse_intersection())
        return U(*parts) if len(parts) > 1 else parts[0]

    def parse_intersection(self) -> Type:
        parts = [self.parse_prefix()]
        while True:
            self.skip_ws()
            if not self.consume("&"):
                break
            parts.append(self.parse_prefix())
        if len(parts) == 1:
            return parts[0]
        return I(*parts)

    def parse_prefix(self) -> Type:
        self.skip_ws()
        tags: list[str] = []
        while self.peek() == "#":
            self.consume("#")
            absent = self.consume("!")
            name = self.read_name()
            tags.append(("!" if absent else "") + name)
            self.skip_ws()
        inner = self.parse_postfix()
        return Tagged(inner, *tags) if tags else inner

    def parse_postfix(self) -> Type:
        base = self.parse_atom()
        while True:
            self.skip_ws()
            if self.consume("?"):
                base = optional(base)
                continue
            marker = self.peek()
            if not marker or marker not in "+*~^>":
                break
            self.pos += 1
            rank_text = self.read_digits()
            rank = int(rank_text) if rank_text else 1
            kind = {
                "+": Coll.LIST_EXACT,
                "*": Coll.LIST_MIN,
                "~": Coll.LIST_RUGGED,
                "^": Coll.ARRAY_EXACT,
                ">": Coll.ARRAY_MIN,
            }[marker]
            if base.kind == Kind.COLLECTION and base.coll_kind == kind:
                base = C(kind, base.base, base.rank + rank)
            else:
                base = C(kind, base, rank)
        return base

    def parse_atom(self) -> Type:
        self.skip_ws()
        if self.consume("("):
            inner = self.parse_union()
            self.expect(")")
            return inner
        if self.match_word("Function"):
            self.expect("[")
            params, returns = self.parse_function_parts()
            self.expect("]")
            return Fn(params, returns)
        name = self.read_name()
        if not name:
            raise ValueError(f"expected type at {self.text[self.pos:]!r}")
        if name == "None":
            return NoneType()
        if len(name) == 1 and name.isupper() and name not in {"N"}:
            return V(name)
        return N(name)

    def parse_function_parts(self) -> tuple[tuple[Type, ...], tuple[Type, ...]]:
        before_arrow = self.read_until_top_level_arrow()
        params = parse_type_list(before_arrow)
        returns = parse_type_list(self.read_until("]"))
        return tuple(params), tuple(returns)

    def read_until_top_level_arrow(self) -> str:
        start = self.pos
        depth = 0
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch in "[(":
                depth += 1
            elif ch in "])":
                depth -= 1
            elif ch == "-" and self.pos + 1 < len(self.text) and self.text[self.pos + 1] == ">" and depth == 0:
                out = self.text[start:self.pos]
                self.pos += 2
                return out
            self.pos += 1
        raise ValueError("expected -> in Function[...]")

    def read_until(self, end: str) -> str:
        start = self.pos
        depth = 0
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch in "[(":
                depth += 1
            elif ch in "])":
                if depth == 0 and ch == end:
                    return self.text[start:self.pos]
                depth -= 1
            self.pos += 1
        return self.text[start:self.pos]

    def read_name(self) -> str:
        self.skip_ws()
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in "_."):
            self.pos += 1
        return self.text[start:self.pos]

    def read_digits(self) -> str:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        return self.text[start:self.pos]

    def match_word(self, word: str) -> bool:
        self.skip_ws()
        if self.text.startswith(word, self.pos):
            end = self.pos + len(word)
            if end == len(self.text) or not self.text[end].isalnum():
                self.pos = end
                return True
        return False

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def consume(self, text: str) -> bool:
        self.skip_ws()
        if self.text.startswith(text, self.pos):
            self.pos += len(text)
            return True
        return False

    def expect(self, text: str) -> None:
        if not self.consume(text):
            raise ValueError(f"expected {text!r} at {self.text[self.pos:]!r}")

    def peek(self) -> str:
        self.skip_ws()
        return self.text[self.pos] if self.pos < len(self.text) else ""


def parse_type(text: str) -> Type:
    special = DEFAULT_VALUES.get(text.strip())
    if special is not None:
        return special
    return Parser(text).parse()


def parse_type_list(text: str) -> list[Type]:
    parts = split_top_level(text, ",")
    return [parse_type(part) for part in parts if part.strip()]


def split_top_level(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for i, ch in enumerate(text):
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def split_arrow(line: str, arrow: str) -> tuple[str, str]:
    index = find_top_level_arrow(line, arrow)
    if index < 0:
        raise ValueError(f"expected {arrow}")
    return line[:index].strip(), line[index + len(arrow):].strip()


def find_top_level_arrow(line: str, arrow: str) -> int:
    depth = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif depth == 0 and line.startswith(arrow, i):
            return i
        i += 1
    return -1


def format_constraints(constraints: dict[str, list[Type]]) -> str:
    if not constraints:
        return "no constraints"
    lines = []
    for name, values in constraints.items():
        combined = _combine_all(values)
        rhs = ", ".join(str(v) for v in values)
        lines.append(f"{name}: [{rhs}] => {combined if combined else 'fail'}")
    return "\n".join(lines)


def default_overloads() -> dict[str, list[Overload]]:
    number = N("Number")
    string = N("String")
    return {
        "+": [
            Overload((number, number), (number,)),
            Overload((string, string), (string,)),
        ],
        "/": [
            Overload((number, number), (number,)),
        ],
        "length": [
            Overload((C(Coll.LIST_EXACT, V("T")),), (number,)),
        ],
        "head": [
            Overload((C(Coll.LIST_EXACT, V("T")),), (V("T"),)),
        ],
    }


OVERLOADS = default_overloads()
DEFAULT_VALUES = {
    "+": Overloads(*OVERLOADS["+"]),
    "/": Overloads(*OVERLOADS["/"]),
}
CTX = Context(trait_impls={"Integer": {"Number"}, "Real": {"Number"}})


@dataclass(frozen=True)
class InferState:
    """One REPL-only branch of toy function-body inference."""

    inputs: tuple[Type, ...]
    stack: tuple[Type, ...]


def command(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    name, _, rest = line.partition(" ")
    if name == "help":
        return HELP
    if name in {"quit", "exit"}:
        raise EOFError

    if name == "show":
        return str(parse_type(rest))

    if name in {"clear", "cls"}:
        os.system("cls" if os.name == "nt" else "clear")
        return ""

    if name == "assignable":
        left, right = split_arrow(rest, "->")
        return str(assignable(parse_type(left), parse_type(right), CTX))

    if name == "compatible":
        left, right = split_arrow(rest, "->")
        arg = parse_type(left)
        param = parse_type(right)
        return f"{compatible(arg, param, CTX)}\nspecificity: {_match_specificity(arg, param, CTX).name}"

    if name == "solve":
        left, right = split_arrow(rest, "<-")
        result = _solve(parse_type(left), parse_type(right))
        return "fail" if result is None else format_constraints(result)

    if name == "combine":
        values = parse_type_list(rest)
        result = _combine_all(values)
        return str(result) if result else "fail"

    if name == "infer":
        tokens = parse_infer_body(rest)
        result = infer_function(tokens)
        return str(result) if result else "could not infer"

    if name == "defover":
        over_name, params, returns = parse_overload_definition(rest)
        overload = Overload(tuple(parse_type_list(params)), tuple(parse_type_list(returns)))
        OVERLOADS.setdefault(over_name, []).append(overload)
        DEFAULT_VALUES[over_name] = Overloads(*OVERLOADS[over_name])
        return f"defined {over_name} overload #{len(OVERLOADS[over_name])}"

    if name == "impl":
        type_name, trait_name = parse_two_names(rest, "impl <type> <trait>")
        CTX.trait_impls.setdefault(type_name, set()).add(trait_name)
        return f"{type_name} implements {trait_name}"

    if name == "trait":
        trait_name, parent_name = parse_two_names(rest, "trait <trait> <parent>")
        CTX.trait_parents.setdefault(trait_name, set()).add(parent_name)
        return f"{trait_name} implements {parent_name}"

    if name == "traits":
        return format_traits()

    if name == "overloads":
        target = rest.strip()
        if target:
            return format_overload_set(target, OVERLOADS.get(target, []))
        if not OVERLOADS:
            return "no overloads defined"
        return "\n\n".join(format_overload_set(key, value) for key, value in sorted(OVERLOADS.items()))

    if name == "overload":
        over_name, args = parse_call(rest)
        overloads = OVERLOADS.get(over_name)
        if not overloads:
            return f"unknown overload set {over_name!r}"
        parsed_args = tuple(parse_type_list(args))
        chosen = resolve_overload_result(overloads, parsed_args, CTX)
        if chosen is None:
            return "no unique overload"
        return format_resolved_overload(chosen)

    raise ValueError(f"unknown command {name!r}")


def format_overload_set(name: str, overloads: list[Overload]) -> str:
    if not overloads:
        return f"{name}: no overloads defined"
    lines = [f"{name}:"]
    for index, overload in enumerate(overloads, start=1):
        params = ", ".join(str(param) for param in overload.params)
        returns = ", ".join(str(ret) for ret in overload.returns)
        lines.append(f"  {index}. ({params}) -> {returns}")
    return "\n".join(lines)


def format_resolved_overload(result) -> str:
    params = ", ".join(str(param) for param in result.overload.params)
    returns = ", ".join(str(ret) for ret in result.overload.returns)
    lines = [f"({params}) -> {returns}"]
    if result.substitution:
        lines.append("generics:")
        for name, value in sorted(result.substitution.items()):
            lines.append(f"  {name} = {value}")
    concrete_params = ", ".join(str(param) for param in result.params)
    concrete_returns = ", ".join(str(ret) for ret in result.returns)
    if result.substitution and (concrete_params != params or concrete_returns != returns):
        lines.append(f"instantiated: ({concrete_params}) -> {concrete_returns}")
    return "\n".join(lines)


def parse_two_names(rest: str, usage: str) -> tuple[str, str]:
    parts = shlex.split(rest)
    if len(parts) != 2:
        raise ValueError(f"expected {usage}")
    return parts[0], parts[1]


def format_traits() -> str:
    lines: list[str] = []
    for type_name, traits in sorted(CTX.trait_impls.items()):
        for trait_name in sorted(traits):
            lines.append(f"{type_name} implements {trait_name}")
    for trait_name, parents in sorted(CTX.trait_parents.items()):
        for parent_name in sorted(parents):
            lines.append(f"trait {trait_name} implements {parent_name}")
    return "\n".join(lines) if lines else "no trait declarations"


def parse_infer_body(rest: str) -> list[str]:
    rest = rest.strip()
    if not rest.startswith("fn"):
        raise ValueError("expected fn => ... end")
    rest = rest[2:].strip()
    if not rest.startswith("=>"):
        raise ValueError("expected fn => ... end")
    rest = rest[2:].strip()
    if not rest.endswith("end"):
        raise ValueError("expected fn => ... end")
    body = rest[:-3].strip()
    return shlex.split(body)


def infer_function(tokens: list[str]) -> Type | None:
    """Infer simple REPL token bodies using the library stack-application API."""
    states = {InferState((), ())}
    for token in tokens:
        literal = literal_type(token)
        next_states: set[InferState] = set()
        if literal is not None:
            for state in states:
                next_states.add(InferState(state.inputs, state.stack + (literal,)))
        elif token in OVERLOADS:
            for state in states:
                for overload in OVERLOADS[token]:
                    applied = apply_overload_to_stack(overload, state.stack, CTX, infer_missing=True)
                    if applied is not None:
                        next_states.add(InferState(state.inputs + applied.inputs, applied.stack))
        else:
            return None
        if not next_states:
            return None
        states = next_states

    inferred = sorted(
        (Overload(state.inputs, state.stack) for state in states),
        key=lambda overload: str(Fn(overload.params, overload.returns)),
    )
    if not inferred:
        return None
    if len(inferred) == 1:
        return Fn(inferred[0].params, inferred[0].returns)
    return Overloads(*inferred)


def literal_type(token: str) -> Type | None:
    """Infer the small set of literal tokens supported by the REPL."""
    if token == "None":
        return NoneType()
    if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
        return N("Number")
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return N("String")
    return None


def parse_overload_definition(rest: str) -> tuple[str, str, str]:
    signature, returns = split_arrow(rest, "->")
    name, args = parse_call(signature)
    return name, args, returns


def parse_call(rest: str) -> tuple[str, str]:
    rest = rest.strip()
    start = rest.find("(")
    end = rest.rfind(")")
    if start < 0 or end < start:
        raise ValueError("expected name(type, ...)")
    return rest[:start].strip(), rest[start + 1:end]


def main() -> None:
    print("Valiance type-system explorer. Type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            line = input("type> ")
            out = command(line)
            if out:
                print(out)
        except EOFError:
            print()
            return
        except Exception as exc:
            print(f"error: {exc}")


if __name__ == "__main__":
    main()
