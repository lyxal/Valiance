"""Host-defined reflected types and native construction independent of JSON."""

import unittest
from dataclasses import replace
from valiance.runtime.bytecode import OpCode, ReflectionFieldReference, ReflectionFieldsReference
from valiance.runtime.serialization import BytecodeFormatError
from valiance.runtime.vm import VirtualMachine
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads
from valiance.runtime.runtime_values import FFIHandleValue, FFIScalarValue, RuntimeNumber, ObjectValue


class RuntimeReflectionConstructionTests(unittest.TestCase):
    def test_dynamic_schema_construction_and_reflected_metadata(self):
        with VirtualMachine(output=lambda _: None) as vm:
            vm.register_reflection_type('RuntimeChild', (
                ('name', 'String'), ('enabled', '#boolean Int'),
            ))
            vm.register_reflection_type('RuntimeBox', (
                ('value', 'T'), ('secret', 'String', 'private'),
            ), generics=('T',), defaults={'secret': 'hidden'})
            child = vm.construct_reflected('RuntimeChild', {'name': 'Ada', 'enabled': True})
            box = vm.construct_reflected('RuntimeBox[RuntimeChild]', {'value': child})
            self.assertEqual(vm.reflected_read(box, 'value'), child)
            other_child = vm.construct_reflected('RuntimeChild', {'name': 'Bee', 'enabled': False})
            other_box = vm.reflected_write(box, 'value', other_child)
            self.assertEqual(vm.reflected_read(other_box, 'value'), other_child)
            self.assertEqual(vm.reflected_read(box, 'value'), child)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                vm.reflected_write(box, 'value', 'not a child')
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                vm.reflected_write(box, 'secret', 'changed')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                vm.construct_reflected('RuntimeBox[RuntimeChild]', {
                    'value': ObjectValue('RuntimeChild', {'name': 7, 'enabled': True}),
                })
            self.assertEqual(vm.reflected_read(box, 'secret', unsafe=True), 'hidden')
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                vm.reflected_read(box, 'secret')
            fields = vm.reflected_fields(box).fields['#fields']
            self.assertEqual([(item.fields['name'], item.fields['type'].type_args[0])
                              for item in fields],
                             [('value', 'RuntimeChild'), ('secret', 'String')])
            self.assertEqual(vm.encode_json(box), '{"value":{"name":"Ada","enabled":true}}')
            decoded = vm.decode_json('{"value":{"name":"Bee","enabled":false}}',
                                     'RuntimeBox[RuntimeChild]')
            self.assertEqual(vm.reflected_read(vm.reflected_read(decoded, 'value'), 'name'), 'Bee')

    def test_construction_rejects_invalid_fields_and_requires_unsafe_for_private(self):
        with VirtualMachine(output=lambda _: None) as vm:
            vm.register_reflection_type('RuntimeSecret', (
                ('n', 'Int'), ('hidden', 'String', 'private'),
            ), defaults={'hidden': 'default'})
            for supplied, message in (
                ({'n': 'bad'}, 'does not match'),
                ({'n': 1, 'unknown': 2}, 'unknown fields'),
                ({'n': 1, 'hidden': 'override'}, 'unsafe'),
                ({}, 'missing required'),
            ):
                with self.subTest(supplied=supplied):
                    with self.assertRaisesRegex(ValueError, message):
                        vm.construct_reflected('RuntimeSecret', supplied)
            private = vm.construct_reflected(
                'RuntimeSecret', {'n': 7, 'hidden': 'override'}, unsafe=True,
            )
            self.assertEqual(vm.reflected_read(private, 'hidden', unsafe=True), 'override')
            with self.assertRaisesRegex(ValueError, 'no runtime-defined'):
                vm.construct_reflected('Unregistered', {})
            with self.assertRaisesRegex(ValueError, 'no matching registered'):
                vm.reflected_fields(ObjectValue('RuntimeSecret', {'n': 7}))

    def test_native_factory_and_type_identity_without_json(self):
        with VirtualMachine(output=lambda _: None) as vm:
            vm.register_reflection_native('&Opaque', construct=lambda data: FFIHandleValue('&Opaque', data['id']),
                                          encode=lambda value: {'id': value.address})
            handle = vm.construct_reflected('&Opaque', {'id': 3})
            self.assertEqual(handle.ffi_type, '&Opaque')
            self.assertEqual(vm.encode_json(handle), '{"id":3}')
            self.assertEqual(vm.decode_json('{"id":4}', '&Opaque').address, 4)
            with self.assertRaisesRegex(ValueError, 'non-null'):
                vm.construct_reflected('&Opaque', {'id': 0})
            handle.request_destroy()
            with self.assertRaisesRegex(ValueError, 'Released native'):
                vm.encode_json(handle)

    def test_source_native_builder_uses_registered_factory(self):
        program = parse("""unsafe =>
  $builder = uninit[&scalar]
  finished? $builder
  finish? $builder
end""")
        analyser = Analyser()
        typed = analyser.analyse(program)
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        bytecode = compile_program(typed)
        for compiled in (bytecode, loads(dumps(bytecode))):
            with self.subTest(serialized=compiled is not bytecode):
                with VirtualMachine(output=lambda _: None) as vm:
                    vm.register_reflection_native(
                        '&scalar', construct=lambda _: FFIScalarValue('&scalar', 17),
                    )
                    ready, result = vm.run(compiled)
                    self.assertEqual(ready, 1)
                    self.assertEqual(result.type_name, 'OK')
                    self.assertEqual(result.fields['value'].value, 17)

    def test_native_bytecode_rejects_fabricated_fields(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('unsafe => uninit[&Opaque] | finish? end'))
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        bytecode = compile_program(typed)
        code = bytecode.main
        index = next(i for i, item in enumerate(code.instructions) if item.op is OpCode.CONSTRUCT)
        instruction = code.instructions[index]
        op, layout, constructor, owner = instruction.arg
        self.assertEqual(owner, ('ffi', '&Opaque'))
        invalid = replace(instruction, arg=(op, ReflectionFieldsReference('&Opaque', (
            ReflectionFieldReference('address', 'Int', 'public'),
        )), constructor, owner))
        instructions = list(code.instructions)
        instructions[index] = invalid
        forged = replace(bytecode, main=replace(code, instructions=tuple(instructions)))
        with self.assertRaises(BytecodeFormatError):
            dumps(forged)

    def test_source_native_builder_rejects_missing_invalid_and_throwing_factories(self):
        source = 'unsafe => uninit[&Opaque] | finish? end'
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        program = compile_program(typed)
        with VirtualMachine(output=lambda _: None) as vm:
            result, = vm.run(program)
            self.assertEqual(result.type_name, 'ConstructionError')
            self.assertEqual(result.fields['reason'], 'NativeFactoryUnavailable')
        for factory in (
            lambda _: FFIHandleValue('&Other', 1),
            lambda _: (_ for _ in ()).throw(RuntimeError('factory failure')),
        ):
            with self.subTest(factory=factory):
                with VirtualMachine(output=lambda _: None) as vm:
                    vm.register_reflection_native('&Opaque', construct=factory)
                    result, = vm.run(program)
                    self.assertEqual(result.type_name, 'ConstructionError')
                    self.assertEqual(result.fields['reason'], 'NativeConstructionFailed')
                    self.assertEqual(result.fields['missing'], [])
        with VirtualMachine(output=lambda _: None) as vm:
            vm.register_reflection_native('&Opaque', construct=lambda _: FFIHandleValue('&Opaque', 5))
            result, = vm.run(program)
            self.assertEqual(result.type_name, 'OK')
            self.assertEqual(result.fields['value'].address, 5)
        direct_analyser = Analyser()
        direct = direct_analyser.analyse(parse('unsafe => uninit[&Opaque] | finish end'))
        self.assertFalse(direct_analyser.diagnostics, direct_analyser.diagnostics)
        with VirtualMachine(output=lambda _: None) as vm:
            with self.assertRaisesRegex(Exception, 'NativeConstructionFault'):
                vm.run(compile_program(direct))
        unsafe_analyser = Analyser()
        unsafe_analyser.analyse(parse('uninit[&Opaque]'))
        self.assertTrue(any('lexical unsafe' in item for item in unsafe_analyser.diagnostics))

    def test_registered_schemas_are_compiler_visible_in_snapshot(self):
        source = """unsafe =>
  $builder = uninit[RuntimeChild]
  $builder[fields[RuntimeChild]["name"]] = "Ada"
  $child = finish $builder
  $outer = uninit[RuntimeBox[RuntimeChild]]
  $outer[fields[RuntimeBox[RuntimeChild]]["value"]] = $child
  $result = finish $outer
  $result.value.name
end"""
        with VirtualMachine(output=lambda _: None) as vm:
            vm.register_reflection_type('RuntimeChild', (('name', 'String'),))
            vm.register_reflection_type('RuntimeBox', (('value', 'T'),), generics=('T',))
            analyser = Analyser(env=vm.reflection_environment())
            typed = analyser.analyse(parse(source))
            self.assertFalse(analyser.diagnostics, analyser.diagnostics)
            program = compile_program(typed)
            self.assertEqual(vm.run(loads(dumps(program))), ['Ada'])
        # Schema registrations are explicit compiler inputs, not a way to
        # retroactively change a previously compiled static program.
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(analyser.diagnostics)

    def test_native_factory_must_return_exact_live_type(self):
        with VirtualMachine(output=lambda _: None) as vm:
            vm.register_reflection_native('&Wrong', construct=lambda _: FFIHandleValue('&Other', 1))
            vm.register_reflection_native('&Dead', construct=lambda _: dead)
            dead = FFIHandleValue('&Dead', 3)
            dead.request_destroy()
            with self.assertRaisesRegex(ValueError, 'invalid'):
                vm.construct_reflected('&Wrong')
            with self.assertRaisesRegex(ValueError, 'non-live'):
                vm.construct_reflected('&Dead')
            with self.assertRaisesRegex(ValueError, 'no registered native'):
                vm.construct_reflected('&Other')
            with self.assertRaisesRegex(ValueError, 'already registered'):
                vm.register_reflection_native('&Wrong', construct=lambda _: None)
            vm.register_reflection_native('&scalar', construct=lambda v: FFIScalarValue('&scalar', v))
            self.assertEqual(vm.construct_reflected('&scalar', 42).value, 42)


if __name__ == '__main__':
    unittest.main()
