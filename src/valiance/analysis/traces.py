"""Optional bounded provenance for immutable analyser checkpoints.

The graph belongs to one analysis request. Branch equality and executable
products deliberately exclude it; only validated function exits publish worlds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, eq=False)
class Trace:
    """A persistent ancestry edge, checkpoint, or inference refinement."""

    owner: TraceCollector
    parents: tuple[Trace, ...] = ()
    node: object = None
    before: object = None
    after: object = None
    refinement: tuple | None = None
    operation_id: tuple[str, int] | None = None

    def refine(self, old, new, *, method="refine_type", name=None):
        """Retain later inference substitutions for earlier cursor facts."""
        return self.owner.add((self,), refinement=(method, name, old, new))


def join_traces(*traces):
    """Keep all control-flow ancestry without creating overload alternatives."""
    parents = tuple(dict.fromkeys(trace for trace in traces if trace is not None))
    if not parents:
        return None
    if len(parents) == 1:
        return parents[0]
    return parents[0].owner.add(parents)


class TraceCollector:
    """Own a capped request-local graph and return-validated function exits."""

    def __init__(self, limit=20000):
        """Bound graph retention; exhaustion makes inspection unavailable."""
        self.limit = limit
        self.count = 0
        self.truncated = False
        self.functions = []
        self.visits = 0

    def add(self, parents=(), **facts):
        """Allocate one immutable graph node, or stop collecting at the limit."""
        if self.count >= self.limit:
            self.truncated = True
            self.functions.clear()
            return None
        self.count += 1
        return Trace(self, parents, **facts)

    def checkpoint(self, node, before, after):
        """Freeze branch facts without retaining accumulated executable bodies."""
        from dataclasses import replace

        parents = (after.trace,) if after.trace is not None else ()
        return self.add(
            parents,
            node=node,
            operation_id=(type(node).__name__, node.location.offset)
            if node.location
            else None,
            before=replace(before, trace=None, typed_body=()),
            after=replace(
                after,
                trace=None,
                typed_body=tuple(
                    item
                    for item in after.typed_body
                    if item.node is node
                    or (
                        node.location is not None
                        and type(item.node) is type(node)
                        and item.node.location == node.location
                    )
                ),
            ),
        )

    def finish_function(self, node, exits):
        """Publish exits only after signature filtering and return validation."""
        if not self.truncated:
            self.functions.append((node, tuple(exits)))

    def checkpoints(self, trace):
        """Walk surviving ancestry, applying downstream substitutions in order."""
        pending = [(trace, ())] if trace else []
        seen = set()
        while pending:
            current, refinements = pending.pop()
            key = (id(current), refinements)
            if key in seen:
                continue
            seen.add(key)
            self.visits += 1
            if self.visits > self.limit * 8:
                self.truncated = True
                return
            if current.refinement:
                refinements = (current.refinement, *refinements)
            if current.node is not None:
                before, after = current.before, current.after
                for method, name, old, new in refinements:
                    args = (old, new) if name is None else (name, old, new)
                    before = getattr(before, method)(*args)
                    after = getattr(after, method)(*args)
                yield current.node, before, after
            pending.extend((parent, refinements) for parent in current.parents)
