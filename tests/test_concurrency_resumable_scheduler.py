import unittest

from valiance.runtime import CancelledFault, Scheduler, TaskState, TaskYield


class ResumableSchedulerTests(unittest.TestCase):
    def test_fifo_round_robin_between_resumable_tasks(self):
        scheduler = Scheduler()
        events: list[str] = []

        def runner(name: str):
            events.append(f"{name}:1")
            yield TaskYield()
            events.append(f"{name}:2")
            yield TaskYield()
            events.append(f"{name}:3")
            return (name,)

        first = scheduler.root_scope.spawn(lambda: runner("first"))
        second = scheduler.root_scope.spawn(lambda: runner("second"))
        scheduler.root_scope.close()
        self.assertEqual(
            events,
            [
                "first:1", "second:1",
                "first:2", "second:2",
                "first:3", "second:3",
            ],
        )
        self.assertEqual(first.result(), ("first",))
        self.assertEqual(second.result(), ("second",))
        self.assertEqual(first.control.quantum_count, 2)

    def test_scalar_wait_drives_resumable_target_to_completion(self):
        scheduler = Scheduler()

        def runner():
            yield TaskYield("first quantum")
            yield TaskYield("second quantum")
            return (9,)

        task = scheduler.root_scope.spawn(runner)
        self.assertEqual(scheduler.wait(task), (9,))
        self.assertEqual(task.control.state, TaskState.COMPLETED)

    def test_cancellation_is_observed_at_yield_boundary(self):
        scheduler = Scheduler()
        events: list[str] = []

        def runner():
            events.append("started")
            yield TaskYield()
            events.append("should not run")
            return ()

        task = scheduler.root_scope.spawn(runner)
        self.assertTrue(scheduler.step())
        task.control.request_cancel()
        self.assertTrue(scheduler.step())
        self.assertEqual(task.control.state, TaskState.CANCELLED)
        self.assertEqual(events, ["started"])
        with self.assertRaises(CancelledFault):
            task.result()

    def test_invalid_runner_yield_fails_task(self):
        scheduler = Scheduler()

        def invalid():
            yield "not a suspension marker"
            return ()

        task = scheduler.root_scope.spawn(invalid)
        with self.assertRaisesRegex(Exception, "TaskYield"):
            scheduler.wait(task)
        self.assertEqual(task.control.state, TaskState.FAILED)

    def test_atomic_thunks_remain_compatible(self):
        scheduler = Scheduler()
        task = scheduler.root_scope.spawn(lambda: (1, 2))
        self.assertEqual(scheduler.wait(task), (1, 2))
        self.assertEqual(task.control.quantum_count, 0)


if __name__ == "__main__":
    unittest.main()
