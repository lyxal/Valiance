import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import Channel, ChannelReceiver, compile_program, run


class ChannelRegistrationTests(unittest.TestCase):
    def test_cancel_one_sender_preserves_other_fifo_registration(self):
        channel = Channel[int]()
        first = channel.register_send(1)
        second = channel.register_send(2)
        assert first is not None and second is not None
        self.assertTrue(channel.cancel_send(first))
        self.assertFalse(channel.cancel_send(first))
        self.assertEqual(channel.try_receive().value, 2)
        self.assertTrue(second.committed)

    def test_cancel_one_receiver_preserves_other_fifo_registration(self):
        channel = Channel[int]()
        first = channel.register_receive()
        second = channel.register_receive()
        self.assertIsInstance(first, ChannelReceiver)
        self.assertIsInstance(second, ChannelReceiver)
        assert isinstance(first, ChannelReceiver)
        assert isinstance(second, ChannelReceiver)
        self.assertTrue(channel.cancel_receive(first))
        self.assertFalse(channel.cancel_receive(first))
        self.assertTrue(channel.try_send(7))
        self.assertEqual(second.result.value, 7)

    def test_committed_sender_cannot_be_cancelled(self):
        channel = Channel[int]()
        sender = channel.register_send(3)
        assert sender is not None
        self.assertEqual(channel.try_receive().value, 3)
        self.assertFalse(channel.cancel_send(sender))

    def test_close_resolves_registered_receivers_and_faults_senders(self):
        receives = Channel[int]()
        receiver = receives.register_receive()
        assert isinstance(receiver, ChannelReceiver)
        receives.close()
        self.assertTrue(receiver.result.closed)

        sends = Channel[int]()
        sender = sends.register_send(1)
        assert sender is not None
        sends.close()
        self.assertIsNotNone(sender.fault)
        self.assertFalse(sends.cancel_send(sender))

    def test_close_preserves_buffer_fifo_and_resolves_waiting_receivers(self):
        channel = Channel[int](3)
        self.assertTrue(channel.try_send(1))
        self.assertTrue(channel.try_send(2))
        channel.close()
        self.assertEqual(channel.try_receive().value, 1)
        self.assertEqual(channel.try_receive().value, 2)
        self.assertTrue(channel.try_receive().closed)
        self.assertTrue(channel.try_receive().closed)

    def test_close_wakes_all_blocked_senders_exactly_once(self):
        channel = Channel[int]()
        wake_counts = [0, 0, 0]
        registrations = []
        for index in range(3):
            def wake(index=index):
                wake_counts[index] += 1
            registration = channel.register_send(index, wake)
            assert registration is not None
            registrations.append(registration)
        channel.close()
        channel.close()
        self.assertEqual(wake_counts, [1, 1, 1])
        self.assertTrue(all(item.fault is not None for item in registrations))
        self.assertEqual(list(channel.senders), [])

    def test_close_wakes_all_blocked_receivers_exactly_once(self):
        channel = Channel[int]()
        wake_counts = [0, 0, 0]
        registrations = []
        for index in range(3):
            def wake(index=index):
                wake_counts[index] += 1
            registration = channel.register_receive(wake)
            assert isinstance(registration, ChannelReceiver)
            registrations.append(registration)
        channel.close()
        channel.close()
        self.assertEqual(wake_counts, [1, 1, 1])
        self.assertTrue(all(item.result.closed for item in registrations))
        self.assertEqual(list(channel.receivers), [])

    def test_cancelled_sender_value_is_never_delivered(self):
        channel = Channel[int]()
        first = channel.register_send(1)
        second = channel.register_send(2)
        assert first is not None and second is not None
        self.assertTrue(channel.cancel_send(first))
        self.assertEqual(channel.try_receive().value, 2)
        self.assertTrue(second.committed)
        pending = channel.register_receive()
        self.assertIsInstance(pending, ChannelReceiver)
        channel.close()
        self.assertTrue(pending.result.closed)

    def test_cancelled_receiver_does_not_consume_later_value(self):
        channel = Channel[int]()
        first = channel.register_receive()
        second = channel.register_receive()
        assert isinstance(first, ChannelReceiver)
        assert isinstance(second, ChannelReceiver)
        self.assertTrue(channel.cancel_receive(first))
        self.assertTrue(channel.try_send(9))
        self.assertEqual(second.result.value, 9)
        self.assertIsNone(first.result)


class VmRegistrationRollbackTests(unittest.TestCase):
    def execute_with_vm(self, source: str):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        return run(compile_program(typed, optimize=False))

    def test_blocking_source_send_reports_rollback(self):
        with self.assertRaisesRegex(Exception, "registration was cancelled"):
            self.execute_with_vm("$c = Channel[Integer]\n$c 1 send")

    def test_blocking_source_receive_reports_rollback(self):
        with self.assertRaisesRegex(Exception, "registration was cancelled"):
            self.execute_with_vm("$c = Channel[Integer]\n$c receive")


if __name__ == "__main__":
    unittest.main()
