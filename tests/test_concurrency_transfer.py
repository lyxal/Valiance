import unittest

from valiance.runtime import (
    Channel,
    IsolatedResource,
    Scheduler,
    TransferClass,
    validate_task_transfer,
)
from valiance.runtime.runtime_values import LazyList


class Resource(IsolatedResource):
    pass


class ClosureLike:
    def __init__(self, **captures):
        self.owned_names = frozenset(captures)
        self.globals = captures


class TransferValidationTests(unittest.TestCase):
    def test_value_graph_is_transferable_without_deep_copy(self):
        value = {"items": [1, 2, {"name": "ok"}]}
        self.assertEqual(validate_task_transfer(value), ())

    def test_nested_isolated_resource_reports_full_path(self):
        value = {"items": [1, Resource()]}
        violations = validate_task_transfer(value, path="argument[0]")
        self.assertEqual(len(violations), 1)
        self.assertIn("argument[0]['items'][1]", violations[0].render())

    def test_closure_capture_path_is_reported(self):
        closure = ClosureLike(connection=Resource())
        violations = validate_task_transfer(closure, path="function")
        self.assertEqual(len(violations), 1)
        self.assertIn("function.capture.connection", violations[0].render())

    def test_cycles_do_not_recurse_forever(self):
        value = []
        value.append(value)
        self.assertEqual(validate_task_transfer(value), ())

    def test_shared_handles_preserve_identity_and_stop_traversal(self):
        scheduler = Scheduler()
        task = scheduler.root_scope.spawn(lambda: (1,))
        channel = Channel[int](1)
        self.assertEqual(task.task_transfer_class, TransferClass.SHARED_HANDLE)
        self.assertEqual(channel.task_transfer_class, TransferClass.SHARED_HANDLE)
        self.assertEqual(validate_task_transfer([task, channel]), ())

    def test_unique_root_isolated_resource_can_move(self):
        self.assertEqual(validate_task_transfer(Resource(), unique=True), ())

    def test_lazy_value_validation_does_not_force_source(self):
        advances = []

        def source():
            advances.append("started")
            yield 1

        value = LazyList(source())
        self.assertEqual(validate_task_transfer(value), ())
        self.assertEqual(advances, [])
        self.assertEqual(value._cache, [])

    def test_channel_send_does_not_force_lazy_value(self):
        advances = []

        def source():
            advances.append("started")
            yield 1

        value = LazyList(source())
        channel = Channel[LazyList](1)
        self.assertTrue(channel.try_send(value))
        self.assertEqual(advances, [])
        self.assertEqual(value._cache, [])
        self.assertIs(channel.try_receive().value, value)

    def test_external_value_declares_transfer_class_explicitly(self):
        from valiance.runtime.transfer import (
            TransferClass, classify_task_value, declare_transfer_class,
        )

        class ExternalHandle:
            pass

        declare_transfer_class(ExternalHandle, TransferClass.ISOLATED)
        self.assertIs(classify_task_value(ExternalHandle()), TransferClass.ISOLATED)
        with self.assertRaises(ValueError):
            declare_transfer_class(ExternalHandle, TransferClass.SHARED_HANDLE)



if __name__ == "__main__":
    unittest.main()


class StaticTransferValidationTests(unittest.TestCase):
    def analyse(self, source: str):
        """Analyse one source program and return its diagnostics."""
        from valiance.analysis import Analyser
        from valiance.parsing import parse

        analyser = Analyser()
        analyser.analyse(parse(source))
        return analyser.diagnostics

    def test_spawn_rejects_known_mustcall_argument(self):
        diagnostics = self.analyse('''
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
$resource = Resource
$resource fn (value: Resource) => $value close end spawn
''')
        self.assertTrue(any("argument[0] has isolated type Resource" in item for item in diagnostics))

    def test_spawn_modifier_rejects_known_isolated_capture(self):
        diagnostics = self.analyse('''
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
$resource = Resource
spawn: fn => $resource close end
''')
        self.assertTrue(any("capture `resource` has isolated type Resource" in item for item in diagnostics))

    def test_spawn_rejects_isolated_resource_nested_in_collection(self):
        diagnostics = self.analyse('''
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
$resources = [Resource]
$resources fn (values: Resource+) => $values end spawn
''')
        self.assertTrue(any("argument[0][] has isolated type Resource" in item for item in diagnostics))

    def test_spawn_rejects_isolated_resource_nested_in_object_field(self):
        diagnostics = self.analyse('''
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
object Holder =>
  public $resource: Resource
end
$holder = Holder(Resource)
$holder fn (value: Holder) => $value end spawn
''')
        self.assertTrue(any("argument[0].resource" in item for item in diagnostics))

    def test_task_and_channel_handles_remain_statically_transferable(self):
        diagnostics = self.analyse('''
$channel = Channel[Integer]
$task = fn -> Integer => 1 end | spawn
$channel $task fn (channel: Channel[Integer], task: Task[Integer]) =>
  $task wait
end | spawn | wait
''')
        self.assertEqual(diagnostics, [])

    def test_explicit_move_allows_unique_isolated_spawn_argument(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program, dumps, loads, run

        source = """
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
Resource
move(value -> value)
spawn: fn (value: Resource) -> Resource => $value close end | wait
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(program), run(loads(dumps(program))))

    def test_plain_spawn_accepts_argument_after_explicit_move(self):
        diagnostics = self.analyse("""
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
Resource
move(value -> value)
fn (value: Resource) -> Resource => $value close end spawn
""")
        self.assertEqual(diagnostics, [])

    def test_non_moved_isolated_argument_remains_rejected(self):
        diagnostics = self.analyse("""
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
Resource
spawn: fn (value: Resource) -> Resource => $value close end
""")
        self.assertTrue(any(
            "argument[0] has isolated type Resource" in item
            for item in diagnostics
        ))

    def test_copy_does_not_prove_unique_transfer(self):
        diagnostics = self.analyse("""
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
Resource
copy(value -> value)
spawn: fn (value: Resource) -> Resource => $value close end
""")
        self.assertTrue(any(
            "argument[0] has isolated type Resource" in item
            for item in diagnostics
        ))

    def test_spawn_reports_all_isolated_captures_in_one_diagnostic(self):
        diagnostics = self.analyse('''
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
$connection = Resource
$transaction = Resource
spawn: fn => $connection $transaction end
''')
        self.assertEqual(len(diagnostics), 1)
        message = diagnostics[0]
        self.assertIn("function captures isolated values", message)
        self.assertIn("capture `connection`: isolated type Resource", message)
        self.assertIn("capture `transaction`: isolated type Resource", message)
        self.assertEqual(message.count("help:"), 1)

    def test_repeated_capture_is_listed_once(self):
        diagnostics = self.analyse('''
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
$resource = Resource
spawn: fn => $resource $resource end
''')
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].count("capture `resource`"), 1)

    def test_composite_capture_diagnostic_keeps_nested_path(self):
        diagnostics = self.analyse('''
@mustcall(any = ["close"])
object Resource =>
  define close => $self
end
object Holder =>
  public $resource: Resource
end
$holder = Holder(Resource)
$direct = Resource
spawn: fn => $holder $direct end
''')
        self.assertEqual(len(diagnostics), 1)
        message = diagnostics[0]
        self.assertIn("capture `holder`.resource: isolated type Resource", message)
        self.assertIn("capture `direct`: isolated type Resource", message)
