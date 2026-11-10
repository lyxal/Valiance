"""State[T] integration, protected-activation and exclusive-gate regressions."""
from __future__ import annotations

import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.concurrency import ExclusiveGate
from valiance.runtime.protected import GuardedValue
from valiance.runtime.transfer import IsolatedResource


def execute(source: str, *, optimize: bool = False):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    program = compile_program(typed, optimize=optimize)
    if optimize:
        program = loads(dumps(program))
    return run(program)


class ExclusiveGateTests(unittest.TestCase):
    def test_fifo_handoff_and_cancel_before_grant(self):
        gate = ExclusiveGate()
        a, b, c, d = (object() for _ in range(4))
        awakened = []
        self.assertIsNone(gate.acquire(a, lambda: awakened.append('a')))
        wb = gate.acquire(b, lambda: awakened.append('b'))
        wc = gate.acquire(c, lambda: awakened.append('c'))
        wd = gate.acquire(d, lambda: awakened.append('d'))
        gate.cancel(wc)
        gate.release(a)
        self.assertEqual(awakened, ['b'])
        self.assertTrue(wb.granted)
        self.assertFalse(wc.granted)
        gate.release(b)
        self.assertEqual(awakened, ['b', 'd'])
        self.assertTrue(wd.granted)
        gate.release(d)
        self.assertIsNone(gate.owner)
        self.assertEqual(gate.waiter_count, 0)

    def test_cancel_after_grant_releases_to_next_waiter(self):
        gate = ExclusiveGate()
        a, b, c = object(), object(), object()
        awakened = []
        gate.acquire(a, lambda: None)
        wb = gate.acquire(b, lambda: awakened.append('b'))
        wc = gate.acquire(c, lambda: awakened.append('c'))
        gate.release(a)
        gate.cancel(wb)
        self.assertEqual(awakened, ['b', 'c'])
        self.assertTrue(wc.granted)
        self.assertIs(gate.owner, c)
        gate.release(c)

    def test_reentrant_acquisition_rejected(self):
        gate = ExclusiveGate()
        owner = object()
        gate.acquire(owner, lambda: None)
        with self.assertRaisesRegex(Exception, 'recursive'):
            gate.acquire(owner, lambda: None)
        gate.release(owner)


class GuardedValueTests(unittest.TestCase):
    def test_multiple_handles_release_stored_value_once(self):
        value = object()
        cell = GuardedValue(value)
        cell.retain_handle()
        released = []
        cell.release_handle(released.append)
        self.assertEqual(released, [])
        cell.release_handle(released.append)
        self.assertEqual(released, [value])
        self.assertTrue(cell.destroyed)

    def test_final_drop_during_active_lease_preserves_handle_count(self):
        cell = GuardedValue(12)
        owner = object()
        cell.gate.acquire(owner, lambda: None)
        with self.assertRaisesRegex(RuntimeError, 'active operation'):
            cell.release_handle(lambda _value: None)
        self.assertEqual(cell.owners, 1)
        self.assertFalse(cell.destroyed)
        cell.gate.release(owner)
        released = []
        cell.release_handle(released.append)
        self.assertEqual(released, [12])
        self.assertTrue(cell.destroyed)

    def test_commit_rejects_isolated_value_without_mutating(self):
        cell = GuardedValue(12)
        owner = object()
        cell.gate.acquire(owner, lambda: None)
        with self.assertRaisesRegex(ValueError, 'cannot transfer'):
            cell.commit(owner, IsolatedResource())
        self.assertEqual(cell.value, 12)
        cell.gate.release(owner)
        cell.release_handle(lambda _v: None)


class StateExecutionTests(unittest.TestCase):
    def test_state_get_does_not_hijack_imported_object_methods(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "foo.vlnc").write_text(
                "public object Foo =>\n"
                "  $x: Number\n"
                "  public define get => $self.x\n"
                "end\n",
                encoding="utf-8",
            )
            source = """import {foo.Foo}
import {std.concurrency}
define get(:Foo) => $.x + 5
Foo(10) get
Foo(10) Foo::get
$s = concurrency.State(3)
concurrency.get($s)"""
            for optimized in (False, True):
                with self.subTest(optimized=optimized):
                    analyser = Analyser(source_file=root / "main.vlnc")
                    typed = analyser.analyse(parse(source))
                    self.assertEqual(analyser.diagnostics, [])
                    program = compile_program(typed, optimize=optimized)
                    if optimized:
                        program = loads(dumps(program))
                    self.assertEqual(run(program), [15, 10, 3])

    def test_set_does_not_hijack_imported_object_methods(self):
        """The new direct-value overload does not override a local declaration."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "foo.vlnc").write_text(
                "public object Foo =>\n"
                "  $x: Number\n"
                "  public define set(value: Int) => $self.x + $value\n"
                "end\n",
                encoding="utf-8",
            )
            source = '''import {foo.Foo}
import {std.concurrency}
define set(foo: Foo, value: Int) => $foo.x + $value + 5
Foo(10) 2 set
Foo(10) 2 Foo::set
$s = concurrency.State(3)
concurrency.set($s, 9)
concurrency.get($s)'''
            for optimized in (False, True):
                with self.subTest(optimized=optimized):
                    analyser = Analyser(source_file=root / "main.vlnc")
                    typed = analyser.analyse(parse(source))
                    self.assertEqual(analyser.diagnostics, [])
                    program = compile_program(typed, optimize=optimized)
                    if optimized:
                        program = loads(dumps(program))
                    self.assertEqual(run(program), [17, 12, 9])

    def test_modify_names_do_not_hijack_imported_object_methods(self):
        """A State intrinsic and a source declaration retain distinct identity."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'foo.vlnc').write_text(
                'public object Foo =>\n'
                '  $x: Number\n'
                '  public define modify(offset: Int) => $self.x + $offset\n'
                '  public define modifyWith(offset: Int) => $self.x + $offset\n'
                'end\n',
                encoding='utf-8',
            )
            source = '''import {foo.Foo}
import {std.concurrency}
define modify(foo: Foo, offset: Int) => $foo.x + $offset + 5
define modifyWith(foo: Foo, offset: Int) => $foo.x + $offset + 6
Foo(10) 2 modify
Foo(10) 2 Foo::modify
Foo(10) 2 modifyWith
Foo(10) 2 Foo::modifyWith
$s = concurrency.State(1)
concurrency.modify($s, fn(x:Int)->Int => $x 1 + end)
concurrency.modifyWith($s, fn(x:Int)->Int,Int => ($x 1 +) $x end)
get $s'''
            for optimized in (False, True):
                with self.subTest(optimized=optimized):
                    analyser = Analyser(source_file=root / 'main.vlnc')
                    typed = analyser.analyse(parse(source))
                    self.assertEqual(analyser.diagnostics, [])
                    program = compile_program(typed, optimize=optimized)
                    if optimized:
                        program = loads(dumps(program))
                    self.assertEqual(run(program), [17, 12, 18, 12, 2, 3])

    def test_state_contents_are_not_interpreted_by_shape(self):
        """Tuple-valued and tagged-result values remain ordinary T snapshots."""
        cases = (
            ('''import {std.concurrency}
$s = concurrency.State({2, true})
concurrency.set($s, {3, false})
get $s''', 'tuple'),
            ('''import {std.concurrency}
$s = concurrency.State(OK(2))
concurrency.set($s, OK(3))
get $s''', 'result'),
        )
        for source, kind in cases:
            for optimized in (False, True):
                with self.subTest(kind=kind, optimized=optimized):
                    result, = execute(source, optimize=optimized)
                    if kind == 'tuple':
                        self.assertEqual(result[0], 3)
                        self.assertEqual(result[1].value, 0)
                    else:
                        self.assertEqual(result.type_name, 'OK')
                        self.assertEqual(result.fields['value'], 3)

    def test_get_in_interpolation_completes_before_formatting(self):
        """Unresolved CALL must materialize a snapshot, not print its request."""
        cases = (
            '''import {std.concurrency.State}
$s = State(123)
"State = ${get $s}"
$s set 7
"State = ${get $s}"''',
            '''import {std.concurrency}
$s = concurrency.State(123)
"State = ${get $s}"
$s set 7
"State = ${get $s}"''',
        )
        for source in cases:
            for optimized in (False, True):
                with self.subTest(optimized=optimized, source=source):
                    self.assertEqual(
                        execute(source, optimize=optimized),
                        ['State = 123', 'State = 7'],
                    )

    def test_interpolated_get_prints_snapshot_not_python_request(self):
        """Formatting a State read may not expose a private runtime request."""
        import io
        from contextlib import redirect_stdout

        source = '''import {std.concurrency.State}
$s = State(42)
println "State = ${get $s}"'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(execute(source, optimize=optimized), [])
                self.assertEqual(output.getvalue(), 'State = 42\n')

    def test_interpolated_get_inside_modify_rejects_nested_access(self):
        """Dynamic CALL must still enforce the protected non-reentrancy rule."""
        source = '''import {std.concurrency.State}
$s = State(7)
$s modify: fn(x:Int)->Int =>
  "State = ${get $s}" pop
  $x 1 +
end'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                with self.assertRaisesRegex(Exception, 'nested protected state'):
                    execute(source, optimize=optimized)

    def test_constructor_get_set_modify_and_qualified_forms(self):
        source = '''import {std.concurrency}
$s = concurrency.State(0)
get $s
$s set 10
get $s
$s modify: (1 +)
get $s
concurrency.modify($s, fn(x:Int)->Int => $x 5 + end)
concurrency.get($s)'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                self.assertEqual(execute(source, optimize=optimized), [0, 10, 11, 16])

    def test_set_stores_function_valued_state_without_calling_it(self):
        """A function may be T; set stores it without treating it as a callback."""
        source = '''import {std.concurrency}
$first = fn(x:Int)->Int => $x 1 + end
$second = fn(x:Int)->Int => $x 2 + end
$s = concurrency.State($first)
$s set $second
$stored = get $s
$stored(10)'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [12])

    def test_set_can_read_same_state_before_acquiring_gate(self):
        """Inline replacement calculations use normal caller semantics."""
        source = '''import {std.concurrency}
$s = concurrency.State(3)
$s set (get $s)
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [3])

    def test_set_expression_returns_no_values(self):
        """A direct replacement consumes the state and value stack operands."""
        source = '''import {std.concurrency}
$s = concurrency.State(0)
$s set 10
concurrency.set($s, 11)
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [11])

    def test_set_takes_a_value_not_a_callback(self):
        """Reject the old colon modifier and function-valued replacement."""
        source = 'import {std.concurrency}\n$s = concurrency.State(0)\n$s set: 10'
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(any('expects 0' in item for item in analyser.diagnostics))

        source = '''import {std.concurrency}
$s = concurrency.State(0)
$s set (fn -> Int => 10 end)'''
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(analyser.diagnostics)

    def test_set_replacement_type_is_checked_before_runtime(self):
        """A State[Int] replacement must be Int in both spelling styles."""
        for source in (
            'import {std.concurrency}\n$s = concurrency.State(0)\n$s set "wrong"',
            'import {std.concurrency}\n$s = concurrency.State(0)\nconcurrency.set($s, "wrong")',
        ):
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(analyser.diagnostics)

    def test_modify_with_distinguishes_state_from_result(self):
        source = '''import {std.concurrency}
$s = concurrency.State(4)
$s modifyWith: fn(x:Int)->Int,Int =>
  $x 1 +
  $x
end
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [4, 5])

    def test_qualified_modify_with_uses_two_stack_outputs(self):
        source = '''import {std.concurrency}
$s = concurrency.State(4)
concurrency.modifyWith($s, fn(x:Int)->Int,Int => ($x 1 +) $x end)
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [4, 5])

    def test_modify_with_preserves_nested_closure_captures(self):
        source = '''import {std.concurrency}
$s = concurrency.State(5)
$extra = 3
$s modifyWith: fn(x:Int)->Int,Int =>
  $x $extra +
  $extra
end
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [3, 8])

    def test_set_expression_runs_once_and_can_read_its_own_state(self):
        """Replacement calculation is ordinary caller work, not a lease."""
        import io
        from contextlib import redirect_stdout

        source = '''import {std.concurrency}
$s = concurrency.State(3)
$replacement = fn -> Int =>
  "SET_EVALUATED" println
  get $s
end
$s set $replacement()
get $s'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(execute(source, optimize=optimized), [3])
                self.assertEqual(output.getvalue().strip(), 'SET_EVALUATED')

    def test_set_after_suspension_commits_atomically(self):
        """A competing modifier can commit while the setter waits on I/O."""
        source = '''import {std.concurrency}
$s = concurrency.State(0)
$c = Channel[Int]
$setter = fn -> =>
  $c receive | pop
  $s set 42
end
$modifier = fn -> =>
  $s modify: (1 +)
  $c 19 send
end
$a = $setter spawn
$b = $modifier spawn
$a wait
$b wait
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [42])

    def test_failed_replacement_calculation_preserves_committed_state(self):
        source = '''import {std.concurrency}
$s = concurrency.State(7)
$replacement = fn -> Int => RuntimeFault("failed before gate") panic end
try =>
  $s set $replacement()
handle RuntimeFault =>
  0 pop
end
$s'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                cell, = execute(source, optimize=optimized)
                self.assertEqual(cell.value, 7)
                self.assertIsNone(cell.gate.owner)
                self.assertEqual(cell.gate.waiter_count, 0)

    def test_cancellation_before_set_call_does_not_acquire_gate(self):
        """Cancellation before reaching set never acquires a State lease."""
        from valiance.runtime.vm import VirtualMachine, FunctionValue, _release_value
        from valiance.runtime.concurrency import TaskState

        source = ('''import {std.concurrency}
$s = concurrency.State(5)
$worker = fn -> =>
''' + '    1 pop\n' * 240 + '''$s set 9
end
$t = $worker spawn
$s''')
        analyser = Analyser()
        program = compile_program(analyser.analyse(parse(source)))
        self.assertEqual(analyser.diagnostics, [])
        with VirtualMachine() as vm:
            vm.tag_parents = program.tag_parents
            cell, = vm.call(FunctionValue(program.main, vm.globals), [])
            try:
                self.assertTrue(vm.scheduler.step())
                task = vm.scheduler.tasks[1]
                self.assertIs(cell.gate.owner, None)
                self.assertEqual(cell.gate.waiter_count, 0)
                self.assertEqual(cell.value, 5)
                task.request_cancel()
                self.assertTrue(vm.scheduler.step())
                vm.scheduler.root_scope.close()
                self.assertIs(task.state, TaskState.CANCELLED)
                self.assertEqual(cell.value, 5)
                self.assertIsNone(cell.gate.owner)
                self.assertEqual(cell.gate.waiter_count, 0)
            finally:
                _release_value(cell, vm)

    def test_cancellation_while_prior_work_waits_on_channel(self):
        """Ordinary VM suspension before set holds no State lease."""
        from valiance.runtime.vm import VirtualMachine, FunctionValue, _release_value
        from valiance.runtime.concurrency import TaskState

        source = '''import {std.concurrency}
$s = concurrency.State(5)
$c = Channel[Int]
$worker = fn -> =>
  $c receive | pop
  $s set 9
end
$t = $worker spawn
$s'''
        analyser = Analyser()
        program = compile_program(analyser.analyse(parse(source)))
        self.assertEqual(analyser.diagnostics, [])
        with VirtualMachine() as vm:
            vm.tag_parents = program.tag_parents
            cell, = vm.call(FunctionValue(program.main, vm.globals), [])
            try:
                self.assertTrue(vm.scheduler.step())
                task = vm.scheduler.tasks[1]
                self.assertIsNotNone(task.blocked_cancel)
                self.assertIsNone(cell.gate.owner)
                task.request_cancel()
                self.assertTrue(vm.scheduler.step())
                vm.scheduler.root_scope.close()
                self.assertIs(task.state, TaskState.CANCELLED)
                self.assertEqual(cell.value, 5)
                self.assertIsNone(cell.gate.owner)
                self.assertEqual(cell.gate.waiter_count, 0)
            finally:
                _release_value(cell, vm)

    def test_cancellation_of_set_waiter_keeps_prior_commit(self):
        """A direct-value replacement remains owned until acquisition or abort."""
        from valiance.runtime.vm import VirtualMachine, FunctionValue, _release_value
        from valiance.runtime.concurrency import TaskState

        source = ('''import {std.concurrency}
$s = concurrency.State(0)
$holder = fn -> =>
  $s modify: fn(x:Int)->Int =>
''' + '    1 pop\n' * 220 + '''    $x 1 +
  end
end
$setter = fn -> => $s set 99 end
$a = $holder spawn
$b = $setter spawn
$s''')
        analyser = Analyser()
        program = compile_program(analyser.analyse(parse(source)))
        self.assertEqual(analyser.diagnostics, [])
        with VirtualMachine() as vm:
            vm.tag_parents = program.tag_parents
            cell, = vm.call(FunctionValue(program.main, vm.globals), [])
            try:
                self.assertTrue(vm.scheduler.step())
                self.assertTrue(vm.scheduler.step())
                holder, setter = (vm.scheduler.tasks[i] for i in (1, 2))
                self.assertIs(cell.gate.owner, holder)
                self.assertEqual(cell.gate.waiter_count, 1)
                setter.request_cancel()
                self.assertTrue(vm.scheduler.step())
                vm.scheduler.root_scope.close()
                self.assertIs(setter.state, TaskState.CANCELLED)
                self.assertIs(holder.state, TaskState.COMPLETED)
                self.assertEqual(cell.value, 1)
                self.assertIsNone(cell.gate.owner)
                self.assertEqual(cell.gate.waiter_count, 0)
            finally:
                _release_value(cell, vm)

    def test_indexed_collection_update_does_not_collide_with_modify(self):
        source = '''import {std.concurrency}
$s = concurrency.State(0)
$s modify: (1 +)
update([1, 2, 3], 1, 9)
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [[1, 9, 3], 1])

    def test_handles_share_identity_across_tasks(self):
        source = '''import {std.concurrency}
$s = concurrency.State(0)
$t = $s
$a = fn => $s modify: (1 +) end | spawn
$b = fn => $t modify: (1 +) end | spawn
$a wait
$b wait
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [2])

    def test_contention_preserves_all_increments(self):
        tasks = '\n'.join(
            f'$task{i} = fn => $s modify: (1 +) end | spawn'
            for i in range(64)
        )
        waits = '\n'.join(f'$task{i} wait' for i in range(64))
        source = 'import {std.concurrency}\n$s = concurrency.State(0)\n' + tasks + '\n' + waits + '\nget $s'
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [64])

    def test_panic_aborts_without_commit(self):
        source = '''import {std.concurrency}
$s = concurrency.State(7)
try =>
  concurrency.modify($s, fn(x:Int)->Int => RuntimeFault("boom") panic end)
handle RuntimeFault =>
  0
end
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized)[-1], 7)

    def test_wait_in_callback_rejected_before_waiting(self):
        source = '''import {std.concurrency}
$s = concurrency.State(7)
$channel = Channel[Int]
$s modify: fn(x:Int)->Int =>
  $channel receive | pop
  $x 1 +
end'''
        for optimized in (False, True):
            with self.assertRaisesRegex(Exception, 'protected callback cannot'):
                execute(source, optimize=optimized)

    def test_builtin_io_rejected_before_side_effect(self):
        import io
        from contextlib import redirect_stdout

        source = '''import {std.concurrency}
$s = concurrency.State(7)
$s modify: fn(x:Int)->Int =>
  "SHOULD_NOT_PRINT" println
  $x 1 +
end'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                output = io.StringIO()
                with redirect_stdout(output):
                    with self.assertRaisesRegex(Exception, 'protected callback cannot'):
                        execute(source, optimize=optimized)
                self.assertEqual(output.getvalue(), '')

    def test_vectorised_builtin_io_rejected_before_side_effect(self):
        import io
        from contextlib import redirect_stdout

        source = '''import {std.concurrency}
$s = concurrency.State(7)
$s modify: fn(x:Int)->Int =>
  [1, 2] println
  $x 1 +
end'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                output = io.StringIO()
                with redirect_stdout(output):
                    with self.assertRaisesRegex(Exception, 'protected callback cannot'):
                        execute(source, optimize=optimized)
                self.assertEqual(output.getvalue(), '')

    def test_builtin_input_rejected_before_side_effect(self):
        from unittest.mock import patch

        source = '''import {std.concurrency}
$s = concurrency.State(7)
$s modify: fn(x:Int)->Int =>
  "prompt" input | pop
  $x 1 +
end'''
        with patch('builtins.input', side_effect=AssertionError('input called')) as read:
            with self.assertRaisesRegex(Exception, 'protected callback cannot'):
                execute(source)
        read.assert_not_called()

    def test_untrusted_host_intrinsic_is_rejected_before_execution(self):
        # Host-registered built-ins need not be in std.* or carry @IO tags.
        # An extension could block Python's thread without touching a VM wait.
        import valiance.vtypes as T
        from valiance.elements.builtins import BuiltinElement, BuiltinOverload
        from valiance.runtime.vm import (
            BuiltinValue, VirtualMachine, _Frame, _call_resolved_builtin,
        )
        from valiance.vtypes.symbols import Symbol

        calls = []

        def foreign_impl(args, _context):
            calls.append(args)
            return (args[0],)

        vm = VirtualMachine()
        foreign = BuiltinOverload(
            T.Overload(
                (T.N(Symbol("Int")),), (T.N(Symbol("Int")),),
                implementation_id="foreign:untagged-native",
            ),
            foreign_impl,
        )
        value = BuiltinValue(
            BuiltinElement(Symbol("foreign", ("vendor",)), (foreign,)),
            vm.globals["+"].context,
        )
        vm._protected_scopes[id(vm._root_protected_owner)] = object()
        try:
            for vectorised in (False, True):
                with self.subTest(vectorised=vectorised):
                    frame = _Frame([123], {}, vm.globals)
                    with self.assertRaisesRegex(Exception, "protected callback cannot"):
                        _call_resolved_builtin(value, foreign, frame, vm, vectorised)
            self.assertEqual(calls, [])
        finally:
            vm._protected_scopes.pop(id(vm._root_protected_owner), None)
            vm.close()

    def test_registered_host_factory_and_encoder_cannot_escape_protection(self):
        from types import SimpleNamespace
        from valiance.runtime.vm import (
            VirtualMachine, _Frame, _call_resolved_builtin, _construct_managed,
        )

        invoked = []
        vm = VirtualMachine()
        vm.register_reflection_native(
            "&Opaque",
            construct=lambda value: invoked.append(("factory", value)),
            encode=lambda value: invoked.append(("encoder", value)),
        )
        vm._protected_scopes[id(vm._root_protected_owner)] = object()
        try:
            with self.assertRaisesRegex(Exception, "protected callback cannot"):
                vm.construct_reflected("&Opaque", {"id": 1})
            with self.assertRaisesRegex(Exception, "protected callback cannot"):
                vm._runtime_represent_native(SimpleNamespace(ffi_type="&Opaque"))
            # Both the recoverable-result native reflection entry and the
            # bytecode constructor path must fail before their error adapters
            # can swallow a protected-policy violation.
            native = vm.globals["constructRuntimeNative?"]
            frame = _Frame([object(), object()], {}, vm.globals)
            with self.assertRaisesRegex(Exception, "protected callback cannot"):
                _call_resolved_builtin(
                    native, native.element.definitions[0], frame, vm, False,
                )
            with self.assertRaisesRegex(Exception, "protected callback cannot"):
                _construct_managed(
                    _Frame([], {}, vm.globals),
                    ("finish", None, None, ("ffi", "&Opaque", ())),
                    vm,
                )
            self.assertEqual(invoked, [])
        finally:
            vm._protected_scopes.pop(id(vm._root_protected_owner), None)
            vm.close()

    def test_nested_other_state_is_rejected(self):
        source = '''import {std.concurrency}
$s = concurrency.State(7)
$t = concurrency.State(5)
$s modify: fn(x:Int)->Int =>
  get $t | pop
  $x 1 +
end'''
        with self.assertRaisesRegex(Exception, 'nested protected state'):
            execute(source)

    def test_escaping_nested_panic_releases_lease_for_next_update(self):
        source = '''import {std.concurrency}
$s = concurrency.State(7)
define die(x: Int) -> Int =>
  RuntimeFault("nested panic") panic
end
try =>
  concurrency.modify($s, fn(x:Int)->Int => die($x) end)
handle RuntimeFault =>
  0 pop
end
$s modify: (1 +)
get $s'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                self.assertEqual(execute(source, optimize=optimized), [8])

    def test_nested_state_acquisition_rejected(self):
        source = '''import {std.concurrency}
$s = concurrency.State(7)
$s modify: fn(x:Int)->Int =>
  get $s | pop
  $x 1 +
end'''
        for optimized in (False, True):
            with self.assertRaisesRegex(Exception, 'nested protected state'):
                execute(source, optimize=optimized)

    def test_nested_immutable_snapshot_survives_replacement(self):
        source = '''import {std.concurrency}
$s = concurrency.State([[1, 2], [3]])
$before = get $s
concurrency.set($s, [[9]])
$before
get $s'''
        for optimized in (False, True):
            self.assertEqual(execute(source, optimize=optimized), [
                [[1, 2], [3]], [[9]],
            ])

    def test_active_and_queued_cancellation_preserve_last_commit(self):
        # Drive actual compiled task activations for deterministic instruction
        # quanta; this distinguishes an active lease from queued acquisition.
        from valiance.runtime.vm import VirtualMachine, FunctionValue, _release_value
        from valiance.runtime.concurrency import TaskState

        source = ('''import {std.concurrency}
$s = concurrency.State(0)
$worker = fn =>
  $s modify: fn(x:Int)->Int =>
''' + '    1 pop\n' * 200 + '''    $x 1 +
  end
end
$a = $worker spawn
$b = $worker spawn
$c = $worker spawn
$s''')
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        compiled = compile_program(typed)
        with VirtualMachine() as vm:
            vm.tag_parents = compiled.tag_parents
            cell, = vm.call(FunctionValue(compiled.main, vm.globals), [])
            try:
                # First task enters callback, the next two enqueue FIFO.
                for _ in range(3):
                    self.assertTrue(vm.scheduler.step())
                first, second, third = (vm.scheduler.tasks[i] for i in (1, 2, 3))
                self.assertIs(cell.gate.owner, first)
                self.assertEqual(cell.gate.waiter_count, 2)
                self.assertEqual(cell.value, 0)
                second.request_cancel()
                vm.scheduler.step()
                self.assertEqual(cell.gate.waiter_count, 1)
                vm.scheduler.root_scope.close()
                self.assertEqual(cell.value, 2)
                self.assertIsNone(cell.gate.owner)
                self.assertEqual(cell.gate.waiter_count, 0)
                self.assertIs(second.state, TaskState.CANCELLED)
                self.assertIs(first.state, TaskState.COMPLETED)
                self.assertIs(third.state, TaskState.COMPLETED)
            finally:
                _release_value(cell, vm)

        # Independently exercise cancellation *inside* an active callback.
        source = source.split('$b = $worker spawn', 1)[0] + '$s'
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        compiled = compile_program(typed)
        with VirtualMachine() as vm:
            vm.tag_parents = compiled.tag_parents
            cell, = vm.call(FunctionValue(compiled.main, vm.globals), [])
            try:
                self.assertTrue(vm.scheduler.step())
                active = vm.scheduler.tasks[1]
                self.assertIs(cell.gate.owner, active)
                active.request_cancel()
                vm.scheduler.step()
                vm.scheduler.root_scope.close()
                self.assertEqual(cell.value, 0)
                self.assertIsNone(cell.gate.owner)
                self.assertIs(active.state, TaskState.CANCELLED)
            finally:
                _release_value(cell, vm)

    def test_woken_waiter_keeps_lease_until_active_cancellation_unwind(self):
        """A stale blocked-wait cancellation must not unlock an active callback."""
        from valiance.runtime.vm import VirtualMachine, FunctionValue, _release_value
        from valiance.runtime.concurrency import TaskState

        source = ('''import {std.concurrency}
$s = concurrency.State(0)
$worker = fn =>
  $s modify: fn(x:Int)->Int =>
'''
                  + '    1 pop\n' * 200
                  + '''    $x 1 +
  end
end
$a = $worker spawn
$b = $worker spawn
$s''')
        analyser = Analyser()
        program = compile_program(analyser.analyse(parse(source)))
        self.assertEqual(analyser.diagnostics, [])
        with VirtualMachine() as vm:
            vm.tag_parents = program.tag_parents
            cell, = vm.call(FunctionValue(program.main, vm.globals), [])
            try:
                first, second = (vm.scheduler.tasks[i] for i in (1, 2))
                for _ in range(24):
                    self.assertTrue(vm.scheduler.step())
                    if cell.gate.owner is second and second.quantum_count >= 3:
                        break
                else:
                    self.fail('granted waiter did not resume inside protected callback')
                self.assertIs(first.state, TaskState.COMPLETED)
                self.assertIs(cell.gate.owner, second)
                second.request_cancel()
                # Cancellation is pending until the scheduled task unwinds.
                # It must not prematurely relinquish an active lease.
                self.assertIs(cell.gate.owner, second)
                self.assertTrue(vm.scheduler.step())
                self.assertIs(second.state, TaskState.CANCELLED)
                self.assertIsNone(cell.gate.owner)
                self.assertEqual(cell.value, 1)
            finally:
                vm.scheduler.root_scope.close()
                _release_value(cell, vm)

    def test_handled_panic_in_protected_callback_commits(self):
        source = '''import {std.concurrency}
$s = concurrency.State(7)
$s
$s modify: fn(x:Int)->Int =>
  try =>
    RuntimeFault("boom") panic
  handle RuntimeFault =>
    0
  end | pop
  $x 2 +
end'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                result, = execute(source, optimize=optimized)
                self.assertEqual(result.value, 9)
                self.assertIsNone(result.gate.owner)

    def test_nonduplicable_state_initial_value_is_rejected_statically(self):
        for initial in ('Locked', '[Locked]', '{Locked}'):
            with self.subTest(initial=initial):
                analyser = Analyser()
                analyser.analyse(parse('''import {std.concurrency}
@nodup("cannot duplicate Locked") object Locked => end
concurrency.State(''' + initial + ')'))
                self.assertTrue(analyser.diagnostics)
                self.assertIn('State initial value must be duplicable',
                              str(analyser.diagnostics))
                self.assertIn('cannot duplicate Locked', str(analyser.diagnostics))

    def test_commit_cleanup_failure_keeps_committed_value_and_releases_gate(self):
        """Once commit publishes, destructor failure cannot roll it back."""
        from unittest.mock import patch
        from valiance.runtime.vm import VirtualMachine, _ProtectedScope
        from valiance.runtime.protected import ProtectedOperation

        old, replacement = object(), object()
        cell = GuardedValue(old)
        cell.retain_handle()  # the pending operation owns its own occurrence
        with VirtualMachine() as vm:
            owner = vm._root_protected_owner
            self.assertIsNone(cell.gate.acquire(owner, lambda: None))
            scope = _ProtectedScope(ProtectedOperation('update', cell), owner, vm)
            scope.activate()
            def drop(value, _vm):
                if value is old:
                    raise RuntimeError('old value destructor failed')
            with patch('valiance.runtime.vm._release_value', side_effect=drop):
                with self.assertRaisesRegex(RuntimeError, 'old value destructor failed'):
                    scope.complete([replacement])
            self.assertIs(cell.value, replacement)
            self.assertIsNone(cell.gate.owner)
            self.assertNotIn(id(owner), vm._protected_scopes)
            self.assertEqual(cell.owners, 1)
            cell.release_handle(lambda _value: None)

    def test_failed_commit_aborts_and_releases_protected_scope(self):
        from valiance.runtime.vm import VirtualMachine, _ProtectedScope
        from valiance.runtime.protected import ProtectedOperation

        cell = GuardedValue(7)
        cell.retain_handle()
        with VirtualMachine() as vm:
            owner = vm._root_protected_owner
            cell.gate.acquire(owner, lambda: None)
            scope = _ProtectedScope(ProtectedOperation('update', cell), owner, vm)
            scope.activate()
            with self.assertRaisesRegex(ValueError, 'cannot transfer'):
                scope.complete([IsolatedResource()])
            self.assertEqual(cell.value, 7)
            self.assertIsNone(cell.gate.owner)
            self.assertNotIn(id(owner), vm._protected_scopes)
            self.assertEqual(cell.owners, 1)
            cell.release_handle(lambda _value: None)

    def test_transitive_nonduplicable_state_storage_is_rejected(self):
        cases = (
            ('record{item => Locked}', 'item'),
            ('dict{"key" => Locked}', 'Locked'),
            ('Owner(Locked)', 'item'),
        )
        for expression, location in cases:
            with self.subTest(expression=expression):
                analyser = Analyser()
                analyser.analyse(parse('''import {std.concurrency}
@nodup("private resource") object Locked => end
object Owner => public $item: Locked end
concurrency.State(''' + expression + ')'))
                self.assertTrue(analyser.diagnostics)
                self.assertIn('State initial value must be duplicable',
                              str(analyser.diagnostics))
                self.assertIn('private resource', str(analyser.diagnostics))
                self.assertIn(location, str(analyser.diagnostics))

    def test_runtime_rejects_nested_nodup_before_commit(self):
        from valiance.runtime.runtime_values import ObjectRuntimeType, ObjectValue
        from valiance.runtime.transfer import require_shared_value

        resource = ObjectValue('Locked', {}, runtime_type=ObjectRuntimeType(
            dup_error='no duplicate owner'))
        nested = ObjectValue('Owner', {'item': resource})
        with self.assertRaisesRegex(ValueError, 'State initial value.item'):
            require_shared_value(nested, path='State initial value')
        cell = GuardedValue(7)
        owner = object()
        cell.gate.acquire(owner, lambda: None)
        try:
            with self.assertRaisesRegex(ValueError, 'State replacement.item'):
                cell.commit(owner, nested)
            self.assertEqual(cell.value, 7)
        finally:
            cell.gate.release(owner)
            cell.release_handle(lambda _value: None)

    def test_runtime_rejects_lazy_and_native_resource_values(self):
        from valiance.runtime.runtime_values import (
            FFIBufferValue, FFIHandleValue, FFIStructValue, LazyList,
        )
        from valiance.runtime.transfer import require_shared_value
        unsafe = (
            LazyList(iter((1, 2))),
            FFIHandleValue('&Opaque', 0x1000),
        )
        for item in unsafe:
            with self.subTest(item=type(item).__name__):
                with self.assertRaisesRegex(ValueError, 'cannot share'):
                    require_shared_value(item, path='State initial value')
        with self.assertRaisesRegex(ValueError, 'State replacement\\.inner'):
            require_shared_value(
                FFIStructValue('&Struct', (('inner', FFIHandleValue('&Opaque', 0x1001)),)),
                path='State replacement',
            )
        # Plain copied native values are still admissible.
        require_shared_value(FFIBufferValue('&Int', ()), path='State initial value')

    def test_static_replacement_type_check(self):
        analyser = Analyser()
        analyser.analyse(parse('''import {std.concurrency}
$s = concurrency.State(1)
concurrency.set($s, "wrong")'''))
        self.assertTrue(analyser.diagnostics)


if __name__ == '__main__':
    unittest.main()
