"""Systematically replay cooperative task quanta in alternative schedules.

Each scheduling prefix is reconstructed from a fresh Scheduler.  A separate
read/write state machine predicts lost updates, completion and cancellation;
that makes both serial and interleaved results meaningful rather than assuming
all legal schedules must produce the same shared-memory result.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from valiance.runtime.concurrency import CancelledFault, Scheduler, TaskState, TaskYield


@dataclass(frozen=True, slots=True)
class ScheduleReport:
    tasks: int
    cancellation_at: int | None
    schedules: int
    explored_prefixes: int
    final_values: frozenset[int]


def _runner(identifier: int, shared: list[int], finished: list[int]):
    """Perform a split read-modify-write, exposing three scheduler quanta."""
    try:
        old = shared[0]
        yield TaskYield("after-read")
        shared[0] = old + 1
        yield TaskYield("after-write")
        return (identifier,)
    finally:
        finished.append(identifier)


def _replay(prefix: tuple[int, ...], tasks: int, cancellation_at: int | None):
    scheduler = Scheduler()
    shared = [0]
    finished: list[int] = []
    handles = [
        scheduler.root_scope.spawn(
            lambda identifier=identifier: _runner(identifier, shared, finished)
        )
        for identifier in range(tasks)
    ]
    # The oracle uses no Scheduler state beyond the identity of the quantum
    # selected by the explorer. Its phase/locals are updated independently.
    model_phase = [0] * tasks
    model_reads = [0] * tasks
    model_value = 0
    model_finalizers: list[int] = []
    cancelled = False
    model_cancelled = False
    for step, identifier in enumerate(prefix):
        if cancellation_at is not None and step == cancellation_at:
            handles[0].control.request_cancel()
            cancelled = True
        ready = [item.id - handles[0].control.id for item in scheduler.runnable]
        if identifier not in ready:
            raise AssertionError(f"invalid replay prefix {prefix}: ready={ready}")
        if cancelled and identifier == 0 and model_phase[0] != 3:
            model_cancelled = True
            if model_phase[0] != 0:
                model_finalizers.append(0)
            model_phase[0] = 3
        elif model_phase[identifier] == 0:
            model_reads[identifier] = model_value
            model_phase[identifier] = 1
        elif model_phase[identifier] == 1:
            model_value = model_reads[identifier] + 1
            model_phase[identifier] = 2
        elif model_phase[identifier] == 2:
            model_phase[identifier] = 3
            model_finalizers.append(identifier)
        else:
            raise AssertionError(f"terminal task was scheduled: {identifier}")
        # Select the requested runnable task, leaving relative order of all
        # other tasks intact. No scheduler internals are patched or mocked.
        while scheduler.runnable[0] is not handles[identifier].control:
            scheduler.runnable.rotate(-1)
        scheduler.step()
        if shared[0] != model_value or finished != model_finalizers:
            raise AssertionError(
                (prefix, shared[0], model_value, finished, model_finalizers)
            )
        for index, handle in enumerate(handles):
            expected = (
                TaskState.CANCELLED
                if model_cancelled and index == 0 and model_phase[index] == 3
                else TaskState.COMPLETED if model_phase[index] == 3
                else TaskState.PENDING if model_phase[index] == 0
                else TaskState.RUNNING
            )
            if handle.control.state is not expected:
                raise AssertionError((prefix, index, handle.control.state, expected))
            if handle.control.terminal_transition_count != int(model_phase[index] == 3):
                raise AssertionError("task entered terminal state more than once")
    if cancellation_at is not None and len(prefix) == cancellation_at:
        handles[0].control.request_cancel()
    return scheduler, handles, shared, finished, model_phase, model_value


def explore_schedules(
    *, tasks: int = 2, cancellation_at: int | None = None, max_schedules: int = 20000
) -> ScheduleReport:
    """Enumerate every reachable runnable choice through task termination.

    Bounds are enforced instead of silently reporting a truncated exploration.
    Cancellation (when requested) is injected before the indicated quantum.
    """
    if not 1 <= tasks <= 4:
        raise ValueError("tasks must be between 1 and 4")
    if cancellation_at is not None and cancellation_at < 0:
        raise ValueError("cancellation_at must be non-negative")
    if max_schedules < 1:
        raise ValueError("max_schedules must be positive")
    frontier = [()]
    terminals = 0
    explored = 0
    outcomes: set[int] = set()
    while frontier:
        prefix = frontier.pop()
        explored += 1
        scheduler, handles, shared, finished, phases, expected = _replay(
            prefix, tasks, cancellation_at
        )
        ready = [
            index for index, handle in enumerate(handles)
            if any(item is handle.control for item in scheduler.runnable)
        ]
        if not ready:
            if any(phase != 3 for phase in phases):
                raise AssertionError(
                    f"scheduler stopped before all tasks terminated: {prefix}"
                )
            if len(finished) != len(set(finished)) or shared[0] != expected:
                raise AssertionError("cleanup repeated or counter diverged")
            for index, handle in enumerate(handles):
                if handle.control.state is TaskState.COMPLETED:
                    if scheduler.wait(handle) != (index,):
                        raise AssertionError("task result disagrees with model")
                elif handle.control.state is TaskState.CANCELLED:
                    try:
                        scheduler.wait(handle)
                    except CancelledFault:
                        pass
                    else:
                        raise AssertionError("cancelled task returned a result")
                else:
                    raise AssertionError("unexpected terminal state")
            terminals += 1
            if terminals > max_schedules:
                raise ValueError("schedule bound exceeded; raise max_schedules")
            outcomes.add(expected)
        else:
            frontier.extend(prefix + (choice,) for choice in reversed(ready))
    return ScheduleReport(tasks, cancellation_at, terminals, explored, frozenset(outcomes))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=2)
    parser.add_argument("--cancel-at", type=int, default=None)
    parser.add_argument("--max-schedules", type=int, default=20000)
    args = parser.parse_args(argv)
    result = explore_schedules(
        tasks=args.tasks,
        cancellation_at=args.cancel_at,
        max_schedules=args.max_schedules,
    )
    print(
        f"PASS schedule explorer: tasks={result.tasks} "
        f"cancel_at={result.cancellation_at} schedules={result.schedules} "
        f"prefixes={result.explored_prefixes} outcomes={sorted(result.final_values)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
