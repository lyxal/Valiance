"""Non-executing, scope and stack aware language completion queries."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace

import valiance.vtypes as T
from valiance.analysis.analyser import Analyser
from valiance.asts import ElementNode
from valiance.parsing.lexer import TokenKind, lex_with_diagnostics
from valiance.parsing.unicode_identifiers import normalize_identifier
from valiance.sessions.events import CompletionItem
from valiance.source_queries import _overload_signature
from valiance.vtypes.symbols import Symbol


def completion_prefix(source: str, offset: int) -> tuple[int, str] | None:
    """Find an editable name fragment, excluding compiler strings and comments."""
    tokens, _ = lex_with_diagnostics(source)
    for token in tokens:
        width = len(token.raw if token.raw is not None else token.value)
        if token.kind == TokenKind.STRING:
            width += 2
            if token.offset < offset < token.offset + width:
                return None
    from valiance.source_queries import comment_ranges

    if any(
        start <= offset < end or offset == end == len(source)
        for start, end in comment_ranges(source)
    ):
        return None
    preceding = [token for token in tokens if token.offset < offset]
    if not preceding:
        return offset, ""
    token = preceding[-1]
    end = token.offset + len(token.raw if token.raw is not None else token.value)
    kinds = {
        TokenKind.IDENT,
        TokenKind.OP,
        TokenKind.DOLLAR,
        TokenKind.DOT,
        TokenKind.DOUBLE_COLON,
    }
    if token.kind not in kinds or end < offset:
        return offset, ""
    start = token.offset
    needs_name = token.kind in {TokenKind.DOT, TokenKind.DOUBLE_COLON}
    for previous in reversed(preceding[:-1]):
        previous_end = previous.offset + len(
            previous.raw if previous.raw is not None else previous.value
        )
        if previous_end != start:
            break
        if needs_name and previous.kind in {TokenKind.IDENT, TokenKind.OP}:
            needs_name = False
        elif not needs_name and previous.kind in {
            TokenKind.DOT,
            TokenKind.DOUBLE_COLON,
        }:
            needs_name = True
        elif not needs_name and previous.kind == TokenKind.DOLLAR:
            start = previous.offset
            break
        else:
            break
        start = previous.offset
    return start, normalize_identifier(source[start:offset])


def completion_end(source: str, offset: int) -> int:
    """Include the remainder of the compiler name token when accepting mid-name."""
    tokens, _ = lex_with_diagnostics(source)
    for token in tokens:
        if token.kind not in {TokenKind.IDENT, TokenKind.OP}:
            continue
        end = token.offset + len(token.raw if token.raw is not None else token.value)
        if token.offset < offset <= end:
            return end
    return offset


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class CompletionContext:
    """Private compiler checkpoint queried only through isolated analysis copies.

    The retained environment is read-only after publication. Queries copy it
    together with its branch so generic inference cannot mutate cursor facts.
    No VM, executable body or runtime value is retained.
    """

    environment: object
    branch: object
    element: ElementNode | None = None

    def candidates(self, prefix: str) -> tuple[CompletionItem, ...]:
        """Group callable names after the compiler's ordinary applicability checks."""
        env, branch = copy.deepcopy((self.environment, self.branch))
        analyser = Analyser()
        analyser.env = env
        branch = replace(branch, typed_body=(), trace=None, errors=())
        if branch.terminal:
            return ()
        items = []
        if prefix.startswith("$"):
            return tuple(
                CompletionItem("$" + str(name), "variable: " + T.show(typ))
                for name, typ in branch.variables.visible_items()
                if ("$" + str(name)).startswith(prefix)
            )
        names = set()
        current = env
        while current is not None:
            names.update(current.overloads)
            current = current.parent
        reference = prefix.startswith("\\")
        builtin = prefix.removeprefix("\\").startswith("*::")
        for symbol in sorted(names, key=str):
            name = ("*::" if builtin else "") + str(symbol)
            text = ("\\" if reference else "") + name
            if not text.startswith(prefix):
                continue
            lookup = Symbol(name) if builtin else symbol
            overloads = env.overloads_for(lookup)
            if reference:
                signatures = [_overload_signature(name, item) for item in overloads]
            else:
                analyser.diagnostics.clear()
                node = (
                    replace(self.element, name=lookup)
                    if self.element
                    else ElementNode(name=lookup)
                )
                result = analyser._element(node, branch)
                signatures = [
                    _overload_signature(name, typed.overload.overload)
                    for state in result
                    if not state.failed
                    for typed in state.typed_body
                    if getattr(typed, "overload", None) is not None
                ]
                if analyser.diagnostics:
                    continue
            if signatures:
                items.append(CompletionItem(text, "\n".join(dict.fromkeys(signatures))))
        return tuple(items)
