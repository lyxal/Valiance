import unittest

from valiance.analysis import Analyser
from valiance.asts import TypedChannelNode
from valiance.parsing import parse
from valiance.runtime import ClosedFault, compile_program, dumps, loads, run
from valiance.runtime.concurrency import Receive
from valiance.runtime.runtime_values import RuntimeNumber
from valiance.vtypes import Int, N
from valiance.vtypes.symbols import Symbol


def analyse(source: str):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def execute(source: str, *, optimize: bool = False, round_trip: bool = False):
    analyser, typed = analyse(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    program = compile_program(typed, optimize=optimize)
    if round_trip:
        program = loads(dumps(program))
    return run(program)


class ChannelSourceAnalysisTests(unittest.TestCase):
    def test_bounded_constructor_has_typed_channel(self):
        analyser, typed = analyse("2 Channel[Int]")
        self.assertEqual(analyser.diagnostics, [])
        node = typed[-1]
        self.assertIsInstance(node, TypedChannelNode)
        self.assertTrue(node.has_capacity)
        self.assertEqual(node.typ, N(Symbol("Channel"), Int))

    def test_receive_uses_distinct_receive_type(self):
        analyser, typed = analyse("$channel = Channel[Int]\n$channel close\n$channel receive")
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Receive"), Int))

    def test_incompatible_send_is_rejected(self):
        analyser, _ = analyse('$channel = Channel[Int]\n$channel "bad" send')
        self.assertTrue(any("channel send expects Int" in item for item in analyser.diagnostics))


class ChannelSourceExecutionTests(unittest.TestCase):
    def test_bounded_fifo_send_receive(self):
        result = execute(
            """$channel = 2 Channel[Int]
$channel 1 send
$channel 2 send
$channel receive
$channel receive"""
        )
        self.assertEqual(
            result,
            [Receive.Value(RuntimeNumber(1)), Receive.Value(RuntimeNumber(2))],
        )

    def test_close_drains_then_returns_closed(self):
        result = execute(
            """$channel = 1 Channel[Int]
$channel 4 send
$channel close
$channel receive
$channel receive"""
        )
        self.assertEqual(
            result,
            [Receive.Value(RuntimeNumber(4)), Receive.Closed()],
        )

    def test_send_after_close_raises_closed_fault(self):
        with self.assertRaisesRegex(Exception, "closed channel"):
            execute(
                """$channel = 1 Channel[Int]
$channel close
$channel 1 send"""
            )

    def test_unbuffered_block_is_explicitly_diagnosed(self):
        with self.assertRaisesRegex(Exception, "would block"):
            execute("$channel = Channel[Int]\n$channel 1 send")

    def test_spawned_unbuffered_sender_and_receiver_rendezvous(self):
        source = """$channel = Channel[Int]
$sender = fn -> => $channel 41 send end
$receiver = fn -> Receive[Int] => $channel receive end
$sendTask = $sender spawn
$receiveTask = $receiver spawn
$receiveTask wait
$sendTask wait"""
        self.assertEqual(
            execute(source),
            [Receive.Value(RuntimeNumber(41))],
        )

    def test_receiver_can_block_before_sender_is_scheduled(self):
        source = """$channel = Channel[Int]
$receiver = fn -> Receive[Int] => $channel receive end
$sender = fn -> => $channel 17 send end
$receiveTask = $receiver spawn
$sendTask = $sender spawn
$receiveTask wait
$sendTask wait"""
        self.assertEqual(
            execute(source),
            [Receive.Value(RuntimeNumber(17))],
        )

    def test_bounded_backpressure_resumes_producer_in_fifo_order(self):
        source = """$channel = 1 Channel[Int]
$producer = fn -> =>
  $channel 1 send
  $channel 2 send
  $channel 3 send
end
$consumer = fn -> Receive[Int], Receive[Int], Receive[Int] =>
  $channel receive
  $channel receive
  $channel receive
end
$producerTask = $producer spawn
$consumerTask = $consumer spawn
$consumerTask wait
$producerTask wait"""
        self.assertEqual(
            execute(source),
            [
                Receive.Value(RuntimeNumber(1)),
                Receive.Value(RuntimeNumber(2)),
                Receive.Value(RuntimeNumber(3)),
            ],
        )

    def test_close_wakes_an_already_blocked_receiver(self):
        source = """$channel = Channel[Int]
$receiver = fn -> Receive[Int] => $channel receive end
$closer = fn -> => $channel close end
$receiverTask = $receiver spawn
$closerTask = $closer spawn
$receiverTask wait
$closerTask wait"""
        self.assertEqual(execute(source), [Receive.Closed()])

    def test_close_faults_an_already_blocked_sender(self):
        source = """$channel = Channel[Int]
$sender = fn -> => $channel 1 send end
$closer = fn -> => $channel close end
$senderTask = $sender spawn
$closerTask = $closer spawn
$senderTask wait
$closerTask wait"""
        with self.assertRaisesRegex(
            Exception, "closed concurrency resource: channel closed before send committed"
        ):
            execute(source)

    def test_structured_rendezvous_matches_after_optimization_and_round_trip(self):
        source = """$channel = Channel[Int]
$sender = fn -> => $channel 23 send end
$receiver = fn -> Receive[Int] => $channel receive end
$sendTask = $sender spawn
$receiveTask = $receiver spawn
$receiveTask wait
$sendTask wait"""
        expected = [Receive.Value(RuntimeNumber(23))]
        self.assertEqual(execute(source), expected)
        self.assertEqual(execute(source, optimize=True), expected)
        self.assertEqual(execute(source, round_trip=True), expected)
        self.assertEqual(
            execute(source, optimize=True, round_trip=True),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
