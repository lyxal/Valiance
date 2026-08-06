"""Bounded CI gates for concurrency fuzzing, stress, leaks, and benchmarks."""

from __future__ import annotations

import gc
import unittest
import weakref

from tools.concurrency_fuzzing import ConcurrencyFuzzConfig, run_concurrency_fuzz
from valiance.runtime.concurrency import Channel, Scheduler, TaskState, TaskYield


class DeterministicConcurrencyFuzzTests(unittest.TestCase):
    def test_bounded_campaign_and_single_case_replay_match(self):
        run_concurrency_fuzz(ConcurrencyFuzzConfig(seed=87, iterations=100, operations=48))
        run_concurrency_fuzz(
            ConcurrencyFuzzConfig(seed=87, start=99, iterations=1, operations=48)
        )


class ConcurrencyStressAndLeakTests(unittest.TestCase):
    def test_large_runnable_queue_is_fair_and_drains(self):
        scheduler = Scheduler()
        order: list[int] = []

        def runner(identifier: int):
            """Record entry, yield once, and record completion."""
            order.append(identifier)
            yield TaskYield("fairness")
            order.append(identifier)
            return ()

        handles = [
            scheduler.root_scope.spawn(lambda i=index: runner(i)) for index in range(2_000)
        ]
        scheduler.run_until(lambda: all(item.control.state.terminal for item in handles))
        self.assertEqual(order[:2_000], list(range(2_000)))
        self.assertEqual(sorted(order[2_000:]), list(range(2_000)))
        self.assertFalse(scheduler.runnable)

    def test_cancelled_sender_and_receiver_storm_cleans_registrations(self):
        channel = Channel[int]()
        senders = [channel.register_send(index) for index in range(5_000)]
        self.assertEqual(len(channel.senders), 5_000)
        for registration in senders:
            self.assertIsNotNone(registration)
            channel.cancel_send(registration)
        receivers = [channel.register_receive() for _ in range(5_000)]
        for registration in receivers:
            channel.cancel_receive(registration)
        self.assertFalse(channel.senders)
        self.assertFalse(channel.receivers)
        self.assertEqual(channel.cancelled_sends, 5_000)
        self.assertEqual(channel.cancelled_receives, 5_000)

    def test_repeated_batches_release_tasks_and_channels(self):
        references: list[weakref.ReferenceType[object]] = []
        for _ in range(20):
            scheduler = Scheduler()
            handles = [scheduler.root_scope.spawn(lambda: ()) for _ in range(200)]
            scheduler.root_scope.close()
            for handle in handles:
                handle.control.release_handle(lambda _outputs, _fault: None)
            channel = Channel[int](8)
            references.append(weakref.ref(channel))
            channel.release_handle(lambda _value: None)
            self.assertTrue(channel.destroyed)
            self.assertTrue(all(task.state is TaskState.COMPLETED for task in scheduler.tasks.values()) or not scheduler.tasks)
            del channel, handles, scheduler
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))

    def test_long_bounded_fifo_has_no_loss_or_duplicates(self):
        channel = Channel[int](32)
        output: list[int] = []
        for value in range(20_000):
            registration = channel.register_send(value)
            if registration is not None:
                result = channel.try_receive()
                self.assertIsNotNone(result)
                output.append(result.value)
        channel.close()
        while True:
            result = channel.try_receive()
            if result is None or result.closed:
                break
            output.append(result.value)
        self.assertEqual(output, list(range(20_000)))


if __name__ == "__main__":
    unittest.main()
