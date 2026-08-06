import unittest

from valiance.runtime import TaskState
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode
from valiance.runtime.runtime_values import RuntimeNumber
from valiance.runtime.vm import FunctionValue, VirtualMachine


class VectorisedWaitSuspensionTests(unittest.TestCase):
    def test_group_wait_blocks_once_and_wakes_for_members(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 8

        def producer(value: int) -> FunctionValue:
            return FunctionValue(
                FunctionCode(
                    (
                        Instruction(OpCode.PUSH_CONST, RuntimeNumber(value)),
                        Instruction(OpCode.RETURN),
                    ),
                    name=f"<producer-{value}>",
                    return_count=1,
                ),
                vm.globals,
            )

        first = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(producer(1), (), 0)
        )
        second = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(producer(2), (), 0)
        )
        waiter_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "tasks"),
                Instruction(OpCode.WAIT_TASKS_VECTORISED, 1),
                Instruction(OpCode.RETURN),
            ),
            name="<group-waiter>",
            return_count=1,
        )
        waiter = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(
                    waiter_code,
                    {**vm.globals, "tasks": [second, first, first]},
                ),
                (),
                0,
            )
        )
        vm.scheduler.runnable.remove(waiter.control)
        vm.scheduler.runnable.appendleft(waiter.control)
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(waiter.control.state, TaskState.RUNNING)
        self.assertIsNotNone(waiter.control.blocked_cancel)
        self.assertEqual(
            vm.scheduler.wait(waiter),
            ([RuntimeNumber(2), RuntimeNumber(1), RuntimeNumber(1)],),
        )

    def test_cancelling_group_wait_removes_all_waiters(self):
        vm = VirtualMachine(output=lambda _value: None)
        tasks = [
            vm.scheduler.root_scope.spawn(
                lambda: (yield from _never_finishes())
            )
            for _ in range(2)
        ]
        waiter_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "tasks"),
                Instruction(OpCode.WAIT_TASKS_VECTORISED, 0),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-group-waiter>",
            return_count=0,
        )
        waiter = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(waiter_code, {**vm.globals, "tasks": tasks}), (), 0
            )
        )
        vm.scheduler.runnable.remove(waiter.control)
        vm.scheduler.runnable.appendleft(waiter.control)
        self.assertTrue(vm.scheduler.step())
        self.assertTrue(all(task.control.waiters for task in tasks))
        waiter.control.request_cancel()
        self.assertTrue(all(not task.control.waiters for task in tasks))

    def test_suspended_group_wait_cancels_unfinished_member_on_failure(self):
        from valiance.runtime.concurrency import TaskScope

        vm = VirtualMachine(output=lambda _value: None)
        failing_scope = TaskScope(vm.scheduler)
        running_scope = TaskScope(vm.scheduler)
        waiter_scope = TaskScope(vm.scheduler)
        failing = failing_scope.spawn(
            lambda: (_ for _ in ()).throw(ValueError("group failed"))
        )
        unfinished = running_scope.spawn(lambda: (yield from _never_finishes()))
        waiter_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "tasks"),
                Instruction(OpCode.WAIT_TASKS_VECTORISED, 0),
                Instruction(OpCode.RETURN),
            ),
            name="<failed-group-waiter>",
            return_count=0,
        )
        waiter = waiter_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(
                    waiter_code,
                    {**vm.globals, "tasks": [failing, unfinished]},
                ),
                (),
                0,
            )
        )
        vm.scheduler.runnable.remove(waiter.control)
        vm.scheduler.runnable.appendleft(waiter.control)
        self.assertTrue(vm.scheduler.step())
        with self.assertRaisesRegex(ValueError, "group failed"):
            vm.scheduler.wait(waiter)
        self.assertEqual(failing.control.state, TaskState.FAILED)
        self.assertEqual(unfinished.control.state, TaskState.CANCELLED)


def _never_finishes():
    from valiance.runtime import TaskYield
    while True:
        yield TaskYield()


if __name__ == "__main__":
    unittest.main()
