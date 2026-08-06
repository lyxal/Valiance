"""Replayable deterministic concurrency fuzzing for scheduler entity invariants."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

from valiance.runtime.concurrency import Channel, Scheduler, TaskState

DEFAULT_CONCURRENCY_SEED = 0xC0_2026


@dataclass(frozen=True, slots=True)
class ConcurrencyFuzzConfig:
    """Bound one replayable concurrency fuzz campaign."""

    seed: int = DEFAULT_CONCURRENCY_SEED
    iterations: int = 250
    start: int = 0
    operations: int = 64


@dataclass(frozen=True, slots=True)
class ConcurrencyFuzzResult:
    """Describe one completed deterministic campaign."""

    seed: int
    start: int
    iterations: int
    cases: int


class ConcurrencyFuzzFailure(AssertionError):
    """Report the exact seed, iteration, and minimized operation prefix."""

    def __init__(
        self, config: ConcurrencyFuzzConfig, iteration: int, sequence: list[str], cause: BaseException
    ) -> None:
        self.seed = config.seed
        self.iteration = iteration
        self.sequence = tuple(sequence)
        self.cause = cause
        command = (
            "python -m tools.concurrency_fuzz --seed "
            f"{config.seed} --start {iteration} --iterations 1 "
            f"--operations {config.operations}"
        )
        super().__init__(
            f"concurrency fuzz failure: seed={config.seed} iteration={iteration}\n"
            f"sequence={sequence!r}\ncause={cause!r}\nreproduce: {command}"
        )


def _rng(seed: int, iteration: int) -> random.Random:
    """Derive a case RNG whose sequence is independent of campaign slicing."""
    digest = hashlib.blake2b(f"concurrency:{seed}:{iteration}".encode(), digest_size=16).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _runner(task_id: int, yields: int, outputs: list[int]):
    """Yield a bounded number of quanta and complete exactly once."""
    from valiance.runtime.concurrency import TaskYield

    for _ in range(yields):
        yield TaskYield("fuzz")
    outputs.append(task_id)
    return (task_id,)


def _run_case(rng: random.Random, operation_limit: int) -> list[str]:
    """Execute one generated task/channel lifecycle and assert global invariants."""
    scheduler = Scheduler()
    channel = Channel[int](rng.randrange(4))
    events: list[int] = []
    handles = [
        scheduler.root_scope.spawn(
            lambda task_id=index, count=rng.randrange(5): _runner(task_id, count, events)
        )
        for index in range(rng.randint(1, 12))
    ]
    aliases = [handles[rng.randrange(len(handles))] for _ in range(rng.randrange(8))]
    sequence: list[str] = []
    sent: list[int] = []
    received: list[int] = []

    for _ in range(operation_limit):
        operation = rng.choice(("step", "cancel", "send", "receive", "close", "wait"))
        sequence.append(operation)
        if operation == "step":
            scheduler.step()
        elif operation == "cancel":
            rng.choice(handles).control.request_cancel()
        elif operation == "send" and not channel.closed:
            value = len(sent)
            registration = channel.register_send(value)
            if registration is None or registration.committed:
                sent.append(value)
            else:
                channel.cancel_send(registration)
        elif operation == "receive":
            result = channel.register_receive()
            if hasattr(result, "result"):
                channel.cancel_receive(result)
            elif not result.closed:
                received.append(result.value)
        elif operation == "close":
            channel.close()
        elif operation == "wait":
            handle = rng.choice(handles + aliases)
            if handle.control.state.terminal:
                try:
                    handle.result()
                except Exception:
                    pass

    for handle in handles:
        if not handle.control.state.terminal:
            handle.control.request_cancel()
    scheduler.run_until(lambda: all(handle.control.state.terminal for handle in handles))
    channel.close()
    while True:
        result = channel.try_receive()
        if result is None or result.closed:
            break
        received.append(result.value)

    tasks = [handle.control for handle in handles]
    assert all(task.terminal_transition_count == 1 for task in tasks)
    assert not scheduler.runnable
    assert all(not task.waiters and task.blocked_cancel is None for task in tasks)
    assert not channel.senders and not channel.receivers
    assert received == sorted(received)
    assert len(received) == len(set(received))
    assert channel.committed_sends + channel.cancelled_sends + channel.faulted_sends >= len(sent)
    return sequence


def run_concurrency_fuzz(config: ConcurrencyFuzzConfig) -> ConcurrencyFuzzResult:
    """Run deterministic cases and wrap failures with a replay command."""
    if min(config.iterations, config.start, config.operations) < 0:
        raise ValueError("fuzz bounds must be non-negative")
    for iteration in range(config.start, config.start + config.iterations):
        sequence: list[str] = []
        try:
            sequence = _run_case(_rng(config.seed, iteration), config.operations)
        except BaseException as exc:
            raise ConcurrencyFuzzFailure(config, iteration, sequence, exc) from exc
    return ConcurrencyFuzzResult(config.seed, config.start, config.iterations, config.iterations)
