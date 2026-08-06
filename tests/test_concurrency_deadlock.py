import unittest

from valiance.runtime import DeadlockFault, Scheduler
from valiance.runtime.concurrency import TaskHandle


class DeadlockDetectionTests(unittest.TestCase):
    def test_self_wait_is_rejected_immediately(self):
        scheduler = Scheduler()
        box: dict[str, TaskHandle] = {}
        task = scheduler.root_scope.spawn(lambda: scheduler.wait(box["task"]))
        box["task"] = task
        with self.assertRaisesRegex(DeadlockFault, "cannot wait on itself") as caught:
            scheduler.wait(task)
        self.assertEqual(caught.exception.cycle, (task.id, task.id))

    def test_two_task_wait_cycle_reports_stable_cycle(self):
        scheduler = Scheduler()
        box: dict[str, TaskHandle] = {}
        first = scheduler.root_scope.spawn(lambda: scheduler.wait(box["second"]))
        second = scheduler.root_scope.spawn(lambda: scheduler.wait(box["first"]))
        box.update(first=first, second=second)
        with self.assertRaisesRegex(DeadlockFault, "task wait cycle") as caught:
            scheduler.wait(first)
        self.assertEqual(caught.exception.cycle, (first.id, second.id, first.id))

    def test_wait_chain_without_cycle_completes(self):
        scheduler = Scheduler()
        leaf = scheduler.root_scope.spawn(lambda: (7,))
        parent = scheduler.root_scope.spawn(lambda: scheduler.wait(leaf))
        self.assertEqual(scheduler.wait(parent), (7,))

    def test_no_runnable_tasks_reports_wait_edges(self):
        scheduler = Scheduler()
        scheduler.waiting_on_task[1] = 2
        with self.assertRaisesRegex(
            DeadlockFault, "task 1 waits for task 2"
        ):
            scheduler.run_until(lambda: False)

    def test_blocked_group_wait_reports_each_member_in_stable_order(self):
        from valiance.runtime.concurrency import TaskBlocked, TaskYield, WaitDependency

        scheduler = Scheduler()

        def blocked_group():
            yield TaskBlocked(
                "wait task group",
                dependencies=(
                    WaitDependency("task", 9, "group wait"),
                    WaitDependency("task", 7, "group wait"),
                ),
            )
            yield TaskYield()

        owner = scheduler.root_scope.spawn(blocked_group)
        self.assertTrue(scheduler.step())
        with self.assertRaises(DeadlockFault) as caught:
            scheduler.run_until(lambda: owner.control.state.terminal)
        self.assertEqual(
            str(caught.exception),
            "deadlock: "
            f"task {owner.id} waits for task 7, "
            f"task {owner.id} waits for task 9",
        )

    def test_blocked_channel_operations_render_resource_and_operation(self):
        from valiance.runtime import Channel
        from valiance.runtime.concurrency import TaskBlocked, WaitDependency

        scheduler = Scheduler()
        channel = Channel[int]()

        def blocked_send():
            yield TaskBlocked(
                "channel send",
                dependencies=(WaitDependency("channel", channel.id, "send"),),
            )

        sender = scheduler.root_scope.spawn(blocked_send)
        self.assertTrue(scheduler.step())
        with self.assertRaisesRegex(
            DeadlockFault,
            rf"task {sender.id} waits for channel {channel.id} send",
        ):
            scheduler.run_until(lambda: sender.control.state.terminal)

    def test_channel_resource_cycle_is_reported_with_stable_nodes(self):
        from valiance.runtime import Channel
        from valiance.runtime.concurrency import TaskBlocked, WaitDependency

        scheduler = Scheduler()
        channel = Channel[int]()

        def blocked_receive():
            yield TaskBlocked(
                "channel receive",
                dependencies=(
                    WaitDependency("channel", channel.id, "receive"),
                ),
            )

        receiver = scheduler.root_scope.spawn(blocked_receive)
        self.assertTrue(scheduler.step())
        with self.assertRaises(DeadlockFault) as caught:
            scheduler.run_until(lambda: receiver.control.state.terminal)
        self.assertEqual(
            caught.exception.cycle,
            (
                f"task {receiver.id}",
                f"channel {channel.id} receive",
                f"task {receiver.id}",
            ),
        )

    def test_mixed_task_and_channel_stall_reports_resource_cycle(self):
        from valiance.runtime import Channel
        from valiance.runtime.concurrency import TaskBlocked, WaitDependency

        scheduler = Scheduler()
        channel = Channel[int]()
        ids: dict[str, int] = {}

        def waits_for_receiver():
            yield TaskBlocked(
                "task wait",
                dependencies=(WaitDependency("task", ids["receiver"]),),
            )

        def blocked_receiver():
            yield TaskBlocked(
                "channel receive",
                dependencies=(
                    WaitDependency("channel", channel.id, "receive"),
                ),
            )

        waiter = scheduler.root_scope.spawn(waits_for_receiver)
        receiver = scheduler.root_scope.spawn(blocked_receiver)
        ids["receiver"] = receiver.id
        self.assertTrue(scheduler.step())
        self.assertTrue(scheduler.step())
        with self.assertRaises(DeadlockFault) as caught:
            scheduler.run_until(lambda: waiter.control.state.terminal)
        message = str(caught.exception)
        self.assertIn(f"task {waiter.id} waits for task {receiver.id}", message)
        self.assertIn(f"channel {channel.id} receive", message)
        self.assertEqual(
            caught.exception.cycle,
            (
                f"task {receiver.id}",
                f"channel {channel.id} receive",
                f"task {receiver.id}",
            ),
        )

    def test_scope_close_task_edges_participate_in_cycle_detection(self):
        from valiance.runtime.concurrency import TaskBlocked, WaitDependency

        scheduler = Scheduler()
        ids: dict[str, int] = {}

        def first_runner():
            yield TaskBlocked(
                "scope close",
                dependencies=(
                    WaitDependency("task", ids["second"], "scope close"),
                ),
            )

        def second_runner():
            yield TaskBlocked(
                "task wait",
                dependencies=(WaitDependency("task", ids["first"]),),
            )

        first = scheduler.root_scope.spawn(first_runner)
        second = scheduler.root_scope.spawn(second_runner)
        ids.update(first=first.id, second=second.id)
        self.assertTrue(scheduler.step())
        self.assertTrue(scheduler.step())
        with self.assertRaises(DeadlockFault) as caught:
            scheduler.run_until(lambda: first.control.state.terminal)
        self.assertEqual(
            caught.exception.cycle,
            (first.id, second.id, first.id),
        )


if __name__ == "__main__":
    unittest.main()
