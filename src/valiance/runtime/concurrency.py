"""Deterministic cooperative concurrency entities for the Valiance runtime.

This module owns scheduler-independent task/scope/channel state transitions.  The
VM adapter can suspend on the operations returned here without blocking the host
thread.  Keeping entities here makes the ownership and wake-up rules directly
unit-testable before bytecode lowering is involved.
"""

from __future__ import annotations

from collections import deque
import heapq
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Generic, Iterator, TypeVar

T = TypeVar("T")


class ConcurrencyFault(Exception):
    """Base class for runtime concurrency faults."""


class CancelledFault(ConcurrencyFault):
    """Raised when a cancelled task is observed."""


class ClosedFault(ConcurrencyFault):
    """Raised when sending to a closed channel."""


class DeadlockFault(ConcurrencyFault):
    """Raised when the scheduler proves that blocked tasks cannot progress."""

    def __init__(self, message: str, cycle: tuple[object, ...] = ()) -> None:
        """Initialize this runtime concurrency object."""
        super().__init__(message)
        self.cycle = cycle


def _with_secondary_faults(
    primary: BaseException,
    secondary: tuple[BaseException, ...],
    *,
    context: str,
) -> BaseException:
    """Attach deterministic secondary concurrency failures to one primary fault."""
    filtered = tuple(fault for fault in secondary if fault is not primary)
    if not filtered:
        return primary
    existing = tuple(getattr(primary, "secondary_faults", ()))
    combined = existing + tuple(fault for fault in filtered if fault not in existing)
    try:
        setattr(primary, "secondary_faults", combined)
    except (AttributeError, TypeError):
        pass
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        for index, fault in enumerate(filtered, 1):
            add_note(
                f"secondary {context} failure {index}: "
                f"{type(fault).__name__}: {fault}"
            )
    return primary


def render_concurrency_fault(exc: BaseException) -> str:
    """Render deterministic task, observation, and secondary-fault context."""
    lines = [f"{type(exc).__name__}: {exc}"]
    lines.extend(f"  {item}" for item in getattr(exc, "task_context", ()))
    observation = getattr(exc, "observation_site", None)
    if observation:
        lines.append(f"  observed at {observation}")
    for index, secondary in enumerate(getattr(exc, "secondary_faults", ()), 1):
        lines.append(
            f"  secondary {index}: {type(secondary).__name__}: {secondary}"
        )
        lines.extend(
            f"    {item}" for item in getattr(secondary, "task_context", ())
        )
    return "\n".join(lines)


class TaskState(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()

    @property
    def terminal(self) -> bool:
        """Return whether this task state is terminal."""
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class ScopeState(Enum):
    OPEN = auto()
    CLOSING = auto()
    CLOSED = auto()


class ExternalCallMode(Enum):
    """Scheduler contract declared by every concurrency-visible external call."""

    IMMEDIATE = auto()
    SUSPENDING = auto()
    HOST_BLOCKING = auto()


@dataclass(frozen=True, slots=True)
class ExternalCallPolicy:
    """Declared external-call progress and cancellation behavior."""

    mode: ExternalCallMode
    cancellation_aware: bool = False

    def validate_concurrent(self) -> None:
        """Reject calls that would freeze the initial single executor."""
        if self.mode is ExternalCallMode.HOST_BLOCKING:
            raise ConcurrencyFault(
                "host-blocking external call is unsupported during concurrent execution"
            )


@dataclass(frozen=True, slots=True)
class TaskYield:
    """One explicit cooperative suspension from a resumable task runner."""

    reason: str = "quantum"


@dataclass(frozen=True, slots=True)
class WaitDependency:
    """One deterministic edge from a blocked task to a waited resource."""

    kind: str
    identifier: int
    operation: str = "wait"
    location: str | None = None

    @property
    def label(self) -> str:
        """Render a stable human-facing resource label for diagnostics."""
        if self.kind == "task":
            return f"task {self.identifier}"
        label = f"{self.kind} {self.identifier} {self.operation}"
        return f"{label} at {self.location}" if self.location else label


@dataclass(frozen=True, slots=True)
class TaskBlocked:
    """A suspension re-enqueued only by a wake callback.

    ``dependencies`` records the scheduler-visible wait-graph edges established
    by the suspension.  It is diagnostic metadata only and never participates
    in wake-up or cancellation decisions.
    """

    reason: str
    cancel: Callable[[], None] | None = None
    dependencies: tuple[WaitDependency, ...] = ()


TaskRunner = Iterator[TaskYield | TaskBlocked]


@dataclass(frozen=True, slots=True)
class Receive(Generic[T]):
    """A channel receive result that distinguishes closure from a sent None."""

    value: T | None = None
    closed: bool = False

    @classmethod
    def Value(cls, value: T) -> Receive[T]:
        """Construct a receive result containing a transmitted value."""
        return cls(value=value)

    @classmethod
    def Closed(cls) -> Receive[T]:
        """Construct a receive result representing a drained closed channel."""
        return cls(closed=True)


@dataclass(slots=True)
class TaskControlBlock:
    id: int
    scope: TaskScope
    spawn_ordinal: int
    thunk: Callable[[], tuple[Any, ...] | TaskRunner]
    state: TaskState = TaskState.PENDING
    runner: TaskRunner | None = None
    quantum_count: int = 0
    blocked_cancel: Callable[[], None] | None = None
    blocked_reason: str | None = None
    blocked_dependencies: tuple[WaitDependency, ...] = ()
    cancellation_requested: bool = False
    terminal_outputs: tuple[Any, ...] = ()
    terminal_fault: BaseException | None = None
    waiters: list[Callable[[TaskControlBlock], None]] = field(default_factory=list)
    active_scopes: list[TaskScope] = field(default_factory=list)
    handle_owners: int = 1
    scope_owned: bool = True
    terminal_release: Callable[[tuple[Any, ...], BaseException | None], None] | None = None
    destroyed: bool = False
    creation_site: str | None = None
    owning_scope_site: str | None = None
    failure_context: tuple[str, ...] = ()
    terminal_transition_count: int = 0

    def retain_handle(self) -> None:
        """Retain one visible task-handle occurrence."""
        if self.destroyed:
            raise RuntimeError("cannot retain a destroyed task handle")
        self.handle_owners += 1

    def release_handle(
        self,
        release: Callable[[tuple[Any, ...], BaseException | None], None],
    ) -> None:
        """Release one visible handle and reclaim an unowned terminal outcome."""
        if self.handle_owners <= 0:
            raise RuntimeError("task handle ownership underflow")
        self.handle_owners -= 1
        self.terminal_release = release
        self._reclaim_if_unowned()

    def release_scope_ownership(self) -> None:
        """Drop structured ownership after the child has been joined."""
        self.scope_owned = False
        self._reclaim_if_unowned()

    def _reclaim_if_unowned(self) -> None:
        """Release terminal state after both scope and handles stop owning it."""
        if (
            self.destroyed
            or self.scope_owned
            or self.handle_owners
            or not self.state.terminal
        ):
            return
        release = self.terminal_release
        if release is not None:
            release(self.terminal_outputs, self.terminal_fault)
        self.terminal_outputs = ()
        self.terminal_fault = None
        self.thunk = lambda: ()
        self.runner = None
        self.active_scopes.clear()
        self.destroyed = True
        self.scope.scheduler.tasks.pop(self.id, None)

    def request_cancel(self) -> None:
        """Request cooperative cancellation and wake a blocked task."""
        if self.state.terminal:
            return
        self.cancellation_requested = True
        was_blocked = self.blocked_reason is not None
        if self.blocked_cancel is not None:
            cancel, self.blocked_cancel = self.blocked_cancel, None
            cancel()
        if was_blocked:
            self.scope.scheduler.schedule(self)

    def run(self) -> None:
        """Advance one task by at most one explicit cooperative quantum."""
        if self.state.terminal:
            return
        if self.cancellation_requested:
            self._finish_cancelled()
            return
        if self.state is TaskState.PENDING:
            self.state = TaskState.RUNNING
            try:
                started = self.thunk()
            except CancelledFault as fault:
                self._finish(TaskState.CANCELLED, fault=fault)
                return
            except BaseException as fault:
                self._finish(TaskState.FAILED, fault=fault)
                return
            if hasattr(started, "__next__"):
                self.runner = started  # type: ignore[assignment]
            else:
                self._finish(TaskState.COMPLETED, outputs=tuple(started))
                return
        if self.runner is None:
            return
        try:
            yielded = next(self.runner)
            if not isinstance(yielded, (TaskYield, TaskBlocked)):
                raise RuntimeError(
                    "resumable task runner must yield TaskYield or TaskBlocked markers"
                )
        except StopIteration as completed:
            outputs = () if completed.value is None else tuple(completed.value)
            self._finish(TaskState.COMPLETED, outputs=outputs)
        except CancelledFault as fault:
            self._finish(TaskState.CANCELLED, fault=fault)
        except BaseException as fault:
            self._finish(TaskState.FAILED, fault=fault)
        else:
            self.quantum_count += 1
            if self.cancellation_requested:
                self._finish_cancelled()
            elif isinstance(yielded, TaskBlocked):
                self.blocked_cancel = yielded.cancel
                self.blocked_reason = yielded.reason
                self.blocked_dependencies = yielded.dependencies
            else:
                self.scope.scheduler.schedule(self)

    def _finish_cancelled(self) -> None:
        """Close suspended execution, then commit one cancelled terminal state."""
        cleanup_fault: BaseException | None = None
        if self.runner is not None:
            try:
                self.runner.close()
            except BaseException as fault:
                cleanup_fault = fault
            finally:
                self.runner = None
        cancelled = CancelledFault(f"task {self.id} was cancelled")
        if cleanup_fault is not None:
            _with_secondary_faults(
                cancelled, (cleanup_fault,), context="cancellation cleanup"
            )
        self._finish(TaskState.CANCELLED, fault=cancelled)

    def _finish(
        self,
        state: TaskState,
        *,
        outputs: tuple[Any, ...] = (),
        fault: BaseException | None = None,
    ) -> None:
        """Commit one terminal task transition and notify its observers."""
        if state not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
            raise ValueError("task can only finish in a terminal state")
        if self.state.terminal:
            raise RuntimeError("terminal task cannot transition again")
        self.state = state
        self.terminal_transition_count += 1
        self.blocked_cancel = None
        self.blocked_reason = None
        self.blocked_dependencies = ()
        self.terminal_outputs = outputs
        self.terminal_fault = fault
        if fault is not None:
            context = [f"task {self.id} ({state.name.lower()})"]
            if self.creation_site:
                context.append(f"spawned at {self.creation_site}")
            if self.owning_scope_site:
                context.append(f"owned by scope at {self.owning_scope_site}")
            self.failure_context = tuple(context)
            try:
                setattr(fault, "task_context", self.failure_context)
            except (AttributeError, TypeError):
                pass
        self.scope._child_finished(self)
        waiters, self.waiters = self.waiters, []
        for wake in waiters:
            wake(self)


@dataclass(frozen=True, slots=True)
class TaskHandle:
    """Immutable, repeatably observable identity for one task."""

    control: TaskControlBlock

    @property
    def task_transfer_class(self):
        """Classify this immutable runtime handle for task transfer."""
        from valiance.runtime.transfer import TransferClass
        return TransferClass.SHARED_HANDLE

    @property
    def id(self) -> int:
        """Return the stable runtime entity identifier."""
        return self.control.id

    def result(self, observation_site: str | None = None) -> tuple[Any, ...]:
        """Return stored outputs or re-raise the stored terminal fault."""
        task = self.control
        if task.state is TaskState.COMPLETED:
            return tuple(task.terminal_outputs)
        if task.state is TaskState.FAILED:
            assert task.terminal_fault is not None
            if observation_site:
                try:
                    setattr(task.terminal_fault, "observation_site", observation_site)
                except (AttributeError, TypeError):
                    pass
            raise task.terminal_fault
        if task.state is TaskState.CANCELLED:
            raise CancelledFault(f"task {task.id} was cancelled") from task.terminal_fault
        raise RuntimeError(f"task {task.id} is not terminal")


@dataclass(slots=True)
class TaskScope:
    scheduler: Scheduler
    parent: TaskScope | None = None
    state: ScopeState = ScopeState.OPEN
    children: list[TaskControlBlock] = field(default_factory=list)
    child_faults: list[tuple[int, BaseException]] = field(default_factory=list)
    body_fault: BaseException | None = None
    owner_task: TaskControlBlock | None = None
    pending_failure: BaseException | None = None
    close_waiters: list[Callable[[], None]] = field(default_factory=list)
    _next_ordinal: int = 0
    creation_site: str | None = None

    def spawn(
        self,
        thunk: Callable[[], tuple[Any, ...] | TaskRunner],
        *,
        creation_site: str | None = None,
    ) -> TaskHandle:
        """Create, register, and schedule one child task in this scope."""
        if self.state is not ScopeState.OPEN:
            raise RuntimeError("cannot spawn into a closing scope")
        task = TaskControlBlock(
            self.scheduler.next_task_id(), self, self._next_ordinal, thunk,
            creation_site=creation_site, owning_scope_site=self.creation_site,
        )
        self._next_ordinal += 1
        task.active_scopes.append(self)
        self.children.append(task)
        self.scheduler.tasks[task.id] = task
        self.scheduler.schedule(task)
        return TaskHandle(task)

    def _child_finished(self, task: TaskControlBlock) -> None:
        """Record child completion and trigger fail-fast sibling cancellation."""
        if task.state is TaskState.FAILED:
            assert task.terminal_fault is not None
            self.child_faults.append((task.spawn_ordinal, task.terminal_fault))
            if self.pending_failure is None:
                self.pending_failure = task.terminal_fault
            for sibling in self.children:
                if sibling is not task and not sibling.state.terminal:
                    sibling.request_cancel()
            if self.owner_task is not None:
                self.scheduler.schedule(self.owner_task)
        if self.state is ScopeState.CLOSING and self.children_terminal:
            waiters, self.close_waiters = self.close_waiters, []
            for wake in waiters:
                wake()

    @property
    def children_terminal(self) -> bool:
        """Return whether every child owned by this scope is terminal."""
        return all(child.state.terminal for child in self.children)

    def begin_close(
        self,
        body_fault: BaseException | None = None,
        wake: Callable[[], None] | None = None,
    ) -> bool:
        """Start closing without driving the scheduler and report readiness."""
        if self.state is ScopeState.CLOSED:
            return True
        self.state = ScopeState.CLOSING
        if body_fault is not None:
            self.body_fault = body_fault
            for child in self.children:
                if not child.state.terminal:
                    child.request_cancel()
        if self.children_terminal:
            return True
        if wake is not None and wake not in self.close_waiters:
            self.close_waiters.append(wake)
        return False

    def cancel_close_waiter(self, wake: Callable[[], None]) -> None:
        """Remove one blocked owner callback and cancel unfinished children."""
        if wake in self.close_waiters:
            self.close_waiters.remove(wake)
        for child in self.children:
            if not child.state.terminal:
                child.request_cancel()

    def finalize_close(self) -> BaseException | None:
        """Finalize a ready scope and return its selected fault without raising."""
        if self.state is ScopeState.CLOSED:
            return None
        if not self.children_terminal:
            raise RuntimeError("cannot finish a scope with unfinished children")
        self.state = ScopeState.CLOSED
        self.close_waiters.clear()
        ordered_faults = tuple(
            fault for _ordinal, fault in sorted(self.child_faults, key=lambda item: item[0])
        )
        selected: BaseException | None = None
        if self.body_fault is not None:
            selected = _with_secondary_faults(
                self.body_fault, ordered_faults, context="child task"
            )
        elif ordered_faults:
            selected = _with_secondary_faults(
                ordered_faults[0], ordered_faults[1:], context="child task"
            )
        children, self.children = self.children, []
        self.child_faults.clear()
        self.pending_failure = None
        for child in children:
            child.release_scope_ownership()
        return selected

    def finish_close(self) -> None:
        """Finalize a ready scope and propagate its deterministic fault."""
        fault = self.finalize_close()
        if fault is not None:
            raise fault

    def close(self, body_fault: BaseException | None = None) -> None:
        """Synchronously close a scope for non-resumable root execution."""
        if not self.begin_close(body_fault):
            self.scheduler.run_until(lambda: self.children_terminal)
        self.finish_close()


@dataclass(slots=True)
class SuspensionRegistration:
    """Exactly-once scheduler registration for a timer or external wake source."""

    scheduler: "Scheduler"
    kind: str
    identifier: int
    wake: Callable[[], None]
    deadline: int | None = None
    active: bool = True
    fired: bool = False

    def cancel(self) -> bool:
        """Unregister once; a committed wake cannot subsequently be cancelled."""
        if not self.active:
            return False
        self.active = False
        self.scheduler._external_registrations.pop(self.identifier, None)
        return True

    def fire(self) -> bool:
        """Commit one wake exactly once."""
        if not self.active:
            return False
        self.active = False
        self.fired = True
        self.scheduler._external_registrations.pop(self.identifier, None)
        self.wake()
        return True


class Scheduler:
    """FIFO deterministic task scheduler used by a VM execution context."""

    def __init__(self) -> None:
        """Initialize this runtime concurrency object."""
        self.runnable: deque[TaskControlBlock] = deque()
        self._next_id = 1
        self.current_task: TaskControlBlock | None = None
        self.waiting_on_task: dict[int, int] = {}
        self.tasks: dict[int, TaskControlBlock] = {}
        self.root_scope = TaskScope(self)
        self.logical_time = 0
        self._next_registration_id = 1
        self._timer_heap: list[tuple[int, int, SuspensionRegistration]] = []
        self._external_registrations: dict[int, SuspensionRegistration] = {}

    def register_timer(
        self, delay: int, wake: Callable[[], None]
    ) -> SuspensionRegistration:
        """Register a deterministic logical timer without blocking the host."""
        if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
            raise ValueError("timer delay must be a non-negative integer")
        identifier = self._next_registration_id
        self._next_registration_id += 1
        registration = SuspensionRegistration(
            self, "timer", identifier, wake, self.logical_time + delay
        )
        self._external_registrations[identifier] = registration
        heapq.heappush(
            self._timer_heap, (registration.deadline, identifier, registration)
        )
        return registration

    def register_external(self, wake: Callable[[], None]) -> SuspensionRegistration:
        """Register a deterministic mock external wake source."""
        identifier = self._next_registration_id
        self._next_registration_id += 1
        registration = SuspensionRegistration(self, "external", identifier, wake)
        self._external_registrations[identifier] = registration
        return registration

    @property
    def has_pending_external_wake(self) -> bool:
        """Return whether a timer or external source can still make progress."""
        return any(item.active for item in self._external_registrations.values())

    def _fire_next_timer(self) -> bool:
        """Advance logical time and fire the next active timer deterministically."""
        while self._timer_heap:
            deadline, _identifier, registration = heapq.heappop(self._timer_heap)
            if not registration.active:
                continue
            self.logical_time = max(self.logical_time, deadline)
            return registration.fire()
        return False

    def next_task_id(self) -> int:
        """Allocate the next deterministic task identifier."""
        task_id = self._next_id
        self._next_id += 1
        return task_id

    def schedule(self, task: TaskControlBlock) -> None:
        """Queue a runnable task exactly once and clear blocked metadata."""
        task.blocked_reason = None
        task.blocked_dependencies = ()
        if (
            task.state in {TaskState.PENDING, TaskState.RUNNING}
            and all(item is not task for item in self.runnable)
        ):
            self.runnable.append(task)

    def step(self) -> bool:
        """Advance the next runnable task by one cooperative quantum."""
        if not self.runnable:
            return False
        task = self.runnable.popleft()
        previous = self.current_task
        self.current_task = task
        try:
            task.run()
        finally:
            self.current_task = previous
            self.waiting_on_task.pop(task.id, None)
        return True

    def _wait_cycle(self, start: int) -> tuple[int, ...]:
        """Return the task-wait cycle reachable from ``start``, if any."""
        positions: dict[int, int] = {}
        path: list[int] = []
        current = start
        while current in self.waiting_on_task:
            if current in positions:
                cycle = path[positions[current] :]
                minimum = min(range(len(cycle)), key=cycle.__getitem__)
                normalized = cycle[minimum:] + cycle[:minimum]
                return tuple(normalized + [normalized[0]])
            positions[current] = len(path)
            path.append(current)
            current = self.waiting_on_task[current]
        return ()

    def _blocked_edges(self) -> tuple[tuple[int, WaitDependency], ...]:
        """Return all currently blocked wait-graph edges in stable order."""
        edges = [
            (task.id, dependency)
            for task in self.tasks.values()
            for dependency in task.blocked_dependencies
            if not task.state.terminal
        ]
        known = {
            (owner, dependency.kind, dependency.identifier)
            for owner, dependency in edges
        }
        for owner, target in self.waiting_on_task.items():
            key = (owner, "task", target)
            if key not in known:
                edges.append((owner, WaitDependency("task", target)))
        return tuple(
            sorted(
                edges,
                key=lambda item: (
                    item[0], item[1].kind, item[1].identifier, item[1].operation
                ),
            )
        )

    def _blocked_cycle(self) -> tuple[str, ...]:
        """Return one stable cycle across blocked tasks and runtime resources."""
        graph: dict[str, set[str]] = {}
        for owner, dependency in self._blocked_edges():
            task_node = f"task {owner}"
            resource_node = dependency.label
            graph.setdefault(task_node, set()).add(resource_node)
            if dependency.kind != "task":
                # A blocked registration is the scheduler-visible owner of this
                # resource edge until it commits, wakes, or is cancelled.
                graph.setdefault(resource_node, set()).add(task_node)
        visiting: set[str] = set()
        visited: set[str] = set()
        path: list[str] = []

        def visit(node: str) -> tuple[str, ...]:
            """Depth-first search one deterministic wait-graph component."""
            if node in visiting:
                start = path.index(node)
                cycle = path[start:] + [node]
                body = cycle[:-1]
                minimum = min(
                    range(len(body)),
                    key=lambda index: (
                        0 if body[index].startswith("task ") else 1,
                        body[index],
                    ),
                )
                normalized = body[minimum:] + body[:minimum]
                return tuple(normalized + [normalized[0]])
            if node in visited:
                return ()
            visiting.add(node)
            path.append(node)
            for target in sorted(graph.get(node, ())):
                cycle = visit(target)
                if cycle:
                    return cycle
            path.pop()
            visiting.remove(node)
            visited.add(node)
            return ()

        for node in sorted(graph):
            cycle = visit(node)
            if cycle:
                return cycle
        return ()

    def _render_blocked_edge(self, owner: int, dependency: WaitDependency) -> str:
        """Render one edge with task creation and blocked-operation locations."""
        task = self.tasks.get(owner)
        origin = f" (spawned at {task.creation_site})" if task and task.creation_site else ""
        return f"task {owner}{origin} waits for {dependency.label}"

    def run_until(self, predicate: Callable[[], bool]) -> None:
        """Drive runnable tasks until the predicate holds or deadlock is proven."""
        while not predicate():
            if not self.step():
                if self._fire_next_timer():
                    continue
                if self.has_pending_external_wake:
                    raise RuntimeError("scheduler awaits an external wake source")
                blocked = self._blocked_edges()
                cycle = self._blocked_cycle()
                if cycle:
                    rendered = " -> ".join(cycle)
                    task_ids = tuple(
                        int(label.removeprefix("task "))
                        for label in cycle
                        if label.startswith("task ")
                    )
                    detail = ", ".join(
                        self._render_blocked_edge(owner, dependency)
                        for owner, dependency in blocked
                    )
                    raise DeadlockFault(
                        f"deadlock: {detail}; wait cycle {rendered}",
                        task_ids if all(label.startswith("task ") for label in cycle) else cycle,
                    )
                detail = (
                    ", ".join(
                        self._render_blocked_edge(owner, dependency)
                        for owner, dependency in blocked
                    )
                    if blocked
                    else "unfinished tasks exist but none are runnable"
                )
                raise DeadlockFault(f"deadlock: {detail}")

    def wait(
        self, handle: TaskHandle, *, observation_site: str | None = None
    ) -> tuple[Any, ...]:
        """Wait for one task while recording task-to-task dependencies."""
        owner = self.current_task
        target = handle.control
        if owner is target:
            raise DeadlockFault(
                f"deadlock: task {owner.id} cannot wait on itself",
                (owner.id, owner.id),
            )
        if owner is not None and not target.state.terminal:
            self.waiting_on_task[owner.id] = target.id
            cycle = self._wait_cycle(owner.id)
            if cycle:
                self.waiting_on_task.pop(owner.id, None)
                rendered = " -> ".join(f"task {task_id}" for task_id in cycle)
                raise DeadlockFault(f"deadlock: task wait cycle {rendered}", cycle)
        try:
            self.run_until(lambda: target.state.terminal)
            return handle.result(observation_site)
        finally:
            if owner is not None:
                self.waiting_on_task.pop(owner.id, None)

    def wait_all(
        self, handles: list[TaskHandle], *, observation_site: str | None = None
    ) -> list[tuple[Any, ...]]:
        """Join a task group and preserve deterministic input-order failures."""
        unique = {id(handle.control): handle.control for handle in handles}
        def group_finished_or_failed() -> bool:
            """Stop driving when the group completes or its first failure appears."""
            return (
                all(task.state.terminal for task in unique.values())
                or any(task.state is TaskState.FAILED for task in unique.values())
            )

        try:
            self.run_until(group_finished_or_failed)
        except BaseException:
            for task in unique.values():
                task.request_cancel()
            self.run_until(lambda: all(task.state.terminal for task in unique.values()))
            raise
        if any(task.state is TaskState.FAILED for task in unique.values()):
            for task in unique.values():
                if not task.state.terminal:
                    task.request_cancel()
            self.run_until(lambda: all(task.state.terminal for task in unique.values()))
        failures = [
            (index, handle.control.terminal_fault)
            for index, handle in enumerate(handles)
            if handle.control.state is TaskState.FAILED
        ]
        if failures:
            for task in unique.values():
                task.request_cancel()
            ordered = tuple(
                fault
                for _index, fault in sorted(failures, key=lambda item: item[0])
                if fault is not None
            )
            primary = _with_secondary_faults(
                ordered[0], ordered[1:], context="group wait"
            )
            if observation_site:
                try:
                    setattr(primary, "observation_site", observation_site)
                except (AttributeError, TypeError):
                    pass
            raise primary
        return [handle.result() for handle in handles]


@dataclass(slots=True)
class ChannelSender(Generic[T]):
    """One cancellable blocked send registration."""
    value: T
    committed: bool = False
    fault: BaseException | None = None
    cancelled: bool = False
    wake: Callable[[], None] | None = None


@dataclass(slots=True)
class ChannelReceiver(Generic[T]):
    """One cancellable blocked receive registration."""
    result: Receive[T] | None = None
    cancelled: bool = False
    wake: Callable[[], None] | None = None


class Channel(Generic[T]):
    """Typed-by-analysis unbuffered or bounded FIFO channel entity."""

    _next_id = 1

    @property
    def task_transfer_class(self):
        """Classify this immutable runtime handle for task transfer."""
        from valiance.runtime.transfer import TransferClass
        return TransferClass.SHARED_HANDLE

    def __init__(
        self, capacity: int = 0, *, creation_site: str | None = None
    ) -> None:
        """Initialize this runtime concurrency object."""
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
            raise ValueError("channel capacity must be a non-negative integer")
        self.id = Channel._next_id
        Channel._next_id += 1
        self.capacity = capacity
        self.creation_site = creation_site
        self.closed = False
        self.buffer: deque[T] = deque()
        self.senders: deque[ChannelSender[T]] = deque()
        self.receivers: deque[ChannelReceiver[T]] = deque()
        self.handle_owners = 1
        self.destroyed = False
        self.committed_sends = 0
        self.cancelled_sends = 0
        self.faulted_sends = 0
        self.committed_receives = 0
        self.cancelled_receives = 0
        self._release_buffered: Callable[[T], None] | None = None

    def retain_handle(self) -> None:
        """Retain one immutable channel-handle occurrence."""
        if self.destroyed:
            raise RuntimeError("cannot retain a destroyed channel")
        self.handle_owners += 1

    def release_handle(self, release: Callable[[T], None]) -> None:
        """Release one channel handle and reclaim an unreachable entity."""
        if self.handle_owners <= 0:
            raise RuntimeError("channel handle ownership underflow")
        self.handle_owners -= 1
        self._release_buffered = release
        self._reclaim_if_unowned()

    def _reclaim_if_unowned(self) -> None:
        """Destroy an entity after handles and blocked registrations are gone."""
        if (
            self.destroyed
            or self.handle_owners
            or self.senders
            or self.receivers
        ):
            return
        release = self._release_buffered
        if release is not None:
            while self.buffer:
                release(self.buffer.popleft())
        self.destroyed = True

    def register_send(
        self, value: T, wake: Callable[[], None] | None = None
    ) -> ChannelSender[T] | None:
        """Try to send, returning a cancellable registration when blocked."""
        before = len(self.senders)
        if self.try_send(value):
            return None
        assert len(self.senders) == before + 1
        self.senders[-1].wake = wake
        return self.senders[-1]

    def cancel_send(self, registration: ChannelSender[T]) -> bool:
        """Remove one uncommitted send without disturbing FIFO peers."""
        if registration.committed or registration.fault is not None or registration.cancelled:
            return False
        try:
            self.senders.remove(registration)
        except ValueError:
            return False
        registration.cancelled = True
        self.cancelled_sends += 1
        self._reclaim_if_unowned()
        return True

    def register_receive(
        self, wake: Callable[[], None] | None = None
    ) -> ChannelReceiver[T] | Receive[T]:
        """Try to receive, returning a cancellable registration when blocked."""
        before = len(self.receivers)
        result = self.try_receive()
        if result is not None:
            return result
        assert len(self.receivers) == before + 1
        self.receivers[-1].wake = wake
        return self.receivers[-1]

    def cancel_receive(self, registration: ChannelReceiver[T]) -> bool:
        """Remove one unresolved receive without disturbing FIFO peers."""
        if registration.result is not None or registration.cancelled:
            return False
        try:
            self.receivers.remove(registration)
        except ValueError:
            return False
        registration.cancelled = True
        self.cancelled_receives += 1
        self._reclaim_if_unowned()
        return True

    def try_send(self, value: T) -> bool:
        """Commit or register one FIFO channel send operation."""
        if self.closed:
            raise ClosedFault("cannot send to a closed channel")
        if self.receivers:
            receiver = self.receivers.popleft()
            receiver.result = Receive.Value(value)
            self.committed_sends += 1
            self.committed_receives += 1
            if receiver.wake is not None:
                receiver.wake()
            self._reclaim_if_unowned()
            return True
        if self.capacity and len(self.buffer) < self.capacity:
            self.buffer.append(value)
            self.committed_sends += 1
            return True
        self.senders.append(ChannelSender(value))
        return False

    def try_receive(self) -> Receive[T] | None:
        """Commit or register one FIFO channel receive operation."""
        if self.buffer:
            value = self.buffer.popleft()
            self.committed_receives += 1
            if self.senders and not self.closed:
                sender = self.senders.popleft()
                self.buffer.append(sender.value)
                sender.committed = True
                self.committed_sends += 1
                if sender.wake is not None:
                    sender.wake()
            self._reclaim_if_unowned()
            return Receive.Value(value)
        if self.senders and not self.closed:
            sender = self.senders.popleft()
            sender.committed = True
            self.committed_sends += 1
            self.committed_receives += 1
            if sender.wake is not None:
                sender.wake()
            self._reclaim_if_unowned()
            return Receive.Value(sender.value)
        if self.closed:
            return Receive.Closed()
        self.receivers.append(ChannelReceiver())
        return None

    def close(self) -> None:
        """Join all children and propagate the deterministic primary fault."""
        if self.closed:
            return
        self.closed = True
        while self.senders:
            sender = self.senders.popleft()
            sender.fault = ClosedFault("channel closed before send committed")
            self.faulted_sends += 1
            if sender.wake is not None:
                sender.wake()
        while self.receivers and self.buffer:
            receiver = self.receivers.popleft()
            receiver.result = Receive.Value(self.buffer.popleft())
            self.committed_receives += 1
            if receiver.wake is not None:
                receiver.wake()
        while self.receivers:
            receiver = self.receivers.popleft()
            receiver.result = Receive.Closed()
            if receiver.wake is not None:
                receiver.wake()
        self._reclaim_if_unowned()
