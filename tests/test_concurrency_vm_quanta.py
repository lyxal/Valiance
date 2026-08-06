import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode
from valiance.runtime.runtime_values import ListValue, RuntimeNumber
from valiance.runtime.vm import FunctionValue, VirtualMachine


class VmActivationQuantumTests(unittest.TestCase):
    def test_task_local_activation_yields_by_instruction_budget(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 2
        code = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(1)),
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(2)),
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(3)),
                Instruction(OpCode.RETURN),
            ),
            name="<budgeted>",
            return_count=3,
        )
        task = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(FunctionValue(code, vm.globals), (), 0)
        )
        self.assertEqual(vm.scheduler.wait(task), (
            RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)
        ))
        self.assertGreaterEqual(task.control.quantum_count, 1)

    def test_cancel_poll_is_explicit_yield_boundary(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 100
        code = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(1)),
                Instruction(OpCode.CANCEL_POLL),
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(2)),
                Instruction(OpCode.RETURN),
            ),
            name="<polling>",
            return_count=2,
        )
        task = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(FunctionValue(code, vm.globals), (), 0)
        )
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(task.control.quantum_count, 1)
        task.control.request_cancel()
        self.assertTrue(vm.scheduler.step())
        self.assertTrue(task.control.state.terminal)

    def test_source_spawn_uses_resumable_activation_runner(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("fn -> Integer => 42 end | spawn | wait"))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 1
        self.assertEqual(
            vm.run(compile_program(typed, optimize=False)),
            [RuntimeNumber(42)],
        )

    def test_nested_user_calls_survive_quantum_suspension(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 1
        child = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(5)),
                Instruction(OpCode.RETURN),
            ),
            name="<child>",
            return_count=1,
        )
        parent = FunctionCode(
            (
                Instruction(OpCode.MAKE_FUNCTION, child),
                Instruction(OpCode.CALL),
                Instruction(OpCode.RETURN),
            ),
            name="<parent>",
            return_count=1,
        )
        task = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(FunctionValue(parent, vm.globals), (), 0)
        )
        self.assertEqual(vm.scheduler.wait(task), (RuntimeNumber(5),))
        self.assertGreaterEqual(task.control.quantum_count, 2)

    def test_cancellation_releases_suspended_operand_stack(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 100
        owned = ListValue([RuntimeNumber(1)])
        code = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, owned),
                Instruction(OpCode.CANCEL_POLL),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-stack-cleanup>",
            return_count=1,
        )
        task = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(code, dict(vm.globals)), (), 0
            )
        )
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(owned.refcount, 1)
        task.control.request_cancel()
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(task.control.state.name, "CANCELLED")
        self.assertEqual(owned.refcount, 0)

    def test_cancellation_releases_every_nested_activation_frame(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 1
        parent_owned = ListValue([RuntimeNumber(1)])
        child_owned = ListValue([RuntimeNumber(2)])
        child = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, child_owned),
                Instruction(OpCode.CANCEL_POLL),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-child-frame>",
            return_count=1,
        )
        parent = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, parent_owned),
                Instruction(OpCode.MAKE_FUNCTION, child),
                Instruction(OpCode.CALL),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-parent-frame>",
            return_count=2,
        )
        task = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(parent, dict(vm.globals)), (), 0
            )
        )
        for _ in range(4):
            self.assertTrue(vm.scheduler.step())
            if child_owned.refcount == 1 and task.control.quantum_count >= 3:
                break
        task.control.request_cancel()
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(parent_owned.refcount, 0)
        self.assertEqual(child_owned.refcount, 0)

    def test_cancelled_task_records_cleanup_failure_as_secondary(self):
        from valiance.runtime import TaskBlocked, TaskState

        scheduler = __import__(
            "valiance.runtime.concurrency", fromlist=["Scheduler"]
        ).Scheduler()

        def runner():
            try:
                yield TaskBlocked("blocked")
            finally:
                raise ValueError("cleanup failed")

        task = scheduler.root_scope.spawn(runner)
        self.assertTrue(scheduler.step())
        task.control.request_cancel()
        self.assertTrue(scheduler.step())
        self.assertEqual(task.control.state, TaskState.CANCELLED)
        self.assertEqual(len(task.control.terminal_fault.secondary_faults), 1)
        self.assertRegex(
            str(task.control.terminal_fault.secondary_faults[0]), "cleanup failed"
        )

    def test_cancellation_releases_transferred_callable_occurrence(self):
        vm = VirtualMachine(output=lambda _value: None)
        code = FunctionCode(
            (
                Instruction(OpCode.CANCEL_POLL),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-callable-cleanup>",
            return_count=0,
        )
        callable_value = FunctionValue(code, dict(vm.globals))
        task = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(callable_value, (), 0)
        )
        self.assertTrue(vm.scheduler.step())
        task.control.request_cancel()
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(callable_value.refcount, 0)


if __name__ == "__main__":
    unittest.main()
