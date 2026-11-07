import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.asts import ImportPath
from valiance.main import main_vln
from valiance.modules_system.modules import ModuleLoader
from valiance.modules_system.runtime_dependencies import resolve_runtime_dependencies
from valiance.incremental.coordinator import CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import compile_program, run
from valiance.vtypes.symbols import Symbol


class PublicReExportTests(unittest.TestCase):
    def analyse(self, root: Path, source: str):
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        return typed

    def test_exported_field_retains_qualified_variant_case_type(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "library.vlnc").write_text('''
public variant Entry => Shortcut => $target: String end end
public object Box => $entries: Shortcut+ end
public define readEntries(box: Box) -> Shortcut+ => $box.entries end
''', encoding="utf-8")

            typed = self.analyse(root, '''
import { library.[Entry, Box, readEntries] }
$entries = readEntries(Box([Entry.Shortcut("file.txt")]))
$shortcut = $entries $[0]
$shortcut.target
''')
            self.assertEqual(run(compile_program(typed)), ["file.txt"])

    def test_selective_public_import_reexports_function(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[double] }\n",
                encoding="utf-8",
            )

            typed = self.analyse(root, "import { facade.[double] }\n4 double")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_selective_public_import_preserves_alias(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[double as twice] }\n",
                encoding="utf-8",
            )

            typed = self.analyse(root, "import { facade.[twice] }\n4 twice")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_public_namespace_import_reexports_qualified_surface(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal as api }\n",
                encoding="utf-8",
            )

            typed = self.analyse(root, "import { facade }\n4 facade.api.double")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_private_import_is_not_reexported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "import { internal.[double] }\n",
                encoding="utf-8",
            )
            analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
            analyser.analyse(parse("import { facade.[double] }"))
            self.assertTrue(any("no public component 'double'" in d for d in analyser.diagnostics))

    def test_public_import_reexports_object(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public object Box =>\n  $value: Number\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[Box] }\n",
                encoding="utf-8",
            )

            exports = ModuleLoader().load(
                ImportPath(("facade",)),
                current_file=root / "main.vlnc",
            )
            self.assertIn(Symbol("Box"), {obj.name for obj in exports.public_objects()})

    def test_public_import_reexports_tag_and_overlay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tags.vlnc").write_text(
                "tag #sorted as computed\n"
                "#sorted: + =>\n"
                "  (#sorted Number, Number) -> #sorted Number\n"
                "end\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { tags.#sorted }\n",
                encoding="utf-8",
            )

            loader = ModuleLoader()
            exports = loader.load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            self.assertIn(Symbol("sorted"), {tag.name for tag in exports.tags})
            self.assertTrue(any(overlay.tag == Symbol("sorted") for overlay in exports.overlays))
            self.analyse(root, "import { facade.#sorted }\n1 #sorted | 2 +")

    def test_public_import_inside_function_does_not_reexport(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "define use(n: Number) -> Number =>\n"
                "  public import { internal.[double] }\n"
                "  $n double\n"
                "end\n",
                encoding="utf-8",
            )
            exports = ModuleLoader().load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            self.assertNotIn(Symbol("double"), {d.name for d in exports.public_definitions()})

    def test_public_import_in_conditional_is_not_reexported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "if true =>\n"
                "  public import { internal.[double] }\n"
                "end\n",
                encoding="utf-8",
            )
            exports = ModuleLoader().load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            self.assertNotIn(Symbol("double"), {d.name for d in exports.public_definitions()})

    def test_repeated_public_import_is_deduplicated(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[double] }\n"
                "public import { internal.[double] }\n",
                encoding="utf-8",
            )
            exports = ModuleLoader().load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            matches = [d for d in exports.public_definitions() if d.name == Symbol("double")]
            self.assertEqual(len(matches), 1)

    def test_two_layer_selective_reexport(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "middle.vlnc").write_text(
                "public import { internal.[double] }\n", encoding="utf-8"
            )
            (root / "facade.vlnc").write_text(
                "public import { middle.[double] }\n", encoding="utf-8"
            )
            typed = self.analyse(root, "import { facade.[double] }\n4 double")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_compiled_facade_preserves_public_reexport(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            internal = root / "internal.vlnc"
            facade = root / "facade.vlnc"
            internal.write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            facade.write_text(
                "public import { internal.[double] }\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main_vln([
                    "compile-module", "--file", str(facade),
                    "--output", str(root / "facade.vbcm"),
                ])
            self.assertEqual(result, 0)
            facade.unlink()
            internal.unlink()

            typed = self.analyse(root, "import { facade.[double] }\n4 double")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_public_import_reexports_selected_overload_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "overload(String -> String)\n"
                "overload(Number -> Number)\n"
                "public define identity(value) => $value\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[identity(Number)] }\n",
                encoding="utf-8",
            )
            numeric = self.analyse(root, "import { facade.[identity] }\n4 identity")
            self.assertEqual(str(numeric[-1].typ.name), "Number")

            analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
            analyser.analyse(parse('import { facade.[identity] }\n"x" identity'))
            self.assertTrue(any("no overload" in d.lower() for d in analyser.diagnostics))

    def test_reexported_object_preserves_constructor_and_friendly_element(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public object Box =>\n"
                "  $value: Number\n"
                "  define unwrap -> Number => $self.value\n"
                "end\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[Box] }\n",
                encoding="utf-8",
            )
            typed = self.analyse(root, "import { facade.[Box] }\nBox(7) unwrap")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_public_trait_implementation_import_reexports_relationship(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public trait Shape => end\n"
                "public object Rectangle =>\n  $width: Number\nend\n"
                "object Rectangle as Shape => end\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[Rectangle, Shape, object Rectangle as Shape] }\n",
                encoding="utf-8",
            )
            analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
            analyser.analyse(parse(
                "import { facade.[Rectangle, Shape, object Rectangle as Shape] }\n"
            ))
            self.assertEqual(analyser.diagnostics, [])
            self.assertIn(
                Symbol("Shape"),
                analyser.env.context.trait_impls.get(Symbol("Rectangle"), set()),
            )

    def test_conflicting_public_imports_do_not_produce_a_facade_interface(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for module in ("first", "second"):
                (root / f"{module}.vlnc").write_text(
                    "public define hash(value: String) -> String => $value\n",
                    encoding="utf-8",
                )
            (root / "facade.vlnc").write_text(
                "public import { first.[hash], second.[hash] }\n",
                encoding="utf-8",
            )
            with self.assertRaises(Exception) as raised:
                ModuleLoader().load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            self.assertIn("contains type errors", str(raised.exception))

    def test_compiled_facade_preserves_selected_overload(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            internal = root / "internal.vlnc"
            facade = root / "facade.vlnc"
            internal.write_text(
                "overload(String -> String)\n"
                "overload(Number -> Number)\n"
                "public define identity(value) => $value\n",
                encoding="utf-8",
            )
            facade.write_text(
                "public import { internal.[identity(Number)] }\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main_vln([
                    "compile-module", "--file", str(facade),
                    "--output", str(root / "facade.vbcm"),
                ])
            self.assertEqual(result, 0)
            facade.unlink()
            internal.unlink()
            typed = self.analyse(root, "import { facade.[identity] }\n4 identity")
            self.assertEqual(str(typed[-1].typ.name), "Number")


class RuntimeDependencyResolutionTests(unittest.TestCase):
    """Private runtime dependencies are a module contract, not a codec fixture."""

    def test_compiler_module_interfaces_have_no_codec_specific_fields(self):
        from dataclasses import fields
        from valiance.asts.nodes import ObjectNode, TypedObjectNode
        from valiance.elements.builtins import RuntimeContext
        from valiance.modules_system.modules import ModuleExports, ModuleObject
        for record in (ObjectNode, TypedObjectNode, ModuleObject, ModuleExports, RuntimeContext):
            with self.subTest(record=record.__name__):
                self.assertNotIn("json", [f.name for f in fields(record)])

    def test_transitive_runtime_dependencies_have_no_source_import_visibility(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "core.vlnc").write_text(
                "object Leaf=>public $value:Int end\n"
                "object Middle=>public $leaf:Leaf end\n"
                "public object Outer=>public $middle:Middle end\n",
                encoding="utf-8",
            )
            exports = ModuleLoader(base_directory=root).load(
                ImportPath(("core",)), current_file=root / "main.vlnc"
            )
            outer, = (obj for obj in exports.public_objects() if obj.name.text == "Outer")
            closure = resolve_runtime_dependencies(exports, (outer,))
            self.assertEqual([str(obj.name) for obj in closure.declarations], ["Middle", "Leaf"])
            self.assertEqual([str(obj.origin or Symbol(obj.name.text, ("core",)))
                              for obj in closure.declarations], ["core.Middle", "core.Leaf"])
            self.assertFalse(any(obj.name.text in {"Middle", "Leaf"}
                                 for obj in exports.public_objects()))

    def test_imported_field_contract_preserves_foreign_provider_dependency(self):
        """A locally exported object must retain the origin of imported fields."""
        for compiled in (False, True):
            with self.subTest(compiled=compiled), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "provider.vlnc").write_text(
                    "public object Item=>public $value:Int end\n", encoding="utf-8"
                )
                (root / "consumer.vlnc").write_text(
                    "import {provider.Item}\n"
                    "public object Container=>public $item:Item end\n",
                    encoding="utf-8",
                )
                (root / "facade.vlnc").write_text(
                    "public import {consumer.Container as Wrapped}\n",
                    encoding="utf-8",
                )
                if compiled:
                    coordinator = CompilationCoordinator(root)
                    for name in ("provider", "consumer", "facade"):
                        path = root / f"{name}.vlnc"
                        coordinator.build_module(
                            path, path.with_suffix(".vbcm"), module_name=name
                        )
                        path.unlink()
                exports = ModuleLoader(base_directory=root).load(
                    ImportPath(("facade",)), current_file=root / "main.vlnc"
                )
                wrapped, = exports.public_objects()
                closure = resolve_runtime_dependencies(exports, (wrapped,))
                self.assertEqual(
                    [str(item.origin) for item in closure.declarations],
                    ["provider.Item"],
                )
                self.assertEqual(
                    dict(wrapped.runtime_nominal_origins)[Symbol("Item")],
                    Symbol("Item", ("provider",)),
                )
                self.assertNotIn("Item", [obj.name.text for obj in exports.public_objects()])

    def test_missing_foreign_provider_does_not_resolve_to_same_named_local_type(self):
        """A damaged or partial interface must not fall back to local identity."""
        from dataclasses import replace
        from valiance.modules_system.modules import ModuleExports

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "owner.vlnc").write_text(
                "object Item=>public $value:Int end\n"
                "public object Container=>public $item:Item end\n",
                encoding="utf-8",
            )
            exports = ModuleLoader(base_directory=root).load(
                ImportPath(("owner",)), current_file=root / "main.vlnc"
            )
            outer, = exports.public_objects()
            foreign_origin = Symbol("Item", ("foreign",))
            owner_with_foreign_field = replace(
                outer,
                runtime_nominal_origins=((Symbol("Item"), foreign_origin),),
            )
            incomplete = ModuleExports(
                "owner", objects=tuple(
                    owner_with_foreign_field if item == outer else item
                    for item in exports.objects
                ),
            )
            closure = resolve_runtime_dependencies(
                incomplete, (owner_with_foreign_field,)
            )
            self.assertEqual(closure.declarations, ())

    def test_incremental_provider_swap_changes_interface_identity(self):
        """A same-shaped foreign type swap must invalidate the compiled interface."""
        from valiance.runtime.compiled_module import loads_module

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for provider in ("first", "second"):
                (root / f"{provider}.vlnc").write_text(
                    "public object Item=>public $value:Int end\n", encoding="utf-8"
                )
            consumer = root / "consumer.vlnc"
            coordinator = CompilationCoordinator(root)
            for provider in ("first", "second"):
                path = root / f"{provider}.vlnc"
                coordinator.build_module(
                    path, path.with_suffix(".vbcm"), module_name=provider
                )
            hashes = []
            for provider in ("first", "second"):
                consumer.write_text(
                    f"import {{{provider}.Item}}\n"
                    "public object Container=>public $item:Item end\n",
                    encoding="utf-8",
                )
                destination = consumer.with_suffix(".vbcm")
                coordinator.build_module(
                    consumer, destination, module_name="consumer"
                )
                loaded = loads_module(destination.read_bytes())
                hashes.append(loaded.interface_hash)
                self.assertEqual(
                    [str(item.origin) for item in
                     loaded.analysed_interface.runtime_dependencies],
                    [f"{provider}.Item"],
                )
                outer, = loaded.analysed_interface.public_objects()
                self.assertEqual(
                    dict(outer.runtime_nominal_origins)[Symbol("Item")],
                    Symbol("Item", (provider,)),
                )
            self.assertNotEqual(hashes[0], hashes[1])

    def test_private_dependencies_survive_alias_facades_and_binary_interfaces(self):
        for compiled in (False, True):
            with self.subTest(compiled=compiled), TemporaryDirectory() as directory:
                root = Path(directory)
                for provider in ("alpha", "beta"):
                    (root / f"{provider}.vlnc").write_text(
                        "object Item=>public $value:Int end\n"
                        "public object Container=>public $item:Item end\n",
                        encoding="utf-8",
                    )
                (root / "facade.vlnc").write_text(
                    "public import {alpha.Container as Left, beta.Container as Right}\n",
                    encoding="utf-8",
                )
                if compiled:
                    coordinator = CompilationCoordinator(root)
                    for module in ("alpha", "beta", "facade"):
                        file = root / f"{module}.vlnc"
                        coordinator.build_module(
                            file, file.with_suffix(".vbcm"), module_name=module
                        )
                    for module in ("alpha", "beta", "facade"):
                        (root / f"{module}.vlnc").unlink()
                exports = ModuleLoader(base_directory=root).load(
                    ImportPath(("facade",)), current_file=root / "main.vlnc"
                )
                for alias, provider in (("Left", "alpha"), ("Right", "beta")):
                    obj, = (item for item in exports.public_objects()
                            if item.name.text == alias)
                    closure = resolve_runtime_dependencies(exports, (obj,))
                    self.assertEqual([str(item.origin) for item in closure.declarations],
                                     [f"{provider}.Item"])
                    self.assertFalse(any(item.name.text == "Item"
                                         for item in exports.public_objects()))


if __name__ == "__main__":
    unittest.main()
