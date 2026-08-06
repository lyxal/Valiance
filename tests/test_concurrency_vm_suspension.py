import unittest

from valiance.runtime import Channel, Receive, TaskState
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode
from valiance.runtime.runtime_values import ListValue, RuntimeNumber
from valiance.runtime.vm import FunctionValue, VirtualMachine


class VmSuspensionTests(unittest.TestCase):
    def test_wait_opcode_blocks_and_wakes_activation(self):
        vm = VirtualMachine(output=lambda _value: None)
        vm.task_instruction_quantum = 8
        producer_code = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(9)),
                Instruction(OpCode.RETURN),
            ),
            name="<producer>",
            return_count=1,
        )
        producer = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(producer_code, vm.globals), (), 0
            )
        )
        waiter_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "target"),
                Instruction(OpCode.WAIT_TASK, 1),
                Instruction(OpCode.RETURN),
            ),
            name="<waiter>",
            return_count=1,
        )
        waiter = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(waiter_code, {**vm.globals, "target": producer}), (), 0
            )
        )
        # Reorder so waiter reaches WAIT_TASK before producer completes.
        vm.scheduler.runnable.remove(waiter.control)
        vm.scheduler.runnable.appendleft(waiter.control)
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(waiter.control.state, TaskState.RUNNING)
        self.assertIsNotNone(waiter.control.blocked_cancel)
        self.assertEqual(vm.scheduler.wait(waiter), (RuntimeNumber(9),))

    def test_unbuffered_receive_wakes_when_sender_commits(self):
        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[int]()
        receive_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.CHANNEL_RECEIVE),
                Instruction(OpCode.RETURN),
            ),
            name="<receiver>",
            return_count=1,
        )
        send_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(5)),
                Instruction(OpCode.CHANNEL_SEND),
                Instruction(OpCode.RETURN),
            ),
            name="<sender>",
            return_count=0,
        )
        receiver = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(receive_code, {**vm.globals, "channel": channel}), (), 0
            )
        )
        sender = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(send_code, {**vm.globals, "channel": channel}), (), 0
            )
        )
        self.assertEqual(
            vm.scheduler.wait(receiver),
            (Receive.Value(RuntimeNumber(5)),),
        )
        self.assertEqual(vm.scheduler.wait(sender), ())

    def test_unbuffered_send_wakes_when_receiver_commits(self):
        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[int]()
        send_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.PUSH_CONST, RuntimeNumber(7)),
                Instruction(OpCode.CHANNEL_SEND),
                Instruction(OpCode.RETURN),
            ),
            name="<sender-first>",
            return_count=0,
        )
        receive_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.CHANNEL_RECEIVE),
                Instruction(OpCode.RETURN),
            ),
            name="<receiver-second>",
            return_count=1,
        )
        sender = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(send_code, {**vm.globals, "channel": channel}), (), 0
            )
        )
        receiver = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(receive_code, {**vm.globals, "channel": channel}), (), 0
            )
        )
        self.assertEqual(vm.scheduler.wait(sender), ())
        self.assertEqual(
            vm.scheduler.wait(receiver),
            (Receive.Value(RuntimeNumber(7)),),
        )

    def test_cancelling_blocked_receive_removes_registration(self):
        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[int]()
        receive_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.CHANNEL_RECEIVE),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-receiver>",
            return_count=1,
        )
        receiver = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(receive_code, {**vm.globals, "channel": channel}), (), 0
            )
        )
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(len(channel.receivers), 1)
        receiver.control.request_cancel()
        self.assertEqual(len(channel.receivers), 0)
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(receiver.control.state, TaskState.CANCELLED)

    def test_cancelling_blocked_send_releases_uncommitted_value(self):
        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[ListValue]()
        value = ListValue([RuntimeNumber(7)])
        send_code = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.PUSH_CONST, value),
                Instruction(OpCode.CHANNEL_SEND),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-owned-sender>",
            return_count=0,
        )
        sender = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(
                    send_code, {**vm.globals, "channel": channel}
                ),
                (),
                0,
            )
        )
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(len(channel.senders), 1)
        self.assertEqual(value.refcount, 1)
        sender.control.request_cancel()
        self.assertEqual(len(channel.senders), 0)
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(sender.control.state, TaskState.CANCELLED)
        self.assertEqual(value.refcount, 0)

    def test_cancelling_blocked_receive_releases_frame_local_value(self):
        vm = VirtualMachine(output=lambda _value: None)
        channel = Channel[int]()
        owned = ListValue([RuntimeNumber(3)])
        receive_code = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, owned),
                Instruction(OpCode.STORE_VAR, "owned"),
                Instruction(OpCode.LOAD_VAR, "channel"),
                Instruction(OpCode.CHANNEL_RECEIVE),
                Instruction(OpCode.RETURN),
            ),
            name="<cancel-receiver-local>",
            return_count=1,
        )
        receiver = vm.scheduler.root_scope.spawn(
            lambda: vm._task_call_runner(
                FunctionValue(
                    receive_code, {**vm.globals, "channel": channel}
                ),
                (),
                0,
            )
        )
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(len(channel.receivers), 1)
        receiver.control.request_cancel()
        self.assertTrue(vm.scheduler.step())
        self.assertEqual(receiver.control.state, TaskState.CANCELLED)
        self.assertEqual(owned.refcount, 0)


if __name__ == "__main__":
    unittest.main()
