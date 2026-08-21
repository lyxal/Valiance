import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, optimize_program, run
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program
from valiance.runtime.concurrency import Channel, Scheduler, TaskState
from valiance.runtime.vm import FunctionValue, VirtualMachine


class OptimizerBarrierTests(unittest.TestCase):
    def test_concurrency_operations_survive_default_optimization_in_order(self):
        operations = (
            OpCode.SCOPE_BEGIN,
            OpCode.SPAWN_CALL,
            OpCode.WAIT_TASK,
            OpCode.WAIT_TASKS_VECTORISED,
            OpCode.SCOPE_END,
            OpCode.CHANNEL_NEW,
            OpCode.CHANNEL_SEND,
            OpCode.CHANNEL_RECEIVE,
            OpCode.CHANNEL_CLOSE,
            OpCode.CANCEL_POLL,
        )
        program = Program(
            FunctionCode(
                tuple(Instruction(operation) for operation in operations)
                + (Instruction(OpCode.RETURN),),
                name="<barriers>",
            )
        )
        optimized = optimize_program(program)
        retained = tuple(
            instruction.op
            for instruction in optimized.main.instructions
            if instruction.op in operations
        )
        self.assertEqual(retained, operations)


class VectorisedWaitRuntimeTests(unittest.TestCase):
    def test_group_wait_preserves_order_and_lifts_native_outputs(self):
        vm = VirtualMachine(output=lambda _value: None)
        scope = vm.scheduler.root_scope
        first = scope.spawn(lambda: (1, "first"))
        second = scope.spawn(lambda: (2, "second"))
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, [second, first, first]),
                    Instruction(OpCode.WAIT_TASKS_VECTORISED, 2),
                    Instruction(OpCode.RETURN),
                ),
                name="<group-wait>",
            )
        )
        self.assertEqual(
            vm.run(program),
            [[2, 1, 1], ["second", "first", "first"]],
        )

    def test_group_wait_chooses_failure_by_input_order(self):
        vm = VirtualMachine(output=lambda _value: None)
        scope = vm.scheduler.root_scope
        first = scope.spawn(lambda: (_ for _ in ()).throw(ValueError("first")))
        second = scope.spawn(lambda: (_ for _ in ()).throw(ValueError("second")))
        with self.assertRaisesRegex(Exception, "first"):
            vm.scheduler.wait_all([first, second])


class ScopeUnwindTests(unittest.TestCase):
    def test_body_failure_cancels_and_joins_root_children(self):
        vm = VirtualMachine(output=lambda _value: None)
        child = vm.scheduler.root_scope.spawn(lambda: (1,))
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, "boom"),
                    Instruction(OpCode.PANIC),
                    Instruction(OpCode.RETURN),
                ),
                name="<body-failure>",
            )
        )
        with self.assertRaisesRegex(Exception, "boom"):
            vm.run(program)
        self.assertTrue(child.control.state.terminal)


class ChannelScopeCancellationTests(unittest.TestCase):
    def test_sibling_failure_removes_blocked_receiver_registration(self):
        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[int]()
        receiver_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.CHANNEL_RECEIVE),
                Instruction(OpCode.RETURN),
            ),
            name="<blocked-receiver>",
            return_count=1,
        )
        receiver = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(receiver_code, {**vm.globals, "channel": channel}), (), 0
            )
        )
        failure = vm.scheduler.root_scope.spawn(
            lambda: (_ for _ in ()).throw(ValueError("sibling failed"))
        )

        with self.assertRaisesRegex(ValueError, "sibling failed"):
            vm.scheduler.root_scope.close()

        self.assertEqual(receiver.control.state, TaskState.CANCELLED)
        self.assertEqual(failure.control.state, TaskState.FAILED)
        self.assertEqual(list(channel.receivers), [])

    def test_sibling_failure_removes_blocked_bounded_sender_registration(self):
        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[int](1)
        producer_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.PUSH_CONST, 1),
                Instruction(OpCode.CHANNEL_SEND),
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.PUSH_CONST, 2),
                Instruction(OpCode.CHANNEL_SEND),
                Instruction(OpCode.RETURN),
            ),
            name="<blocked-producer>",
            return_count=0,
        )
        producer = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(producer_code, {**vm.globals, "channel": channel}), (), 0
            )
        )
        failure = vm.scheduler.root_scope.spawn(
            lambda: (_ for _ in ()).throw(ValueError("sibling failed"))
        )

        with self.assertRaisesRegex(ValueError, "sibling failed"):
            vm.scheduler.root_scope.close()

        self.assertEqual(producer.control.state, TaskState.CANCELLED)
        self.assertEqual(failure.control.state, TaskState.FAILED)
        self.assertEqual(list(channel.buffer), [1])
        self.assertEqual(list(channel.senders), [])


class ConcurrencyStressTests(unittest.TestCase):
    def test_bounded_channel_stress_preserves_every_value_in_order(self):
        channel = Channel[int](7)
        expected = list(range(1000))
        received = []
        next_send = 0
        while len(received) < len(expected):
            while next_send < len(expected) and channel.try_send(next_send):
                next_send += 1
            result = channel.try_receive()
            self.assertIsNotNone(result)
            received.append(result.value)
            if next_send < len(expected):
                next_send += 1
        self.assertEqual(received, expected)
        self.assertEqual(list(channel.buffer), [])
        self.assertEqual(list(channel.senders), [])

    def test_many_tasks_and_aliases_complete_once(self):
        scheduler = Scheduler()
        executions = [0] * 250
        handles = []
        for index in range(len(executions)):
            def thunk(index=index):
                executions[index] += 1
                return (index,)
            handles.append(scheduler.root_scope.spawn(thunk))
        aliased = [handle for handle in handles for _ in range(3)]
        rows = scheduler.wait_all(aliased)
        self.assertEqual(rows, [(index,) for index in range(250) for _ in range(3)])
        self.assertEqual(executions, [1] * 250)

    def test_nested_concurrency_survives_optimization_and_serialization(self):
        source = """$outer = fn -> Int =>
  concurrent -> Int =>
    $child = fn -> Int => 7 end | spawn
    $child wait
  end
end
$task = $outer spawn
$task wait"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        expected = [7]
        optimized = compile_program(typed, optimize=True)
        self.assertEqual(run(optimized), expected)
        self.assertEqual(run(loads(dumps(optimized))), expected)


class ResumableScopeCloseTests(unittest.TestCase):
    def test_scope_end_blocks_owner_without_recursively_driving_child(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 64
        child_code = FunctionCode(
            tuple(
                instruction
                for value in range(80)
                for instruction in (
                    Instruction(OpCode.PUSH_CONST, value),
                    Instruction(OpCode.POP),
                )
            )
            + (
                Instruction(OpCode.PUSH_CONST, 1),
                Instruction(OpCode.RETURN),
            ),
            name="<slow-scope-child>",
            return_count=1,
        )
        owner_code = FunctionCode(
            (
                Instruction(OpCode.SCOPE_BEGIN, (0, 1)),
                Instruction(OpCode.MAKE_FUNCTION, child_code),
                Instruction(OpCode.SPAWN_CALL, (0, 1, 0)),
                Instruction(OpCode.POP),
                Instruction(OpCode.PUSH_CONST, 9),
                Instruction(OpCode.SCOPE_END, (0, 1)),
                Instruction(OpCode.RETURN),
            ),
            name="<scope-owner>",
            return_count=1,
        )
        owner = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(owner_code, vm.globals), (), 0
            )
        )

        self.assertTrue(vm.scheduler.step())
        child = next(
            task for task in vm.scheduler.tasks.values() if task is not owner.control
        )
        self.assertFalse(child.state.terminal)
        self.assertEqual(owner.control.blocked_reason, "scope close")
        self.assertNotIn(owner.control, vm.scheduler.runnable)
        self.assertEqual(vm.scheduler.wait(owner), (9,))
        self.assertTrue(child.state.terminal)

    def test_child_failure_interrupts_cpu_owner_at_next_quantum(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 8
        child_code = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, "child failed"),
                Instruction(OpCode.PANIC),
                Instruction(OpCode.RETURN),
            ),
            name="<failing-child>",
        )
        cpu_work = tuple(
            instruction
            for value in range(100)
            for instruction in (
                Instruction(OpCode.PUSH_CONST, value),
                Instruction(OpCode.POP),
            )
        )
        owner_code = FunctionCode(
            (
                Instruction(OpCode.SCOPE_BEGIN, (0, 1)),
                Instruction(OpCode.MAKE_FUNCTION, child_code),
                Instruction(OpCode.SPAWN_CALL, (0, 0, 0)),
                Instruction(OpCode.POP),
            )
            + cpu_work
            + (
                Instruction(OpCode.PUSH_CONST, 99),
                Instruction(OpCode.SCOPE_END, (0, 1)),
                Instruction(OpCode.RETURN),
            ),
            name="<cpu-owner>",
            return_count=1,
        )
        owner = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(owner_code, vm.globals), (), 0
            )
        )

        self.assertTrue(vm.scheduler.step())
        self.assertTrue(vm.scheduler.step())
        with self.assertRaisesRegex(Exception, "child failed"):
            vm.scheduler.wait(owner)
        self.assertEqual(owner.control.state, TaskState.FAILED)
        self.assertLess(owner.control.quantum_count, 10)


class SchedulerExternalWakeTests(unittest.TestCase):
    def test_timer_wakes_blocked_work_while_other_task_progresses(self):
        scheduler = Scheduler()
        events = []
        scheduler.root_scope.spawn(lambda: (events.append("sibling"),)[1:])
        scheduler.register_timer(5, lambda: events.append("timer"))
        scheduler.run_until(lambda: "timer" in events)
        self.assertEqual(events, ["sibling", "timer"])
        self.assertEqual(scheduler.logical_time, 5)

    def test_timer_and_external_cancellation_are_exactly_once(self):
        scheduler = Scheduler()
        events = []
        timer = scheduler.register_timer(1, lambda: events.append("timer"))
        external = scheduler.register_external(lambda: events.append("external"))
        self.assertTrue(timer.cancel())
        self.assertFalse(timer.cancel())
        self.assertTrue(external.fire())
        self.assertFalse(external.fire())
        self.assertFalse(external.cancel())
        self.assertEqual(events, ["external"])
        self.assertFalse(scheduler.has_pending_external_wake)


    def test_host_blocking_external_call_is_rejected_before_execution(self):
        from valiance.runtime.concurrency import ExternalCallMode, ExternalCallPolicy

        policy = ExternalCallPolicy(ExternalCallMode.HOST_BLOCKING)
        with self.assertRaisesRegex(Exception, "host-blocking external call"):
            policy.validate_concurrent()

    def test_pending_external_source_is_not_reported_as_deadlock(self):
        scheduler = Scheduler()
        scheduler.register_external(lambda: None)
        with self.assertRaisesRegex(RuntimeError, "awaits an external wake source"):
            scheduler.run_until(lambda: False)



if __name__ == "__main__":
    unittest.main()
