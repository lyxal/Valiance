"""Language-facing filesystem and ownership integration tests."""
import os
import json
import tempfile
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run, RuntimeError as ValianceRuntimeError
from valiance.runtime.runtime_values import PanicSignal
from valiance.runtime.runtime_values import ObjectValue, TaggedValue, unwrap_runtime_value
from valiance.std import file as file_backend
from valiance.std.path import wrap_path, native_path
from valiance.std._filesystem_core import Path as NativePath


class FileLanguageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.addCleanup(file_backend._handles.clear)
        self.assertFalse(file_backend._handles)

    def _path(self, filename='data.txt'):
        return json.dumps(os.path.join(self._temp.name, filename))

    def _run(self, body):
        analyser = Analyser()
        tree = analyser.analyse(parse('import {std.path, std.file}\n' + body))
        self.assertFalse(analyser.diagnostics, '\n'.join(map(str, analyser.diagnostics)))
        result = run(compile_program(tree, optimize=False))
        return result, analyser

    def test_raii_write_and_append(self):
        path = self._path()
        self._run(f'file.WriteFile(path = path.Path({path})) | file.writeLine "alpha" | pop')
        self.assertFalse(file_backend._handles)
        self._run(f'file.AppendFile(path = path.Path({path})) | file.writeLine "beta" | pop')
        self.assertFalse(file_backend._handles)
        with open(os.path.join(self._temp.name, 'data.txt'), 'rb') as stream:
            self.assertEqual(stream.read(), b'alpha\nbeta\n')

    def test_selected_writer_import_preserves_private_constructor_defaults(self):
        for imports, constructor, path_constructor in (
            ('std.file.WriteFile, std.path.Path', 'WriteFile', 'Path'),
            ('std.path.Path, std.file.WriteFile', 'WriteFile', 'Path'),
            ('std.file.[WriteFile as Writer], std.path.[Path as Location]', 'Writer', 'Location'),
        ):
            for serialized in (False, True):
                with self.subTest(imports=imports, serialized=serialized):
                    analyser = Analyser()
                    source = (
                        f'import {{{imports}}}\n'
                        'define \\write -> =>\n'
                        f'$path = {path_constructor} {self._path()}\n'
                        f'$f = {constructor} $path\n'
                        '$f = $f writeLine "Hello"\n'
                        '$f = $f writeLine "World"\n'
                        'end\n\\write\n'
                    )
                    tree = analyser.analyse(parse(source))
                    self.assertEqual(analyser.diagnostics, [])
                    self.assertEqual(analyser.warnings, [])
                    program = compile_program(tree)
                    run(loads(dumps(program)) if serialized else program)
                    self.assertFalse(file_backend._handles)
                    with open(os.path.join(self._temp.name, 'data.txt'), 'rb') as stream:
                        self.assertEqual(stream.read(), b'Hello\nWorld\n')

    def test_selected_writer_import_does_not_expose_sibling_enum(self):
        for expression in ('file.Encoding.UTF8', 'Encoding.UTF8'):
            with self.subTest(expression=expression):
                analyser = Analyser()
                analyser.analyse(parse('import {std.file.WriteFile}\n' + expression))
                self.assertTrue(any(
                    f"unknown element '{expression}'" in diagnostic
                    for diagnostic in analyser.diagnostics
                ), analyser.diagnostics)

    def test_selected_path_warns_only_when_converted_to_string(self):
        analyser = Analyser()
        analyser.analyse(parse('import {std.path.Path, std.path.to}\nPath "example" | to[String]'))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(len(analyser.warnings), 1)
        self.assertIn('may lose non-Unicode filename data', analyser.warnings[0])

    def test_selected_writer_reported_program_has_no_import_diagnostics(self):
        analyser = Analyser()
        analyser.analyse(parse('''import {std.file.WriteFile, std.path.Path}
$path = Path "out.txt"
$f = WriteFile $path
$f writeLine "Hello"
$f writeLine "World"
'''))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.warnings, [])

    def test_named_writer_calls_borrow_and_leave_the_file_available(self):
        for optimize in (False, True):
            for serialized in (False, True):
                with self.subTest(optimize=optimize, serialized=serialized):
                    analyser = Analyser()
                    tree = analyser.analyse(parse(
                        'import {std.file.WriteFile, std.path.Path}\n'
                        f'$path = Path {self._path()}\n'
                        '$f = WriteFile $path\n'
                        '$f writeLine "Hello" | writeLine "World"\n'
                        '$f writeLine "Again"\n'
                    ))
                    self.assertEqual(analyser.diagnostics, [])
                    self.assertEqual(analyser.warnings, [])
                    program = compile_program(tree, optimize=optimize)
                    try:
                        stack = run(loads(dumps(program)) if serialized else program)
                        self.assertEqual(len(stack), 2)
                        owner = unwrap_runtime_value(stack[0])
                        self.assertIs(unwrap_runtime_value(stack[1]), owner)
                        self.assertEqual(owner.refcount, 1)
                        self.assertFalse(owner.destroyed)
                    finally:
                        for handle in tuple(file_backend._handles.values()):
                            handle.close()
                        file_backend._handles.clear()
                    with open(os.path.join(self._temp.name, 'data.txt'), 'rb') as stream:
                        self.assertEqual(stream.read(), b'Hello\nWorld\nAgain\n')

    def test_file_imports_do_not_depend_on_path_import_order(self):
        for imports, constructor, module in (
            ('std.file, std.path', 'path.Path', 'file'),
            ('std.path, std.file', 'path.Path', 'file'),
            ('std.file, std.path.Path', 'Path', 'file'),
            ('std.path.Path, std.file', 'Path', 'file'),
            ('std.file, std.path.[Path as Location]', 'Location', 'file'),
            ('std.file as io, std.path as paths', 'paths.Path', 'io'),
        ):
            for serialized in (False, True):
                with self.subTest(imports=imports, serialized=serialized):
                    filename = f'import-{serialized}.txt'
                    analyser = Analyser()
                    tree = analyser.analyse(parse(
                        f'import {{{imports}}}\n'
                        'define \\write -> =>\n'
                        f'$res = {module}.WriteFile({constructor} {self._path(filename)})\n'
                        f'$res = $res {module}.writeLine "67"\n'
                        'end\n\\write'
                    ))
                    self.assertFalse(analyser.diagnostics, analyser.diagnostics)
                    program = compile_program(tree)
                    if serialized:
                        program = loads(dumps(program))
                    run(program)
                    self.assertFalse(file_backend._handles)
                    with open(os.path.join(self._temp.name, filename), 'rb') as stream:
                        self.assertEqual(stream.read(), b'67\n')
                    os.remove(os.path.join(self._temp.name, filename))

    def test_selected_path_alias_in_nested_signature_and_field_types(self):
        analyser = Analyser()
        tree = analyser.analyse(parse(
            'import {std.path.[Path as Location]}\n'
            'object Container => $paths: Location+ end\n'
            'define identity(paths: Location+) -> Location+ => $paths end\n'
            'Container(identity([Location "first", Location "second"]))'
        ))
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        values = run(compile_program(tree))
        paths = values[0].fields['paths']
        self.assertEqual([value.type_name for value in paths], ['path.Path', 'path.Path'])

    def test_file_dependencies_are_not_visible_to_importer(self):
        analyser = Analyser()
        analyser.analyse(parse('import {std.file}\npath.Path "ignored"'))
        self.assertTrue(any(
            "unknown element 'path.Path'" in str(diagnostic)
            for diagnostic in analyser.diagnostics
        ), analyser.diagnostics)
        self.assertFalse(any(
            'undefined type' in str(diagnostic)
            for diagnostic in analyser.diagnostics
        ), analyser.diagnostics)

    def test_selected_stdlib_aliases_in_annotations_casts_and_patterns(self):
        analyser = Analyser()
        tree = analyser.analyse(parse(
            'import {std.path.[Path as Location], std.file.Encoding}\n'
            '$p: Location = Location "first"\n'
            '($p as[Location]) match =>\n'
            '  as value: Location => 67\n'
            '  _ => 0\n'
            'end\n'
            'Encoding.UTF8'
        ))
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        program = compile_program(tree)
        for candidate in (program, loads(dumps(program))):
            values = run(candidate)
            self.assertEqual(values[0], 67)
            self.assertEqual(values[1].type_name, 'file.Encoding')
            self.assertEqual(values[1].fields['name'], 'UTF8')
    def test_default_encoding_constructors_accept_positional_path(self):
        # A single explicit argument binds to `path`; the trailing enum
        # default is filled, rather than treating `path` as `encoding`.
        path = self._path()
        self._run(f'file.WriteFile(path.Path({path})) | file.writeLine "café" | pop')
        self._run(f'file.AppendFile(path.Path({path})) | file.writeLine "second" | pop')
        contents, _ = self._run(
            f'file.ReadFile(path.Path({path})) | file.readText'
        )
        self.assertEqual(contents[1], 'café\nsecond\n')
        for handle in tuple(file_backend._handles.values()):
            handle.close()
        file_backend._handles.clear()
        with open(os.path.join(self._temp.name, 'data.txt'), 'rb') as stream:
            self.assertEqual(stream.read(), 'café\nsecond\n'.encode('utf-8'))
        self.assertFalse(file_backend._handles)

    def test_two_positional_constructor_arguments_override_default(self):
        path = self._path()
        self._run(
            f'file.WriteFile(path.Path({path}), file.Encoding.UTF16BE) '
            '| file.writeText "hi" | pop'
        )
        self._run(
            f'file.AppendFile(path.Path({path}), file.Encoding.UTF16BE) '
            '| file.writeText "!" | pop'
        )
        with open(os.path.join(self._temp.name, 'data.txt'), 'rb') as stream:
            self.assertEqual(stream.read(), b'\x00h\x00i\x00!')
        self.assertFalse(file_backend._handles)

    def test_encoding_constructor_parameter_is_nominal(self):
        analyser = Analyser()
        analyser.analyse(parse(
            'import {std.path, std.file}\n'
            'file.WriteFile(path.Path("ignored"), 123) | pop'
        ))
        self.assertTrue(analyser.diagnostics)
        self.assertTrue(any(
            'no overloads' in str(diagnostic)
            for diagnostic in analyser.diagnostics
        ))

    def test_file_constructors_require_nominal_path(self):
        # A path cannot be substituted by String, Int, or the Encoding enum.
        for constructor, invalid in (
            ('ReadFile', '123'),
            ('WriteFile', '"filename.txt"'),
            ('AppendFile', 'file.Encoding.UTF8'),
        ):
            with self.subTest(constructor=constructor):
                analyser = Analyser()
                analyser.analyse(parse(
                    'import {std.path, std.file}\n'
                    f'file.{constructor}({invalid})'
                ))
                self.assertTrue(analyser.diagnostics)
                self.assertTrue(any(
                    'no overloads' in str(diagnostic)
                    and 'path.Path' in str(diagnostic)
                    for diagnostic in analyser.diagnostics
                ), analyser.diagnostics)

    def test_nominal_path_and_encoding_named_arguments(self):
        location = self._path()
        self._run(
            f'file.WriteFile(path = path.Path({location}), '
            'encoding = file.Encoding.UTF16LE) | file.writeText "ok" | pop'
        )
        self._run(
            f'file.AppendFile(path = path.Path({location}), '
            'encoding = file.Encoding.UTF16LE) | file.writeText "!" | pop'
        )
        values, _ = self._run(
            f'file.ReadFile(path = path.Path({location}), '
            'encoding = file.Encoding.UTF16LE) | file.readText'
        )
        self.assertEqual(values[1], 'ok!')
        for handle in tuple(file_backend._handles.values()):
            handle.close()
        file_backend._handles.clear()

    def test_reader_remaining_resource_and_line(self):
        p = os.path.join(self._temp.name, 'data.txt')
        with open(p, 'wb') as stream:
            stream.write(b'a\r\nb\n')
        stack, _ = self._run(f'file.ReadFile(path = path.Path({self._path()})) | file.readLine')
        self.assertEqual(len(stack), 2)
        self.assertEqual(stack[0].type_name, 'file.ReadFile')
        self.assertIsInstance(stack[1], ObjectValue)
        self.assertEqual(stack[1].type_name, 'Some')
        self.assertEqual(stack[1].fields['value'], 'a')
        # The VM returns the reader to the caller; this test must dispose it.
        for handle in tuple(file_backend._handles.values()):
            handle.close()
        file_backend._handles.clear()

    def test_named_encoding_enum_and_text(self):
        path = self._path()
        self._run(f'file.WriteFile(path = path.Path({path}), encoding = file.Encoding.UTF16LE) | file.writeText "hi" | pop')
        with open(os.path.join(self._temp.name, 'data.txt'), 'rb') as stream:
            self.assertEqual(stream.read(), b'h\x00i\x00')

    def test_one_shot_path_helpers(self):
        path = self._path()
        self._run(f'file.do.writeText(path.Path({path}), "hello")')
        values, _ = self._run(f'file.do.readText(path.Path({path}))')
        self.assertEqual(values, ['hello'])
        self._run(f'file.do.appendText(path.Path({path}), "!")')
        values, _ = self._run(f'file.do.readText(path.Path({path}))')
        self.assertEqual(values, ['hello!'])
        self.assertFalse(file_backend._handles)

    def test_one_shot_text_helpers_accept_optional_encodings(self):
        path = self._path('encoded.txt')
        p = os.path.join(self._temp.name, 'encoded.txt')
        self._run(
            f'file.do.writeText(path.Path({path}), "café", file.Encoding.UTF16LE)'
        )
        with open(p, 'rb') as stream:
            self.assertEqual(stream.read(), 'café'.encode('utf-16-le'))
        self._run(
            f'file.do.appendText(path.Path({path}), "!", file.Encoding.UTF16LE)'
        )
        result, _ = self._run(
            f'file.do.readText(path.Path({path}), file.Encoding.UTF16LE)'
        )
        self.assertEqual(result, ['café!'])
        result, _ = self._run(
            f'file.do.readText(path = path.Path({path}), encoding = file.Encoding.UTF16LE)'
        )
        self.assertEqual(result, ['café!'])
        self.assertFalse(file_backend._handles)

    def test_one_shot_line_helpers_accept_optional_encodings(self):
        path = self._path('lines.txt')
        p = os.path.join(self._temp.name, 'lines.txt')
        self._run(
            f'file.do.writeLines(path.Path({path}), ["alpha", "β"], file.Encoding.UTF16BE)'
        )
        self._run(
            f'file.do.appendLines(path.Path({path}), ["γ"], file.Encoding.UTF16BE)'
        )
        with open(p, 'rb') as stream:
            self.assertEqual(stream.read(), 'alpha\nβ\nγ\n'.encode('utf-16-be'))
        result, _ = self._run(
            f'file.do.readLines(path.Path({path}), file.Encoding.UTF16BE)'
        )
        self.assertEqual(list(result[0]), ['alpha', 'β', 'γ'])
        self.assertFalse(file_backend._handles)

    def test_one_shot_encoding_rejects_invalid_type_at_compile_time(self):
        analyser = Analyser()
        analyser.analyse(parse(
            'import {std.path, std.file}\n'
            'file.do.writeText(path.Path("unused"), "hello", "UTF8")'
        ))
        self.assertTrue(analyser.diagnostics)
        self.assertIn('no overloads', ' '.join(map(str, analyser.diagnostics)))

    def test_nominal_path_operations_and_optional_components(self):
        def component(expression):
            result, _ = self._run(expression)
            return result[0]

        self.assertEqual(component('path.parent(path.Path("leaf"))').type_name, 'None')
        self.assertEqual(component('path.parent(path.Path("/"))').type_name, 'None')
        parent = component('path.parent(path.Path("dir/leaf"))')
        self.assertEqual(parent.type_name, 'Some')
        self.assertEqual(parent.fields['value'].type_name, 'path.Path')
        self.assertEqual(native_path(parent.fields['value']).native, ('dir' if os.name == 'nt' else b'dir'))
        self.assertEqual(component('path.name(path.Path("/"))').type_name, 'None')
        name = component('path.name(path.Path("dir/leaf/"))')
        self.assertEqual(native_path(name.fields['value']).native, ('leaf' if os.name == 'nt' else b'leaf'))
        self.assertEqual(component('path.extension(path.Path("no-extension"))').type_name, 'None')
        extension = component('path.extension(path.Path("dir/test.tar.gz"))')
        self.assertEqual(native_path(extension.fields['value']).native, ('.gz' if os.name == 'nt' else b'.gz'))
        joined = component('path.join(path.Path("dir"), path.Path("leaf"))')
        self.assertEqual(joined.type_name, 'path.Path')
        self.assertEqual(native_path(joined).native, (os.path.join('dir', 'leaf') if os.name == 'nt' else os.fsencode(os.path.join('dir', 'leaf'))))

    def test_path_operations_reject_strings_at_analysis_time(self):
        for expression in (
            'path.join("directory", path.Path("file"))',
            'path.parent("file")',
            'path.name("file")',
            'path.extension("file")',
        ):
            with self.subTest(expression=expression):
                analyser = Analyser()
                analyser.analyse(parse('import {std.path}\n' + expression))
                self.assertTrue(analyser.diagnostics)
                self.assertIn('no overloads', ' '.join(map(str, analyser.diagnostics)))

    def test_standalone_exact_byte_count(self):
        with open(os.path.join(self._temp.name, 'data.txt'), 'wb') as stream:
            stream.write(b'abc')
        values, _ = self._run(f'file.do.readBytes(path.Path({self._path()}))')
        self.assertIsInstance(values[0], TaggedValue)
        self.assertEqual([int(x) for x in unwrap_runtime_value(values[0])], [97, 98, 99])

    def test_do_submodule_can_be_imported_directly(self):
        path = self._path()
        analyser = Analyser()
        tree = analyser.analyse(parse(
            'import {std.path, std.file.do}\n'
            f'do.writeText(path.Path({path}), "direct")\n'
            f'do.readText(path.Path({path}))'
        ))
        self.assertFalse(analyser.diagnostics, '\n'.join(map(str, analyser.diagnostics)))
        self.assertEqual(run(compile_program(tree, optimize=False)), ['direct'])
        self.assertFalse(file_backend._handles)

    def test_do_submodule_is_exposed_via_parent_file_namespace(self):
        path = self._path()
        self._run(f'file.do.writeLines(path.Path({path}), ["a", "b"])')
        values, _ = self._run(f'file.do.readLines(path.Path({path}))')
        self.assertEqual(list(values[0]), ['a', 'b'])
        self._run(f'file.do.appendLines(path.Path({path}), ["c"])')
        values, _ = self._run(f'file.do.readText(path.Path({path}))')
        self.assertEqual(values, ['a\nb\nc\n'])
        self.assertFalse(file_backend._handles)

    def test_do_byte_operations_are_single_action(self):
        path = self._path()
        self._run(f'import {{std.file.#byte}}\nfile.do.writeBytes(path.Path({path}), [65 #byte, 66 #byte])')
        self._run(f'import {{std.file.#byte}}\nfile.do.appendBytes(path.Path({path}), [67 #byte])')
        values, _ = self._run(f'file.do.readBytes(path.Path({path}))')
        self.assertIsInstance(values[0], TaggedValue)
        self.assertEqual([int(x) for x in unwrap_runtime_value(values[0])], [65, 66, 67])
        self.assertEqual({tag.name for tag in values[0].tags}, {'byte'})
        self.assertFalse(file_backend._handles)

    def test_read_lines_maps_complete_tab_separated_rows(self):
        path = self._path()
        readers = (
            ('std.file.do, std.path.Path', f'do.readLines(Path {path})'),
            ('std.file, std.path', f'file.do.readLines(path.Path({path}))'),
            ('std.file, std.path',
             f'$reader = file.ReadFile(path.Path({path}))\n'
             '$reader file.readLines\n$reader file.close'),
        )
        fixtures = (
            (b'AB\t10\t20\t30\t40\t50\t60\r\nCD\t11\t21\t31\t41\t51\t61',
             [{'code': 'AB', 'raw': '40', 'factor': '60'},
              {'code': 'CD', 'raw': '41', 'factor': '61'}]),
            (b'', []),
            (b'AB\t10', None),
        )
        for imports, reader in readers:
            for contents, expected in fixtures:
                with open(os.path.join(self._temp.name, 'data.txt'), 'wb') as stream:
                    stream.write(contents)
                analyser = Analyser()
                tree = analyser.analyse(parse(
                    f'import {{{imports}}}\n{reader}\n' + r'''
map fn (row) =>
  const $items = $row split "\t"
  record{
    code => $items[0],
    raw => $items[4],
    factor => $items[6]
  }
end
'''))
                self.assertEqual(analyser.diagnostics, [])
                for optimize in (False, True):
                    program = compile_program(tree, optimize=optimize)
                    for serialized in (False, True):
                        with self.subTest(reader=reader, contents=contents,
                                          optimize=optimize, serialized=serialized):
                            executable = loads(dumps(program)) if serialized else program
                            if expected is None:
                                with self.assertRaises(PanicSignal) as caught:
                                    list(run(executable)[-1])
                                self.assertEqual(caught.exception.value.type_name, 'IndexFault')
                                self.assertEqual(caught.exception.value.fields['message'],
                                                 'index 4 is out of range for length 2')
                            else:
                                self.assertEqual(list(run(executable)[-1]), expected)
                            self.assertFalse(file_backend._handles)

    def test_open_failure_is_catchable_during_partial_construction(self):
        path = self._path('absent.txt')
        values, _ = self._run(f'''try =>
  file.ReadFile(path.Path({path})) | pop
handle NotFoundFault =>
  "missing"
end''')
        self.assertEqual(values, ['missing'])
        self.assertFalse(file_backend._handles)

    def test_one_shot_panic_can_be_handled_by_valiance(self):
        path = self._path('absent.txt')
        values, _ = self._run(f'''try =>
  file.do.readText(path.Path({path}))
handle NotFoundFault =>
  "missing"
end''')
        self.assertEqual(values, ['missing'])
        self.assertFalse(file_backend._handles)

    def test_missing_file_is_a_catchable_not_found_panic(self):
        with self.assertRaises(ValianceRuntimeError) as raised:
            self._run(
                f'file.do.readText(path.Path({self._path("absent.txt")}))'
            )
        self.assertIn('uncaught panic: NotFoundFault', str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, PanicSignal)
        self.assertEqual(raised.exception.__cause__.value.type_name, 'NotFoundFault')
        self.assertFalse(file_backend._handles)

    def test_exact_read_eof_is_an_unexpected_eof_panic_and_closes(self):
        p = os.path.join(self._temp.name, 'data.txt')
        with open(p, 'wb') as stream:
            stream.write(b'a')
        with self.assertRaises(ValianceRuntimeError) as raised:
            self._run(f'file.ReadFile(path.Path({self._path()})) | file.readBytes 2')
        self.assertIn('uncaught panic: UnexpectedEOFFault', str(raised.exception))
        self.assertEqual(raised.exception.__cause__.value.type_name, 'UnexpectedEOFFault')
        self.assertFalse(file_backend._handles)

    def test_invalid_utf8_is_a_parse_panic(self):
        p = os.path.join(self._temp.name, 'data.txt')
        with open(p, 'wb') as stream:
            stream.write(b'\xff')
        with self.assertRaises(ValianceRuntimeError) as raised:
            self._run(f'file.do.readText(path.Path({self._path()}))')
        self.assertEqual(raised.exception.__cause__.value.type_name, 'ParseFault')
        self.assertFalse(file_backend._handles)

    def test_imported_byte_unit_validator_rejects_out_of_range_values(self):
        for value in (0, 255):
            result, _ = self._run(
                f'import {{std.file.#byte}}\n{value} #byte'
            )
            self.assertIsInstance(result[0], TaggedValue)
        for value in (-1, 256):
            with self.subTest(value=value):
                with self.assertRaises(ValianceRuntimeError) as raised:
                    self._run(f'import {{std.file.#byte}}\n{value} #byte')
                self.assertIn('tag validator', str(raised.exception))

    def test_byte_writers_require_unit_tag_at_analysis_time(self):
        analyser = Analyser()
        analyser.analyse(parse(
            'import {std.path, std.file}\n'
            'file.do.writeBytes(path.Path("unused"), [1, 2])'
        ))
        self.assertTrue(analyser.diagnostics)
        self.assertIn('no overloads', ' '.join(map(str, analyser.diagnostics)))

    def test_compiler_warns_about_lossy_string_conversion(self):
        result, analyser = self._run('path.Path("hello") path.to[String]')
        self.assertEqual(result, ['hello'])
        self.assertTrue(any('may lose' in str(warning) for warning in analyser.warnings), analyser.warnings)

    @unittest.skipIf(os.name == 'nt', 'POSIX filename bytes are platform-specific')
    def test_non_utf8_file_name_roundtrips(self):
        native = os.fsencode(self._temp.name) + b'/bad-\xff'
        wrapped = wrap_path(NativePath(native))
        self.assertEqual(native_path(wrapped).native, native)
        with open(native, 'wb') as stream:
            stream.write(b'ok')
        with open(native_path(wrapped).native, 'rb') as stream:
            self.assertEqual(stream.read(), b'ok')


if __name__ == '__main__':
    unittest.main()
