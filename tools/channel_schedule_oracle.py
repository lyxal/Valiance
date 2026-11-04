"""Finite exhaustive channel operation-order exploration against a separate model.

Enumerates all 7! operation orders for each of three channel capacities. This
is NOT full VM instruction interleaving or multithreaded native scheduling.
The model intentionally does not read Channel's internal state to predict an
outcome; internal fields are only inspected when checking it after each step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from collections import deque

from valiance.runtime.concurrency import Channel, ChannelSender, ChannelReceiver, ClosedFault, Receive

OPERATIONS = ('send0', 'send1', 'receive0', 'receive1',
              'cancel_sender', 'cancel_receiver', 'close')


@dataclass
class Oracle:
    capacity: int
    buffer: deque[int] = field(default_factory=deque)
    senders: deque[int] = field(default_factory=deque)
    receivers: deque[int] = field(default_factory=deque)
    closed: bool = False
    send_status: dict[int, str] = field(default_factory=dict)
    receiver_results: dict[int, tuple[str, int | None] | None] = field(default_factory=dict)
    wakes: dict[str, int] = field(default_factory=dict)
    committed_sends: int = 0
    committed_receives: int = 0
    cancelled_sends: int = 0
    cancelled_receives: int = 0
    faulted_sends: int = 0

    def _wake(self, operation: str, identifier: int) -> None:
        key = ('s' if operation == 'send' else 'r') + str(identifier)
        self.wakes[key] = self.wakes.get(key, 0) + 1

    def action(self, step: str) -> tuple[str, int | None] | None:
        if step.startswith('send'):
            identifier = int(step[-1])
            if self.closed:
                self.send_status[identifier] = 'closed_rejected'
                return ('closed_rejected', None)
            if self.receivers:
                recipient = self.receivers.popleft()
                self.receiver_results[recipient] = ('value', identifier)
                self._wake('receive', recipient)
                self.send_status[identifier] = 'committed'
                self.committed_sends += 1
                self.committed_receives += 1
                return ('committed', None)
            if self.capacity and len(self.buffer) < self.capacity:
                self.buffer.append(identifier)
                self.send_status[identifier] = 'committed'
                self.committed_sends += 1
                return ('committed', None)
            self.senders.append(identifier)
            self.send_status[identifier] = 'pending'
            return ('pending', None)
        if step.startswith('receive'):
            identifier = int(step[-1])
            if self.buffer:
                value = self.buffer.popleft()
                self.committed_receives += 1
                if self.senders and not self.closed:
                    sender = self.senders.popleft()
                    self.buffer.append(sender)
                    self.send_status[sender] = 'committed'
                    self.committed_sends += 1
                    self._wake('send', sender)
                result = ('value', value)
            elif self.senders and not self.closed:
                sender = self.senders.popleft()
                self.send_status[sender] = 'committed'
                self.committed_sends += 1
                self.committed_receives += 1
                self._wake('send', sender)
                result = ('value', sender)
            elif self.closed:
                result = ('closed', None)
            else:
                self.receivers.append(identifier)
                result = None
            self.receiver_results[identifier] = result
            return result
        if step == 'cancel_sender':
            if self.senders:
                identifier = self.senders.popleft()
                self.send_status[identifier] = 'cancelled'
                self.cancelled_sends += 1
                return ('cancelled_sender', identifier)
            return ('nothing_to_cancel', None)
        if step == 'cancel_receiver':
            if self.receivers:
                identifier = self.receivers.popleft()
                self.receiver_results[identifier] = ('cancelled', None)
                self.cancelled_receives += 1
                return ('cancelled_receiver', identifier)
            return ('nothing_to_cancel', None)
        if step != 'close':
            raise ValueError(step)
        self.closed = True
        while self.senders:
            identifier = self.senders.popleft()
            self.send_status[identifier] = 'faulted'
            self.faulted_sends += 1
            self._wake('send', identifier)
        while self.receivers and self.buffer:
            identifier = self.receivers.popleft()
            self.receiver_results[identifier] = ('value', self.buffer.popleft())
            self.committed_receives += 1
            self._wake('receive', identifier)
        while self.receivers:
            identifier = self.receivers.popleft()
            self.receiver_results[identifier] = ('closed', None)
            self._wake('receive', identifier)
        return ('closed', None)


def _receive_state(result: Receive | None) -> tuple[str, int | None] | None:
    if result is None:
        return None
    return ('closed', None) if result.closed else ('value', result.value)


def _replay(schedule: tuple[str, ...], capacity: int) -> None:
    model = Oracle(capacity)
    channel = Channel[int](capacity)
    send_registers: dict[int, ChannelSender | None] = {}
    recv_registers: dict[int, ChannelReceiver | Receive] = {}
    wakes: dict[str, int] = {}

    def wake(kind: str, identifier: int):
        def callback():
            key = kind + str(identifier)
            wakes[key] = wakes.get(key, 0) + 1
        return callback

    for step in schedule:
        expected = model.action(step)
        if step.startswith('send'):
            identifier = int(step[-1])
            if expected[0] == 'closed_rejected':
                try:
                    channel.register_send(identifier, wake('s', identifier))
                except ClosedFault:
                    pass
                else:
                    raise AssertionError((schedule, step, 'closed send accepted'))
            else:
                registered = channel.register_send(identifier, wake('s', identifier))
                send_registers[identifier] = registered
                if (registered is None) != (expected[0] == 'committed'):
                    raise AssertionError((schedule, step, 'send mode mismatch'))
        elif step.startswith('receive'):
            identifier = int(step[-1])
            got = channel.register_receive(wake('r', identifier))
            recv_registers[identifier] = got
            observed = None if isinstance(got, ChannelReceiver) else _receive_state(got)
            if observed != expected:
                raise AssertionError((schedule, step, observed, expected))
        elif step == 'cancel_sender':
            if expected[0] == 'cancelled_sender':
                identifier = expected[1]
                reg = send_registers[identifier]
                if reg is None or not channel.cancel_send(reg):
                    raise AssertionError((schedule, 'cannot cancel queued send'))
                if channel.cancel_send(reg):
                    raise AssertionError((schedule, 'cancelled send twice'))
        elif step == 'cancel_receiver':
            if expected[0] == 'cancelled_receiver':
                identifier = expected[1]
                reg = recv_registers[identifier]
                if not isinstance(reg, ChannelReceiver) or not channel.cancel_receive(reg):
                    raise AssertionError((schedule, 'cannot cancel queued receive'))
                if channel.cancel_receive(reg):
                    raise AssertionError((schedule, 'cancelled receive twice'))
        else:
            channel.close()
            channel.close()  # idempotence; no duplicate wake or transition

        values = (channel.committed_sends, channel.committed_receives,
                  channel.cancelled_sends, channel.cancelled_receives,
                  channel.faulted_sends)
        expected_counts = (model.committed_sends, model.committed_receives,
                           model.cancelled_sends, model.cancelled_receives,
                           model.faulted_sends)
        if values != expected_counts or tuple(channel.buffer) != tuple(model.buffer):
            raise AssertionError((schedule, step, values, expected_counts,
                                  list(channel.buffer), list(model.buffer)))
        if list(reg.value for reg in channel.senders) != list(model.senders):
            raise AssertionError((schedule, step, 'sender queue divergence'))
        if len(channel.receivers) != len(model.receivers) or channel.closed != model.closed:
            raise AssertionError((schedule, step, 'receiver / closed divergence'))
        if wakes != model.wakes:
            raise AssertionError((schedule, step, wakes, model.wakes))
        for identifier, reg in send_registers.items():
            status = model.send_status[identifier]
            if reg is None and status != 'committed':
                raise AssertionError((schedule, 'committed send changed state'))
            if reg is not None and (
                bool(reg.committed) != (status == 'committed')
                or bool(reg.cancelled) != (status == 'cancelled')
                or bool(reg.fault) != (status == 'faulted')
            ):
                raise AssertionError((schedule, step, identifier, status))
        for identifier, reg in recv_registers.items():
            result = model.receiver_results[identifier]
            if isinstance(reg, ChannelReceiver):
                observed = ('cancelled', None) if reg.cancelled else _receive_state(reg.result)
            else:
                observed = _receive_state(reg)
            if observed != result:
                raise AssertionError((schedule, step, identifier, observed, result))

    # Dispose every buffered value exactly once; no pending registrations are
    # permitted to hide ownership when the last channel handle disappears.
    channel.close()
    model.action('close')
    released: list[int] = []
    channel.release_handle(released.append)
    if not channel.destroyed or released != list(model.buffer):
        raise AssertionError((schedule, capacity, released, list(model.buffer)))


@dataclass(frozen=True)
class ChannelScheduleReport:
    orders: int
    capacities: tuple[int, ...]
    executions: int


def explore_channel_orders(capacities=(0, 1, 2), max_orders=5040) -> ChannelScheduleReport:
    """Explore the entire bounded universe, or reject an insufficient budget."""
    required = 5040
    if max_orders < required:
        raise ValueError('schedule limit too small for exhaustive 7-operation universe')
    for capacity in capacities:
        if capacity not in (0, 1, 2):
            raise ValueError('unsupported channel model capacity')
        for schedule in permutations(OPERATIONS):
            _replay(schedule, capacity)
    return ChannelScheduleReport(required, tuple(capacities), required * len(capacities))


if __name__ == '__main__':
    report = explore_channel_orders()
    print('PASS', report)
