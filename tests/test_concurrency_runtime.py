import unittest

from valiance.runtime.concurrency import (
    CancelledFault,
    Channel,
    ClosedFault,
    Receive,
    Scheduler,
    TaskState,
)


class TaskRuntimeTests(unittest.TestCase):
    def test_repeatable_wait_and_alias_execute_once(self):
        scheduler = Scheduler()
        calls = []
        task = scheduler.root_scope.spawn(lambda: (calls.append("run") or 42,))
        alias = task
        self.assertEqual(scheduler.wait(task), (42,))
        self.assertEqual(scheduler.wait(alias), (42,))
        self.assertEqual(calls, ["run"])

    def test_scope_joins_discarded_children(self):
        scheduler = Scheduler()
        calls = []
        scheduler.root_scope.spawn(lambda: (calls.append(1),))
        scheduler.root_scope.close()
        self.assertEqual(calls, [1])

    def test_primary_failure_is_spawn_order_not_completion_order(self):
        scheduler = Scheduler()
        scope = scheduler.root_scope
        first = scope.spawn(lambda: (_ for _ in ()).throw(ValueError("first")))
        second = scope.spawn(lambda: (_ for _ in ()).throw(ValueError("second")))
        with self.assertRaisesRegex(ValueError, "first"):
            scope.close()
        self.assertEqual(first.control.state, TaskState.FAILED)
        self.assertEqual(second.control.state, TaskState.CANCELLED)

    def test_cancelled_wait_has_fault(self):
        scheduler = Scheduler()
        task = scheduler.root_scope.spawn(lambda: (1,))
        task.control.request_cancel()
        with self.assertRaises(CancelledFault):
            scheduler.wait(task)

    def test_scope_attaches_later_spawn_failures_as_secondary_context(self):
        scheduler = Scheduler()
        scope = scheduler.root_scope
        first = ValueError("first")
        second = KeyError("second")
        scope.child_faults.extend(((1, second), (0, first)))
        with self.assertRaises(ValueError) as caught:
            scope.close()
        self.assertIs(caught.exception, first)
        self.assertEqual(caught.exception.secondary_faults, (second,))
        self.assertTrue(
            any("secondary child task failure" in note for note in caught.exception.__notes__)
        )

    def test_body_fault_remains_primary_and_children_are_secondary(self):
        scheduler = Scheduler()
        scope = scheduler.root_scope
        body = RuntimeError("body")
        child = ValueError("child")
        scope.child_faults.append((0, child))
        with self.assertRaises(RuntimeError) as caught:
            scope.close(body)
        self.assertIs(caught.exception, body)
        self.assertEqual(caught.exception.secondary_faults, (child,))

    def test_group_wait_attaches_other_failures_in_input_order(self):
        scheduler = Scheduler()
        scope = scheduler.root_scope
        first = scope.spawn(lambda: (1,))
        second = scope.spawn(lambda: (2,))
        first_fault = ValueError("first")
        second_fault = KeyError("second")
        first.control.state = TaskState.FAILED
        first.control.terminal_fault = first_fault
        second.control.state = TaskState.FAILED
        second.control.terminal_fault = second_fault
        scheduler.runnable.clear()
        with self.assertRaises(ValueError) as caught:
            scheduler.wait_all([first, second])
        self.assertIs(caught.exception, first_fault)
        self.assertEqual(caught.exception.secondary_faults, (second_fault,))

    def test_group_wait_cancels_unfinished_member_after_failure(self):
        from valiance.runtime.concurrency import TaskScope, TaskYield

        scheduler = Scheduler()
        failing_scope = TaskScope(scheduler)
        running_scope = TaskScope(scheduler)
        failing = failing_scope.spawn(
            lambda: (_ for _ in ()).throw(ValueError("group failed"))
        )

        def runs_forever():
            while True:
                yield TaskYield()

        unfinished = running_scope.spawn(runs_forever)
        with self.assertRaisesRegex(ValueError, "group failed"):
            scheduler.wait_all([failing, unfinished])
        self.assertEqual(failing.control.state, TaskState.FAILED)
        self.assertEqual(unfinished.control.state, TaskState.CANCELLED)

    def test_group_wait_alias_failure_cancels_entity_once(self):
        from valiance.runtime.concurrency import TaskScope, TaskYield

        scheduler = Scheduler()
        failing_scope = TaskScope(scheduler)
        running_scope = TaskScope(scheduler)
        failing = failing_scope.spawn(
            lambda: (_ for _ in ()).throw(ValueError("alias failed"))
        )

        def runs_forever():
            while True:
                yield TaskYield()

        unfinished = running_scope.spawn(runs_forever)
        with self.assertRaisesRegex(ValueError, "alias failed"):
            scheduler.wait_all([failing, failing, unfinished, unfinished])
        self.assertEqual(unfinished.control.state, TaskState.CANCELLED)

    def test_task_terminal_outputs_release_after_scope_and_final_handle(self):
        from valiance.runtime.runtime_values import ListValue, RuntimeNumber
        from valiance.runtime.vm import VirtualMachine, _release_value

        vm = VirtualMachine(output=lambda _value: None)
        output = ListValue([RuntimeNumber(1)])
        task = vm.scheduler.root_scope.spawn(lambda: (output,))
        self.assertEqual(vm.scheduler.wait(task), (output,))
        task_id = task.id
        vm.scheduler.root_scope.close()
        self.assertEqual(output.refcount, 1)
        self.assertIn(task_id, vm.scheduler.tasks)
        _release_value(task, vm)
        self.assertEqual(output.refcount, 0)
        self.assertEqual(task.control.terminal_outputs, ())
        self.assertTrue(task.control.destroyed)
        self.assertNotIn(task_id, vm.scheduler.tasks)

    def test_task_scope_releases_child_but_visible_alias_keeps_outcome(self):
        from valiance.runtime.runtime_values import ListValue, RuntimeNumber
        from valiance.runtime.vm import VirtualMachine, _release_value, _retain_runtime_reference

        vm = VirtualMachine(output=lambda _value: None)
        output = ListValue([RuntimeNumber(2)])
        task = vm.scheduler.root_scope.spawn(lambda: (output,))
        alias = _retain_runtime_reference(task)
        vm.scheduler.root_scope.close()
        _release_value(task, vm)
        self.assertFalse(alias.control.destroyed)
        self.assertEqual(alias.result(), (output,))
        _release_value(alias, vm)
        self.assertTrue(alias.control.destroyed)
        self.assertEqual(output.refcount, 0)

    def test_repeatable_task_observation_owns_each_returned_occurrence(self):
        from valiance.runtime.runtime_values import ListValue, RuntimeNumber
        from valiance.runtime.vm import VirtualMachine, _release_value

        vm = VirtualMachine(output=lambda _value: None)
        output = ListValue([RuntimeNumber(3)])
        task = vm.scheduler.root_scope.spawn(lambda: (output,))
        vm.scheduler.wait(task)
        first = vm._retain_task_row(task.result())[0]
        second = vm._retain_task_row(task.result())[0]
        self.assertEqual(output.refcount, 3)
        _release_value(first, vm)
        _release_value(second, vm)
        self.assertEqual(output.refcount, 1)
        vm.scheduler.root_scope.close()
        _release_value(task, vm)
        self.assertEqual(output.refcount, 0)


class ChannelRuntimeTests(unittest.TestCase):
    def test_bounded_fifo_and_backpressure(self):
        channel = Channel[int](2)
        self.assertTrue(channel.try_send(1))
        self.assertTrue(channel.try_send(2))
        self.assertFalse(channel.try_send(3))
        self.assertEqual(channel.try_receive(), Receive.Value(1))
        self.assertEqual(channel.try_receive(), Receive.Value(2))
        self.assertEqual(channel.try_receive(), Receive.Value(3))

    def test_unbuffered_rendezvous(self):
        channel = Channel[str]()
        self.assertFalse(channel.try_send("message"))
        self.assertEqual(channel.try_receive(), Receive.Value("message"))

    def test_close_drains_then_reports_closed(self):
        channel = Channel[object](2)
        channel.try_send(None)
        channel.try_send("last")
        channel.close()
        channel.close()
        self.assertEqual(channel.try_receive(), Receive.Value(None))
        self.assertEqual(channel.try_receive(), Receive.Value("last"))
        self.assertEqual(channel.try_receive(), Receive.Closed())
        with self.assertRaises(ClosedFault):
            channel.try_send("late")

    def test_capacity_validation(self):
        for value in (-1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Channel(value)

    def test_final_channel_handle_releases_buffered_values(self):
        from valiance.runtime.runtime_values import ListValue, RuntimeNumber
        from valiance.runtime.vm import VirtualMachine, _release_value

        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[ListValue](1)
        value = ListValue([RuntimeNumber(4)])
        self.assertTrue(channel.try_send(value))
        _release_value(channel, vm)
        self.assertTrue(channel.destroyed)
        self.assertEqual(value.refcount, 0)
        self.assertEqual(list(channel.buffer), [])

    def test_channel_alias_preserves_entity_until_final_release(self):
        from valiance.runtime.runtime_values import ListValue, RuntimeNumber
        from valiance.runtime.vm import VirtualMachine, _release_value, _retain_runtime_reference

        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[ListValue](1)
        alias = _retain_runtime_reference(channel)
        value = ListValue([RuntimeNumber(5)])
        channel.try_send(value)
        _release_value(channel, vm)
        self.assertFalse(alias.destroyed)
        self.assertEqual(alias.try_receive(), Receive.Value(value))
        _release_value(value, vm)
        _release_value(alias, vm)
        self.assertTrue(alias.destroyed)

    def test_receive_value_releases_transmitted_ownership(self):
        from valiance.runtime.runtime_values import ListValue, RuntimeNumber
        from valiance.runtime.vm import VirtualMachine, _release_value

        vm = VirtualMachine(output=lambda _value: None)
        value = ListValue([RuntimeNumber(6)])
        received = Receive.Value(value)
        _release_value(received, vm)
        self.assertEqual(value.refcount, 0)

    def test_blocked_registration_keeps_channel_entity_alive(self):
        from valiance.runtime.runtime_values import ListValue, RuntimeNumber
        from valiance.runtime.vm import VirtualMachine, _release_value

        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[ListValue]()
        value = ListValue([RuntimeNumber(7)])
        registration = channel.register_send(value)
        self.assertIsNotNone(registration)
        _release_value(channel, vm)
        self.assertFalse(channel.destroyed)
        self.assertTrue(channel.cancel_send(registration))
        self.assertTrue(channel.destroyed)
        self.assertEqual(value.refcount, 1)
        _release_value(value, vm)

    def test_close_releases_registration_entity_ownership(self):
        from valiance.runtime.concurrency import ChannelReceiver
        from valiance.runtime.vm import VirtualMachine, _release_value

        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[int]()
        registration = channel.register_receive()
        self.assertIsInstance(registration, ChannelReceiver)
        _release_value(channel, vm)
        self.assertFalse(channel.destroyed)
        channel.close()
        self.assertTrue(channel.destroyed)
        self.assertEqual(registration.result, Receive.Closed())


if __name__ == "__main__":
    unittest.main()


class TaskDiagnosticContextTests(unittest.TestCase):
    def test_failed_task_records_creation_scope_and_observation_context(self):
        from valiance.runtime.concurrency import render_concurrency_fault
        from valiance.runtime import TaskScope

        scheduler = Scheduler()
        scope = TaskScope(scheduler, creation_site="scope.vlnc:2:1")
        task = scope.spawn(
            lambda: (_ for _ in ()).throw(ValueError("boom")),
            creation_site="task.vlnc:4:3",
        )
        with self.assertRaises(ValueError) as caught:
            scheduler.wait(task, observation_site="main.vlnc:9:5")
        rendered = render_concurrency_fault(caught.exception)
        self.assertIn("task 1 (failed)", rendered)
        self.assertIn("spawned at task.vlnc:4:3", rendered)
        self.assertIn("owned by scope at scope.vlnc:2:1", rendered)
        self.assertIn("observed at main.vlnc:9:5", rendered)

    def test_repeated_wait_keeps_fault_and_updates_observation_site(self):
        scheduler = Scheduler()
        fault = ValueError("stored")
        task = scheduler.root_scope.spawn(
            lambda: (_ for _ in ()).throw(fault), creation_site="1:1"
        )
        for site in ("2:1", "3:1"):
            with self.assertRaises(ValueError) as caught:
                scheduler.wait(task, observation_site=site)
            self.assertIs(caught.exception, fault)
            self.assertEqual(caught.exception.observation_site, site)

    def test_source_locations_survive_optimization_and_serialization(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program, dumps, loads, run

        source = 'fn -> Int => panic ValueFault("boom") end | spawn | wait'
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        observed = []
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            for executable in (program, loads(dumps(program))):
                with self.assertRaises(Exception) as caught:
                    run(executable)
                observed.append(
                    (
                        caught.exception.task_context,
                        caught.exception.observation_site,
                    )
                )
        self.assertTrue(all(item == observed[0] for item in observed))
        self.assertIn("spawned at 1:45", observed[0][0])
        self.assertEqual(observed[0][1], "1:53")
