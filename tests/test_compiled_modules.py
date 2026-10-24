import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.main import main_vln
from valiance.asts import ImportPath
from valiance.modules_system.modules import ModuleLoadError, ModuleLoader
from valiance.modules_system.packages import load_manifest
from valiance.parsing import parse
from valiance.runtime import BytecodeFormatError, loads_module


class CompiledModuleTests(unittest.TestCase):
    def test_generic_union_dispatch_specializes_from_source_free_modules(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / 'reflector.vlnc'
            library.write_text('''
public define[T] nameOf(field: Field[T]) => $field.name end
public define[T] layoutOf(field: Field[T]) => fields[T] end
''', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(['compile-module', '--file', str(library),
                                          '--output', str(library.with_suffix('.vbcm'))]), 0)
            library.unlink()
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('''
import {reflector.nameOf, reflector.layoutOf}
object A => public $number: Int end
object B => public $label: String end
$fa: Field[A] | Field[B] = fields[A]["number"]
$fb: Field[A] | Field[B] = fields[B]["label"]
nameOf($fa)
nameOf($fb)
nameOf([$fa, $fb])
$layout = layoutOf($fb)
$layout["label"].name
'''))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                self.assertEqual(run(loads(dumps(compile_program(typed, optimize=optimize)))),
                                 ['number', 'label', ['number', 'label'], 'label'])

    def test_runtime_union_layout_selection_distinguishes_qualified_owner_names(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'one.vlnc').write_text('public object Owner => public $value: Int end\n', encoding='utf-8')
            (root / 'two.vlnc').write_text('public object Owner => public $value: String end\n', encoding='utf-8')
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('''
import {one, two}
define pick(flag: Int) -> one.Owner | two.Owner =>
  if $flag == 1 => one.Owner(7) else => two.Owner("hello") end
end
$owner = pick(0)
$metadata = $owner fields
$field = $metadata["value"]
$field.owner
$field.type
$owner[$field]
read?($owner, fields[one.Owner]["value"])
'''))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                stack = run(loads(dumps(compile_program(typed, optimize=optimize))))
                self.assertEqual(stack[0].type_args, ('two.Owner',))
                self.assertEqual(stack[1].type_args, ('String',))
                self.assertEqual(stack[2], 'hello')
                self.assertEqual(stack[3].fields['reason'], 'InvalidField')

    def test_runtime_union_reflection_specializes_from_source_free_modules(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / 'reflector.vlnc'
            library.write_text('''
public define[T] names(owner: T) =>
  $out: String* = []
  $owner fields foreach(field) => $out := $out append $field.name end
  $out
end
''', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(['compile-module', '--file', str(library),
                                          '--output', str(library.with_suffix('.vbcm'))]), 0)
            library.unlink()
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('''
import {reflector.names}
object A => public $number: Int end
object B => public $label: String end
define pick(flag: Int) -> A | B => if $flag == 1 => A(7) else => B("hello") end end
names(pick(1))
names(pick(0))
'''))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                self.assertEqual(run(loads(dumps(compile_program(typed, optimize=optimize)))), [['number'], ['label']])

    def test_managed_construction_preserves_namespaced_owner_identity(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'model.vlnc').write_text('public object Owner => public $value: Int end\n',
                                             encoding='utf-8')
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('''
import {model}
unsafe =>
  $builder = uninit[model.Owner]
  $builder[fields[model.Owner]["value"]] = 7
  finish $builder
end
model.Owner(7)
'''))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                built, ordinary = run(loads(dumps(compile_program(typed, optimize=optimize))))
                self.assertEqual(built.type_name, ordinary.type_name)
                self.assertEqual(built.fields, ordinary.fields)
                self.assertEqual(built.runtime_type, ordinary.runtime_type)

    def test_managed_construction_specializes_from_source_free_modules(self):
        from valiance.runtime import compile_program, dumps, loads, run
        from valiance.vtypes.symbols import Symbol

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / 'builder.vlnc'
            library.write_text('''
object[T] Box => public $value: T end
public define[T] build(value: T) =>
  unsafe =>
    $builder = uninit[Box[T]]
    $builder[fields[Box[T]]["value"]] = $value
    finish? $builder
  end
end
''', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(['compile-module', '--file', str(library),
                                          '--output', str(library.with_suffix('.vbcm'))]), 0)
            library.unlink()
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('''
import {builder.build}
$box = build("hello") ?!
$box
'''))
            self.assertEqual(analyser.diagnostics, [])
            self.assertIsNone(analyser.env.lookup_object(Symbol('Box')))
            for optimize in (False, True):
                (box,) = run(loads(dumps(compile_program(typed, optimize=optimize))))
                self.assertEqual(box.type_name, 'Box')
                self.assertEqual(box.type_args, ('String',))
                self.assertEqual(box.fields, {'value': 'hello'})

    def test_descriptor_conversion_specializes_from_source_free_modules(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / 'reflector.vlnc'
            library.write_text('public define[U] convert(value: Int, evidence: Type[U]) => '
                               '$value to[$evidence] end\n', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(['compile-module', '--file', str(library),
                                          '--output', str(library.with_suffix('.vbcm'))]), 0)
            library.unlink()
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('import {reflector.convert}\n'
                                           'object Owner => public $value: String end\n'
                                           'convert(7, fields[Owner]["value"].type)'))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                self.assertEqual(run(loads(dumps(compile_program(typed, optimize=optimize)))), ["7"])

    def test_fallible_reflection_specializes_from_source_free_modules(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / 'reflector.vlnc'
            library.write_text('''
public define[T] inspect(value: T, field: Field[T]) => read?($value, $field) end
public define[T,U] replaceValue(value: T, field: Field[T], replacement: U) =>
  write?($value, $field, $replacement)
end
''', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(['compile-module', '--file', str(library),
                                          '--output', str(library.with_suffix('.vbcm'))]), 0)
            library.unlink()
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('''
import {reflector.inspect, reflector.replaceValue}
object Box => public $value: Int end
$box = Box(7)
inspect($box, fields[Box]["value"]) ?!
$updated = replaceValue($box, fields[Box]["value"], 8) ?!
$updated.value
$box.value
'''))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                self.assertEqual(run(loads(dumps(compile_program(typed, optimize=optimize)))), [7, 8, 7])

    def test_generic_reflective_write_specializes_from_source_free_modules(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "reflector.vlnc"
            library.write_text('''public define[T, U] write(value: T, field: Field[T], replacement: U) =>
  $copy = $value
  $copy[$field] = $replacement
  $copy
end
''', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(library.with_suffix(".vbcm")),
                ]), 0)
            library.unlink()
            analyser = Analyser(source_file=root / "main.vlnc")
            typed = analyser.analyse(parse('import {reflector.write}\n'
                                           'object Box => public $value: Int end\n'
                                           '$box = Box(7)\n'
                                           '$updated = write($box, fields[Box]["value"], 8)\n'
                                           '$box.value\n$updated.value'))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                self.assertEqual(run(loads(dumps(compile_program(typed, optimize=optimize)))), [7, 8])

    def test_generic_reflection_specializes_from_source_free_modules(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "reflector.vlnc"
            library.write_text('public define[T] read(value: T, name: String) => '
                               '$value[fields[T][$name]] end\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(library.with_suffix(".vbcm")),
                ]), 0)
            library.unlink()
            analyser = Analyser(source_file=root / "main.vlnc")
            typed = analyser.analyse(parse('import {reflector.[read as inspect]}\n'
                                           'object Box => public $value: Int end\n'
                                           '$box = Box(7)\ninspect($box, "value")'))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                program = compile_program(typed, optimize=optimize)
                self.assertEqual(run(loads(dumps(program))), [7])

    def test_selected_generic_constructor_keeps_private_nested_enum_defaults(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / 'provider.vlnc'
            library.write_text('''enum Mode => First Second end
public object[T] Holder =>
  $value: T
  $modes: Mode+
  define Holder(value: T, modes: Mode+ = [Mode.First]) =>
    $self.value = $value
    $self.modes = $modes
  end
end
''', encoding='utf-8')
            for source_free in (False, True):
                if source_free:
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = main_vln([
                            'compile-module', '--file', str(library),
                            '--output', str(library.with_suffix('.vbcm')),
                        ])
                    self.assertEqual(result, 0)
                    library.unlink()
                with self.subTest(source_free=source_free):
                    analyser = Analyser(source_file=root / 'main.vlnc')
                    typed = analyser.analyse(parse('''import {provider.[Holder as Box]}
enum Mode => Local end
$input = 67
$box = Box($input)
$box.value
$box.modes
'''))
                    self.assertEqual(analyser.diagnostics, [])
                    program = compile_program(typed)
                    for candidate in (program, loads(dumps(program))):
                        values = run(candidate)
                        self.assertEqual(values[0], 67)
                        self.assertEqual(values[1][0].type_name, 'provider.Mode')
                        self.assertEqual(values[1][0].fields['name'], 'First')
                    invalid = Analyser(source_file=root / 'main.vlnc')
                    invalid.analyse(parse('''import {provider.[Holder as Box]}
enum Mode => Local end
Box(67, [Mode.Local])
'''))
                    self.assertTrue(any(
                        'no overloads' in diagnostic for diagnostic in invalid.diagnostics
                    ), invalid.diagnostics)

    def test_unqualified_imports_do_not_resolve_standard_library_modules(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            loader = ModuleLoader(base_directory=root)
            for name in ('file', 'path', 'random'):
                with self.subTest(module=name):
                    self.assertEqual(
                        loader.resolve(ImportPath((name,))), root / f'{name}.vlnc',
                    )
                    with self.assertRaises(ModuleLoadError):
                        loader.load(ImportPath((name,)))

            analyser = Analyser(source_file=root / 'main.vlnc')
            analyser.analyse(parse('''import {file.WriteFile, path.Path}
$path = Path "out.txt"
$f = WriteFile $path
$f writeLine "Hello"
$f writeLine "World"
'''))
            messages = '\n'.join(map(str, analyser.diagnostics))
            self.assertIn("module 'file' was not found", messages)
            self.assertNotIn('nativeOpen', messages)
            self.assertNotIn(str(Path(__file__).parent.parent / 'src' / 'valiance' / 'std'), messages)

    def test_local_modules_can_have_standard_library_names(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'file.vlnc').write_text(
                'public object WriteFile => $value: Int end\n', encoding='utf-8',
            )
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse(
                'import {file.WriteFile}\n$file = WriteFile 67\n$file.value'
            ))
            self.assertEqual(analyser.diagnostics, [])
            program = compile_program(typed)
            self.assertEqual(run(program), [67])
            self.assertEqual(run(loads(dumps(program))), [67])

    def test_explicit_standard_library_import_still_resolves(self):
        loader = ModuleLoader()
        exports = loader.load(ImportPath(('std', 'path')))
        self.assertEqual(exports.module_name, 'std.path')
        self.assertTrue(any(obj.name.text == 'Path' for obj in exports.public_objects()))

    def test_imported_constructor_uses_provider_private_dependencies(self):
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'shared.vlnc').write_text(
                'public object Value => $value: Int end\n', encoding='utf-8',
            )
            (root / 'wrapper.vlnc').write_text(
                'import {shared}\n'
                'public object Holder =>\n'
                '  $value: shared.Value\n'
                '  define Holder(value: shared.Value) => $self.value = $value end\n'
                'end\n', encoding='utf-8',
            )
            analyser = Analyser(source_file=root / 'main.vlnc')
            tree = analyser.analyse(parse(
                'import {wrapper, shared}\n'
                '$holder = wrapper.Holder(shared.Value(67))\n'
                '$holder.value.value'
            ))
            self.assertEqual(analyser.diagnostics, [])
            program = compile_program(tree)
            self.assertEqual(run(program), [67])
            self.assertEqual(run(loads(dumps(program))), [67])

    def test_private_import_not_exported_as_renamed_nominal_type(self):
        # Imported declarations belong to the provider's runtime prelude,
        # not the importing module's own symbol surface.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'shared.vlnc').write_text(
                'public object Thing =>\n  $value: Int\nend\n'
                'public define answer(n: Int) -> Int => $n end\n',
                encoding='utf-8',
            )
            (root / 'wrapper.vlnc').write_text(
                'import {shared}\n', encoding='utf-8',
            )
            exports = ModuleLoader(base_directory=root).load(
                ImportPath(('wrapper',)), current_file=root / 'main.vlnc'
            )
            self.assertFalse(any(obj.name.text == 'Thing' for obj in exports.objects))
            self.assertFalse(any(definition.name.text == 'answer' for definition in exports.definitions))
            # The dependency is still carried into runtime initialization.
            self.assertTrue(exports.runtime_prelude)

    def test_explicit_public_import_still_reexports_nominal_type(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'shared.vlnc').write_text(
                'public object Thing =>\n  $value: Int\nend\n'
                'public define answer(n: Int) -> Int => $n end\n',
                encoding='utf-8',
            )
            (root / 'wrapper.vlnc').write_text(
                'public import {shared}\n', encoding='utf-8',
            )
            exports = ModuleLoader(base_directory=root).load(
                ImportPath(('wrapper',)), current_file=root / 'main.vlnc'
            )
            self.assertEqual(
                [str(obj.name) for obj in exports.public_objects()],
                ['shared.Thing'],
            )
            self.assertEqual(
                [str(definition.name) for definition in exports.public_definitions()],
                ['shared.answer'],
            )

    def test_missing_module_reports_matching_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "moduleA").mkdir()

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("moduleA",)),
                    current_file=root / "main.vlnc",
                )

            message = str(raised.exception)
            self.assertIn("module 'moduleA' was not found", message)
            self.assertIn(f"found directory: {root / 'moduleA'}", message)
            self.assertIn("directories cannot be imported", message)
            self.assertIn("import a .vlnc file from the directory", message)
            self.assertNotIn("make sure the source file exists", message)

    def test_missing_module_suggests_renaming_similar_unimportable_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "json-parser.vlnc").write_text("", encoding="utf-8")

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("jsonParser",)),
                    current_file=root / "main.vlnc",
                )

            message = str(raised.exception)
            self.assertIn("json-parser.vlnc", message)
            self.assertIn("not a valid Valiance identifier", message)
            self.assertIn("rename 'json-parser.vlnc' to 'jsonParser.vlnc'", message)

    def test_missing_module_suggests_similar_importable_module(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "geometry2.vlnc").write_text("", encoding="utf-8")

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("geometry",)),
                    current_file=root / "main.vlnc",
                )

            message = str(raised.exception)
            self.assertIn("module 'geometry' was not found", message)
            self.assertIn("looked for:", message)
            self.assertIn("did you mean 'geometry2'?", message)
            self.assertNotIn("[Errno 2]", message)

    def test_missing_module_does_not_suggest_unrelated_unimportable_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "totally-unrelated.vlnc").write_text("", encoding="utf-8")

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("jsonParser",)),
                    current_file=root / "main.vlnc",
                )

            self.assertNotIn("help: rename", str(raised.exception))

    def test_compile_module_and_import_without_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "mathlib.vlnc"
            library.write_text(
                "public define triple(n: Number) -> Number => $n * 3\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(root / "mathlib.vbcm"),
                ])
            self.assertEqual(result, 0)
            library.unlink()

            main = root / "main.vlnc"
            analyser = Analyser(module_loader=ModuleLoader(), source_file=main)
            typed = analyser.analyse(parse("import { mathlib }\n5 mathlib.triple"))

            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_source_takes_precedence_over_compiled_module(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "thing.vlnc"
            source.write_text(
                "public define old(n: Number) -> Number => $n\n", encoding="utf-8"
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(source)]), 0)
            source.write_text(
                "public define fresh(n: Number) -> Number => $n\n", encoding="utf-8"
            )
            analyser = Analyser(source_file=root / "main.vlnc")
            analyser.analyse(parse("import { thing.[fresh] }"))
            self.assertEqual(analyser.diagnostics, [])

    def test_manifest_build_target_and_named_build(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "library.vlnc").write_text(
                "public define identity(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            (root / "valiance.toml").write_text(
                "[project]\nname = \"demo\"\nversion = \"0.1.0\"\n\n"
                "[entries]\nlib = \"src/library.vlnc\"\n\n"
                "[build.library]\nkind = \"module\"\nentry = \"lib\"\n"
                "output = \"dist/library.vbcm\"\noptimize = true\n",
                encoding="utf-8",
            )
            manifest = load_manifest(root)
            self.assertEqual(manifest.builds["library"].kind, "module")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = main_vln(["build", "library"])
            finally:
                os.chdir(previous)
            self.assertEqual(result, 0)
            artifact = root / "dist" / "library.vbcm"
            self.assertTrue(artifact.is_file())
            self.assertEqual(loads_module(artifact.read_bytes()).module_name, "library")

    def test_valid_analysed_interface_skips_reanalysis(self):
        from unittest.mock import patch

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "cached.vlnc"
            library.write_text(
                "public define identity(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(library)]), 0)
            main = root / "main.vlnc"
            with patch("valiance.analysis.Analyser.analyse", side_effect=AssertionError("reanalyzed")):
                exports = ModuleLoader().load(parse("import { cached }")[0].specs[0].path, current_file=main)
            self.assertEqual(exports.module_name, "cached")


    def test_compiled_module_records_canonical_dependency_interface_hashes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = root / "dependency.vlnc"
            importer = root / "importer.vlnc"
            dependency.write_text(
                "public define value(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            importer.write_text(
                "import { dependency.[value] }\n"
                "public define use(n: Number) -> Number => $n value\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(dependency)]), 0)
                self.assertEqual(main_vln(["compile-module", "--file", str(importer)]), 0)

            artifact = loads_module(importer.with_suffix(".vbcm").read_bytes())
            self.assertEqual(len(artifact.dependency_hashes), 1)
            identity, digest = artifact.dependency_hashes[0]
            self.assertEqual(identity, "local:dependency")
            self.assertNotEqual(digest, "source")
            self.assertEqual(len(digest), 64)

    def test_changed_dependency_interface_rejects_importer_artifact(self):
        from unittest.mock import patch

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = root / "dependency.vlnc"
            importer = root / "importer.vlnc"
            dependency.write_text(
                "public define value(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            importer.write_text(
                "import { dependency.[value] }\n"
                "public define use(n: Number) -> Number => $n value\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(dependency)]), 0)
                self.assertEqual(main_vln(["compile-module", "--file", str(importer)]), 0)

            dependency.write_text(
                "public define value(n: String) -> String => $n\n",
                encoding="utf-8",
            )
            original = Analyser.analyse
            with patch.object(Analyser, "analyse", autospec=True, wraps=original) as analyse:
                with self.assertRaises(ModuleLoadError):
                    ModuleLoader().load(
                        ImportPath(("importer",)),
                        current_file=root / "main.vlnc",
                    )
            self.assertGreaterEqual(analyse.call_count, 1)

    def test_interface_hash_is_canonical_and_detects_tampering(self):
        from valiance.runtime import dumps_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "hashes.vlnc"
            library.write_text(
                "public define identity(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(library)]), 0)
            data = library.with_suffix(".vbcm").read_bytes()
            module = loads_module(data)
            self.assertTrue(module.interface_hash)
            self.assertEqual(dumps_module(module), data)
            import struct
            damaged = bytearray(data)
            header = len(b"VLNCBM\x03")
            metadata_size, interface_size, _ = struct.unpack(">III", data[header:header + 12])
            self.assertGreater(interface_size, 0)
            damaged[header + 12 + metadata_size] ^= 1
            with self.assertRaises(BytecodeFormatError):
                loads_module(bytes(damaged))

    def test_source_hash_invalidates_persisted_interface(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "invalidate.vlnc"
            library.write_text(
                "public define old(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(library)]), 0)
            library.write_text(
                "public define fresh(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            analyser = Analyser(source_file=root / "main.vlnc")
            analyser.analyse(parse("import { invalidate.[fresh] }"))
            self.assertEqual(analyser.diagnostics, [])

    def test_declared_body_edit_changes_only_implementation_hash(self):
        """Keep declared public contracts stable while fingerprinting changed code."""
        from valiance.modules_system.modules import collect_module_exports
        from valiance.runtime import compile_program
        from valiance.runtime.compiled_module import build_module

        artifacts = []
        for source in (
            "public define value(n: Number) -> Number => $n\n",
            "public define value(n: Number) -> Number => $n + 1\n",
        ):
            program = parse(source)
            analyser = Analyser()
            typed = analyser.analyse(program)
            self.assertEqual(analyser.diagnostics, [])
            artifacts.append(build_module(
                "sample",
                source,
                compile_program(typed),
                analysed_interface=collect_module_exports(
                    "sample", program, typed, analyser
                ),
            ))

        self.assertEqual(artifacts[0].interface_hash, artifacts[1].interface_hash)
        self.assertNotEqual(
            artifacts[0].implementation_hash, artifacts[1].implementation_hash
        )

    def test_inferred_public_signature_changes_semantic_hash(self):
        """Invalidate importers when body inference changes a public contract."""
        from valiance.modules_system.modules import collect_module_exports
        from valiance.runtime import compile_program
        from valiance.runtime.compiled_module import build_module

        hashes = []
        for source in (
            "public define value(n) => $n + 1\n",
            'public define value(n) => $n + "one"\n',
        ):
            program = parse(source)
            analyser = Analyser()
            typed = analyser.analyse(program)
            self.assertEqual(analyser.diagnostics, [])
            artifact = build_module(
                "sample",
                source,
                compile_program(typed),
                analysed_interface=collect_module_exports(
                    "sample", program, typed, analyser
                ),
            )
            hashes.append(artifact.interface_hash)

        self.assertNotEqual(*hashes)

    def test_optimization_mode_changes_only_implementation_hash(self):
        """Treat optimizer selection as runtime input rather than semantic input."""
        from valiance.modules_system.modules import collect_module_exports
        from valiance.runtime import compile_program
        from valiance.runtime.compiled_module import build_module

        source = "public define value(n: Number) -> Number => $n + 1\n"
        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        exports = collect_module_exports("sample", program, typed, analyser)
        optimized = build_module(
            "sample",
            source,
            compile_program(typed, optimize=True),
            analysed_interface=exports,
            implementation_options="optimize=true",
        )
        direct = build_module(
            "sample",
            source,
            compile_program(typed, optimize=False),
            analysed_interface=exports,
            implementation_options="optimize=false",
        )

        self.assertEqual(optimized.interface_hash, direct.interface_hash)
        self.assertNotEqual(optimized.implementation_hash, direct.implementation_hash)

    def test_dependency_body_edit_relinks_executable_target(self):
        """Relink a target when dependency code changes but its contract does not."""
        from valiance.incremental import BuildDisposition, CompilationCoordinator

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = root / "dependency.vlnc"
            main = root / "main.vlnc"
            output = root / "bin" / "main.vbc"
            dependency.write_text(
                "public define value(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            main.write_text(
                "import { dependency.[value] }\n1 value\n",
                encoding="utf-8",
            )
            first = CompilationCoordinator(root).build_executable(
                main, output, target_identity="entry:main"
            )
            self.assertEqual(first.disposition, BuildDisposition.RELINKED)
            dependency.write_text(
                "public define value(n: Number) -> Number => $n + 1\n",
                encoding="utf-8",
            )
            second = CompilationCoordinator(root).build_executable(
                main, output, target_identity="entry:main"
            )
            self.assertEqual(second.disposition, BuildDisposition.RELINKED)

    def test_incremental_store_restores_deleted_target_output(self):
        """Restore a configured output from a verified immutable cache object."""
        from valiance.incremental import BuildDisposition, CompilationCoordinator

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.vlnc"
            output = root / "bin" / "main.vbc"
            source.write_text("1 2 +\n", encoding="utf-8")
            coordinator = CompilationCoordinator(root)
            coordinator.build_executable(source, output, target_identity="entry:main")
            expected = output.read_bytes()
            output.unlink()

            result = CompilationCoordinator(root).build_executable(
                source, output, target_identity="entry:main"
            )

            self.assertEqual(result.disposition, BuildDisposition.REUSED)
            self.assertEqual(output.read_bytes(), expected)

    def test_corrupt_cached_object_is_rebuilt_when_source_exists(self):
        """Replace a corrupt immutable object rather than publishing stale bytes."""
        import json
        from valiance.incremental import BuildDisposition, CompilationCoordinator

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.vlnc"
            output = root / "bin" / "main.vbc"
            source.write_text("1 2 +\n", encoding="utf-8")
            CompilationCoordinator(root).build_executable(
                source, output, target_identity="entry:main"
            )
            index = json.loads(
                (root / ".vln/incremental/indexes/targets").read_text(
                    encoding="utf-8"
                )
            )
            digest = index["entry:main"]["artifact"]
            object_path = root / ".vln/incremental/objects" / digest[:2] / digest[2:]
            object_path.write_bytes(b"corrupt")
            output.unlink()

            result = CompilationCoordinator(root).build_executable(
                source, output, target_identity="entry:main"
            )

            self.assertEqual(result.disposition, BuildDisposition.RELINKED)
            self.assertNotEqual(output.read_bytes(), b"corrupt")
            self.assertEqual(
                __import__("hashlib").sha256(object_path.read_bytes()).hexdigest(),
                digest,
            )

    def test_failed_rebuild_keeps_previous_target_index(self):
        """Leave the previous index and output published after current source fails."""
        from valiance.incremental import CompilationCoordinator

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.vlnc"
            output = root / "bin" / "main.vbc"
            source.write_text("1 2 +\n", encoding="utf-8")
            CompilationCoordinator(root).build_executable(
                source, output, target_identity="entry:main"
            )
            index_path = root / ".vln/incremental/indexes/targets"
            previous_index = index_path.read_bytes()
            previous_output = output.read_bytes()
            source.write_text("this is not valid => =>\n", encoding="utf-8")

            with self.assertRaises(Exception):
                CompilationCoordinator(root).build_executable(
                    source, output, target_identity="entry:main"
                )

            self.assertEqual(index_path.read_bytes(), previous_index)
            self.assertEqual(output.read_bytes(), previous_output)

    def test_atomic_output_failure_preserves_previous_bytes(self):
        """Keep a prior output readable when replacement is interrupted."""
        from unittest.mock import patch
        from valiance.incremental.store import ArtifactStore

        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "artifact.vbc"
            output.write_bytes(b"previous")
            with patch("valiance.incremental.store.os.replace", side_effect=OSError("stop")):
                with self.assertRaises(OSError):
                    ArtifactStore._atomic_write(output, b"replacement")
            self.assertEqual(output.read_bytes(), b"previous")

    def test_incremental_store_garbage_collection_preserves_reachable_objects(self):
        """Delete only immutable objects absent from module and target indexes."""
        from valiance.incremental import ArtifactStore

        with TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            reachable = store.put(b"reachable")
            unreachable = store.put(b"unreachable")
            store.publish_index("targets", {"main": {"artifact": reachable}})

            removed = store.collect_garbage()

            self.assertEqual(removed, (unreachable,))
            self.assertEqual(store.read(reachable), b"reachable")
            self.assertFalse(store.object_path(unreachable).exists())

    def test_two_module_complete_recursive_component_compiles(self):
        """Publish complete contracts instead of empty interfaces in a cycle."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.vlnc").write_text(
                "import { b.[odd] }\n"
                "public define even(n: Int) -> #boolean Int => "
                "if ($n == 0) => true else => odd($n - 1) end end\n",
                encoding="utf-8",
            )
            (root / "b.vlnc").write_text(
                "import { a.[even] }\n"
                "public define odd(n: Int) -> #boolean Int => "
                "if ($n == 0) => false else => even($n - 1) end end\n",
                encoding="utf-8",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse("import { a.[even] }\neven(8)"))
            self.assertEqual(analyser.diagnostics, [])

    def test_three_module_complete_recursive_component_compiles(self):
        """Resolve a three-member cycle with declaration-first interfaces."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.vlnc").write_text(
                "import { b.[stepB] }\n"
                "public define stepA(n: Int) -> Int => stepB($n)\n",
                encoding="utf-8",
            )
            (root / "b.vlnc").write_text(
                "import { c.[stepC] }\n"
                "public define stepB(n: Int) -> Int => stepC($n)\n",
                encoding="utf-8",
            )
            (root / "c.vlnc").write_text(
                "import { a.[stepA] }\n"
                "public define stepC(n: Int) -> Int => stepA($n)\n",
                encoding="utf-8",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse("import { a.[stepA] }\nstepA(1)"))
            self.assertEqual(analyser.diagnostics, [])

    def test_inferred_cross_module_cycle_has_stable_diagnostic(self):
        """Reject cyclic contract inference with module and declaration context."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.vlnc").write_text(
                "import { b.[stepB] }\npublic define stepA(n) => stepB($n)\n",
                encoding="utf-8",
            )
            (root / "b.vlnc").write_text(
                "import { a.[stepA] }\npublic define stepB(n) => stepA($n)\n",
                encoding="utf-8",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse("import { a.[stepA] }"))
            rendered = "\n".join(analyser.diagnostics)
            self.assertIn("cross-module recursive declarations require complete", rendered)
            self.assertIn("stepA", rendered)
            self.assertIn("a -> b -> a", rendered)

    def test_module_graph_reports_one_cycle_component(self):
        """Discover deterministic strongly connected module components."""
        from valiance.incremental import (
            discover_module_graph,
            strongly_connected_components,
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.vlnc").write_text("import { b }\n", encoding="utf-8")
            (root / "b.vlnc").write_text("import { a }\n", encoding="utf-8")
            graph = discover_module_graph(root / "a.vlnc", ModuleLoader())
            components = strongly_connected_components(graph)
            cycle = next(component for component in components if component.cyclic)
            self.assertEqual(
                tuple(path.name for path in cycle.members), ("a.vlnc", "b.vlnc")
            )

    def test_interface_schema_is_canonical_and_not_pickle(self):
        """Encode interfaces deterministically without executable pickle payloads."""
        import subprocess
        import sys
        from valiance.modules_system.modules import ModuleExports
        from valiance.runtime.interface_serialization import dumps_interface

        exports = ModuleExports("sample")
        first = dumps_interface(exports)
        second = dumps_interface(exports)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(b"VLNI\x09"))
        self.assertNotIn(b"pickle", first.lower())
        command = (
            "from valiance.modules_system.modules import ModuleExports; "
            "from valiance.runtime.interface_serialization import dumps_interface; "
            "import sys; sys.stdout.buffer.write(dumps_interface(ModuleExports('sample')))"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src:."
        separate = subprocess.check_output([sys.executable, "-c", command], env=environment)
        self.assertEqual(first, separate)

    def test_interface_schema_rejects_unknown_tags_and_trailing_data(self):
        """Reject unrecognized schema variants and extra bytes."""
        from valiance.runtime.interface_serialization import loads_interface

        with self.assertRaises(BytecodeFormatError):
            loads_interface(b'VLNI\x09{"schema":9,"value":{"$":"future"}}')
        with self.assertRaises(BytecodeFormatError):
            loads_interface(
                b'VLNI\x09{"schema":9,"value":null}trailing'
            )

    def test_interface_schema_rejects_truncated_record(self):
        """Reject incomplete explicit interface documents."""
        from valiance.runtime.interface_serialization import loads_interface

        with self.assertRaises(BytecodeFormatError):
            loads_interface(b'VLNI\x09{"schema":9,"value":')

    def test_semantic_hash_ignores_source_locations(self):
        """Exclude diagnostic coordinates from canonical semantic identity."""
        from dataclasses import replace
        from valiance.asts import NumberLiteralNode, SourceLocation
        from valiance.runtime.compiled_module import interface_hash

        left = NumberLiteralNode(1, location=SourceLocation(1, 1, 0))
        right = replace(left, location=SourceLocation(9, 4, 120))
        self.assertEqual(interface_hash(left), interface_hash(right))

    def test_vbcm_is_not_plain_vbc(self):
        with self.assertRaises(BytecodeFormatError):
            loads_module(b"VLNCBC\\x16")


    def test_compiled_module_exports_mutually_recursive_definitions(self):
        """Preserve declaration prescanning through module compilation and import."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "parity.vlnc"
            library.write_text(
                "public define even(n: Int) -> #boolean Int => "
                "if ($n == 0) => true else => odd($n - 1) end end\n"
                "public define odd(n: Int) -> #boolean Int => "
                "if ($n == 0) => false else => even($n - 1) end end\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(root / "parity.vbcm"),
                ])
            self.assertEqual(result, 0)
            library.unlink()

            main = root / "main.vlnc"
            analyser = Analyser(module_loader=ModuleLoader(), source_file=main)
            typed = analyser.analyse(parse("import { parity }\nparity.even(8)"))
            self.assertEqual(analyser.diagnostics, [])
            self.assertIsNotNone(typed[-1].typ)


if __name__ == "__main__":
    unittest.main()


class CompiledGenericTraitImplementationTests(unittest.TestCase):
    def _compile_without_source(self, root: Path, source: str) -> None:
        library = root / "generic.vlnc"
        library.write_text(source, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(root / "generic.vbcm"),
                ]),
                0,
            )
        library.unlink()

    def test_generic_trait_implication_round_trips_without_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compile_without_source(
                root,
                "public trait[T] Producer => end\n"
                "public trait[T] Iterable => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n"
                "trait[T] Producer as Iterable[T] => end\n",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { generic.[Box, Producer, Iterable] }\n"
                "Box(1) as[Iterable[Int]]\n"
            ))
            self.assertEqual(analyser.diagnostics, [])

    def test_compiled_generic_trait_implication_preserves_correlation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compile_without_source(
                root,
                "public trait[T] Producer => end\n"
                "public trait[T] Iterable => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n"
                "trait[T] Producer as Iterable[T] => end\n",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { generic.[Box, Producer, Iterable] }\n"
                'Box("value") as[Iterable[Int]]\n'
            ))
            self.assertTrue(any(
                "cannot safely cast Box[String] to Iterable[Int]" in diagnostic
                for diagnostic in analyser.diagnostics
            ))

    def test_compiled_generic_trait_implication_preserves_constraints(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compile_without_source(
                root,
                "public trait[T] Producer => end\n"
                "public trait[T] Iterable => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n"
                "trait[T: Number] Producer as Iterable[T] => end\n",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { generic.[Box, Producer, Iterable] }\n"
                'Box("value") as[Iterable[String]]\n'
            ))
            self.assertTrue(any(
                "cannot safely cast Box[String] to Iterable[String]" in diagnostic
                for diagnostic in analyser.diagnostics
            ))
