"""Independent replay of recovered cross-module JSON linker regressions.

These cases deliberately import only the container, never its field type.  The
VM must link checked internal schemas without leaking source imports or
allowing equal short names from different modules to become interchangeable.
"""

from __future__ import annotations

from pathlib import Path
import json
from tempfile import TemporaryDirectory
import unittest

from valiance.analysis import Analyser
from valiance.incremental.coordinator import CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import compile_program
from valiance.runtime.compiled_module import dumps, loads
from valiance.runtime.vm import run


class ReconstructedMultiModuleRepairs(unittest.TestCase):
    @staticmethod
    def run_project(modules: dict[str, str], source: str, layout: str | frozenset[str]):
        with TemporaryDirectory(prefix="valiance-overnight-recovery-") as folder:
            root = Path(folder)
            for name, text in modules.items():
                path = root.joinpath(*name.split(".")).with_suffix(".vlnc")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            # In addition to source-only and source-free projects, test selected
            # precompiled dependencies while other modules remain source.  A
            # fresh project is used for every layout to prevent cache leakage.
            binary_names = (frozenset(modules) if layout == "compiled" else
                            frozenset() if layout == "source" else layout)
            if not isinstance(binary_names, frozenset) or not binary_names <= modules.keys():
                raise ValueError(f"unknown artifact layout: {layout!r}")
            if binary_names:
                coordinator = CompilationCoordinator(root)
                for name in modules:
                    if name not in binary_names:
                        continue
                    path = root.joinpath(*name.split(".")).with_suffix(".vlnc")
                    coordinator.build_module(path, path.with_suffix(".vbcm"), module_name=name)
                    path.unlink()
            analyser = Analyser(source_file=root / "main.vlnc")
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(f"{layout}: {analyser.diagnostics}")
            results = []
            for optimized in (False, True):
                compiled = compile_program(typed, optimize=optimized)
                for bytecode in (compiled, loads(dumps(compiled))):
                    results.append(run(bytecode))
            return results

    def test_transitive_public_field_schema_is_linked_without_import(self):
        modules = {"core": (
            "public object Item => public $value:Int end\n"
            "public object Container => public $item:Item end\n"
        )}
        source = (
            "import {core.Container}\nimport {std.json}\n"
            'json.decode[Container]("{\\"item\\":{\\"value\\":7}}") ?!\n'
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                for results in self.run_project(modules, source, layout):
                    self.assertEqual(len(results), 1)
                    container = results[0]
                    self.assertEqual(container.type_name, "Container")
                    self.assertEqual(container.fields["item"].type_name, "core.Item")
                    self.assertEqual(container.fields["item"].fields["value"], 7)

    def test_two_providers_with_same_short_name_keep_distinct_json_schemas(self):
        modules = {
            "alpha": ("public object Item=>public $value:Int end\n"
                      "public object Container=>public $item:Item end\n"),
            "beta": ("public object Item=>public $text:String end\n"
                     "public object Container=>public $item:Item end\n"),
        }
        source = (
            "import {alpha.Container as First,beta.Container as Second}\n"
            "import {std.json}\n"
            'json.decode[First]("{\\"item\\":{\\"value\\":7}}") ?!\n'
            'json.decode[Second]("{\\"item\\":{\\"text\\":\\"abc\\"}}") ?!\n'
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                for left, right in self.run_project(modules, source, layout):
                    self.assertEqual(left.fields["item"].type_name, "alpha.Item")
                    self.assertEqual(left.fields["item"].fields["value"], 7)
                    self.assertEqual(right.fields["item"].type_name, "beta.Item")
                    self.assertEqual(right.fields["item"].fields["text"], "abc")

    def test_facade_preserves_provider_schema_provenance(self):
        modules = {
            "core": ("public object Item=>public $value:Int end\n"
                     "public object Container=>public $item:Item end\n"),
            "gateway": "public import {core.Container as Wrapped}\n",
        }
        source = (
            "import {gateway.Wrapped}\nimport {std.json}\n"
            'json.decode[Wrapped]("{\\"item\\":{\\"value\\":7}}") ?!\n'
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                for [container] in self.run_project(modules, source, layout):
                    self.assertEqual(container.type_name, "Wrapped")
                    self.assertEqual(container.fields["item"].type_name, "core.Item")
                    self.assertEqual(container.fields["item"].fields["value"], 7)

    def test_identical_schema_bodies_from_different_providers_are_not_deduplicated(self):
        module = ("public object Item=>public $value:Int end\n"
                  "public object Container=>public $item:Item end\n")
        modules = {"alpha": module, "beta": module}
        source = (
            "import {alpha.Container as First,beta.Container as Second}\n"
            "import {std.json}\n"
            'json.decode[First]("{\\"item\\":{\\"value\\":7}}") ?!\n'
            'json.decode[Second]("{\\"item\\":{\\"value\\":8}}") ?!\n'
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                for left, right in self.run_project(modules, source, layout):
                    self.assertEqual(left.fields["item"].type_name, "alpha.Item")
                    self.assertEqual(right.fields["item"].type_name, "beta.Item")
                    self.assertEqual(left.fields["item"].fields["value"], 7)
                    self.assertEqual(right.fields["item"].fields["value"], 8)

    def test_transitive_schemas_inside_collections(self):
        modules = {"core": (
            "public object Item=>public $value:Int end\n"
            "public object Container=>public $items:Item+ end\n"
        )}
        source = (
            "import {core.Container}\nimport {std.json}\n"
            'json.decode[Container]("{\\"items\\":[{\\"value\\":1},{\\"value\\":2}]}") ?!\n'
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                for [container] in self.run_project(modules, source, layout):
                    items = container.fields["items"]
                    self.assertEqual([item.type_name for item in items],
                                     ["core.Item", "core.Item"])
                    self.assertEqual([item.fields["value"] for item in items], [1, 2])

    def test_cross_provider_field_dependencies_survive_facades_and_artifacts(self):
        """Two outside field providers stay distinct through a composed facade."""
        modules = {
            "alpha.item": "public object Item=>public $value:Int end\n",
            "beta.item": "public object Item=>public $text:String end\n",
            "left": (
                "import {alpha.item.Item}\n"
                "public object Container=>public $item:Item end\n"
            ),
            "right": (
                "import {beta.item.Item}\n"
                "public object Container=>public $item:Item end\n"
            ),
            "facade": (
                "public import {left.Container as First,right.Container as Second}\n"
            ),
        }
        source = (
            "import {facade.[First,Second]}\nimport {std.json}\n"
            'json.decode[First]("{\\"item\\":{\\"value\\":7}}") ?!\n'
            'json.decode[Second]("{\\"item\\":{\\"text\\":\\"abc\\"}}") ?!\n'
        )
        layouts = (
            "source", "compiled",
            frozenset({"alpha.item", "left", "facade"}),
            frozenset({"beta.item", "right"}),
        )
        for layout in layouts:
            with self.subTest(layout=layout):
                for first, second in self.run_project(modules, source, layout):
                    self.assertEqual(first.fields["item"].type_name, "alpha.item.Item")
                    self.assertEqual(first.fields["item"].fields["value"], 7)
                    self.assertEqual(second.fields["item"].type_name, "beta.item.Item")
                    self.assertEqual(second.fields["item"].fields["text"], "abc")

    def test_generic_field_provider_retains_origin_and_specialization(self):
        """Generic field constructors retain provider identity and type args."""
        modules = {
            "provider": "public object[T] Item=>public $value:T end\n",
            "consumer": (
                "import {provider.Item}\n"
                "public object[T] Container=>public $item:Item[T] end\n"
            ),
            "facade": "public import {consumer.Container as Wrapped}\n",
        }
        source = (
            "import {facade.Wrapped}\nimport {std.json}\n"
            'json.decode[Wrapped[Int]]("{\\"item\\":{\\"value\\":7}}") ?!\n'
        )
        for layout in ("source", "compiled", frozenset({"provider", "facade"})):
            with self.subTest(layout=layout):
                for [outer] in self.run_project(modules, source, layout):
                    self.assertEqual(outer.type_name, "Wrapped")
                    self.assertEqual(outer.type_args, ("Int",))
                    self.assertEqual(outer.fields["item"].type_name, "provider.Item")
                    self.assertEqual(outer.fields["item"].type_args, ("Int",))
                    self.assertEqual(outer.fields["item"].fields["value"], 7)

    def test_imported_field_provider_is_not_reexported_as_source_name(self):
        """A private import remains invisible despite runtime dependency metadata."""
        modules = {
            "provider": "public object Item=>public $value:Int end\n",
            "consumer": (
                "import {provider.Item}\n"
                "public object Container=>public $item:Item end\n"
            ),
        }
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout), TemporaryDirectory() as folder:
                root = Path(folder)
                for name, text in modules.items():
                    (root / f"{name}.vlnc").write_text(text, encoding="utf-8")
                if layout == "compiled":
                    coordinator = CompilationCoordinator(root)
                    for name in ("provider", "consumer"):
                        path = root / f"{name}.vlnc"
                        coordinator.build_module(
                            path, path.with_suffix(".vbcm"), module_name=name
                        )
                        path.unlink()
                analyser = Analyser(source_file=root / "main.vlnc")
                analyser.analyse(parse("import {consumer.Item}\n"))
                self.assertTrue(analyser.diagnostics)
                self.assertIn("Item", " ".join(analyser.diagnostics))

    def test_imported_generic_reflection_keeps_checked_descriptor_owner(self):
        modules = {
            "core": "public object[T] Box=>public $value:T end\n",
            "logic": (
                "import {core.Box}\n"
                "public define[T] reflect(x:Box[T])->T =>\n"
                ' $field=fields[Box[T]]["value"]\n'
                " read?($x, $field) ?!\nend\n"
            ),
            "gateway": "public import {core.Box,logic.reflect}\n",
        }
        source = "import {gateway.[Box,reflect]}\nreflect(Box(9))\n"
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                for results in self.run_project(modules, source, layout):
                    self.assertEqual(results, [9])

    @staticmethod
    def nested_schema_project(depth: int, *, generic: bool = False):
        """Construct a source-level JSON tree and its independent expected shape.

        Only the outer constructor is imported; inner nominal types have to be
        discovered through the checked field schemas and are never source-visible.
        """
        declarations = []
        data: object = {"value": 71}
        annotation = "[T]" if generic else ""
        type_arg = "[T]" if generic else ""
        for index in range(depth + 1):
            name = f"Layer{index}"
            if index == 0:
                fields = "public $value:T" if generic else "public $value:Int"
            else:
                fields = f"public $inner:Layer{index - 1}{type_arg}"
                data = {"inner": data}
            declarations.append(f"public object{annotation} {name}=>{fields} end\n")
        return "".join(declarations), data

    def test_nested_schema_closure_through_facade_and_mixed_artifacts(self):
        """Validate every nested owner against independent JSON construction."""
        for depth in (1, 2, 3):
            provider, payload = self.nested_schema_project(depth)
            for facade_depth in (1, 2):
                modules = {"core": provider,
                           "gateway": f"public import {{core.Layer{depth} as Selected}}\n"}
                if facade_depth == 2:
                    modules["api"] = "public import {gateway.Selected}\n"
                imported_from = "api" if facade_depth == 2 else "gateway"
                raw_json = json.dumps(payload, separators=(",", ":"))
                source = (f"import {{{imported_from}.Selected}}\n"
                          f"import {{std.json}}\n"
                          f"json.decode[Selected]({json.dumps(raw_json)}) ?!\n")
                binary_layouts = ("source", frozenset({"core"}),
                                  "compiled")
                for layout in binary_layouts:
                    with self.subTest(depth=depth, facade_depth=facade_depth,
                                      layout=layout):
                        for [result] in self.run_project(modules, source, layout):
                            self.assertEqual(result.type_name, "Selected")
                            cursor = result
                            for index in range(depth, 0, -1):
                                cursor = cursor.fields["inner"]
                                self.assertEqual(cursor.type_name, f"core.Layer{index - 1}")
                            self.assertEqual(cursor.fields["value"], 71)

    def test_generic_nested_schema_closure_across_serialization(self):
        """Reification must reach an unimported generic inner constructor."""
        for depth in (1, 2):
            provider, payload = self.nested_schema_project(depth, generic=True)
            modules = {"core": provider,
                       "gateway": f"public import {{core.Layer{depth} as Selected}}\n"}
            raw_json = json.dumps(payload, separators=(",", ":"))
            source = ("import {gateway.Selected}\nimport {std.json}\n"
                      f"json.decode[Selected[Int]]({json.dumps(raw_json)}) ?!\n")
            for layout in ("source", frozenset({"core"}), "compiled"):
                with self.subTest(depth=depth, layout=layout):
                    for [result] in self.run_project(modules, source, layout):
                        self.assertEqual(result.type_args, ("Int",))
                        cursor = result
                        for index in range(depth, 0, -1):
                            cursor = cursor.fields["inner"]
                            self.assertEqual(cursor.type_name, f"core.Layer{index - 1}")
                            self.assertEqual(cursor.type_args, ("Int",))
                        self.assertEqual(cursor.fields["value"], 71)

    def test_identical_generic_providers_are_not_merged_by_import_order(self):
        """Changing import order must not change nominal field provenance."""
        module = ("public object[T] Item=>public $value:T end\n"
                  "public object[T] Container=>public $inner:Item[T] end\n")
        for import_order in (("alpha", "beta"), ("beta", "alpha")):
            modules = {"alpha": module, "beta": module}
            first, second = import_order
            source = (
                f"import {{{first}.Container as First,"
                f"{second}.Container as Second}}\nimport {{std.json}}\n"
                'json.decode[First[Int]]("{\\"inner\\":{\\"value\\":31}}") ?!\n'
                'json.decode[Second[Int]]("{\\"inner\\":{\\"value\\":47}}") ?!\n'
            )
            for layout in ("source", frozenset({first}), "compiled"):
                with self.subTest(import_order=import_order, layout=layout):
                    for left, right in self.run_project(modules, source, layout):
                        self.assertEqual(left.fields["inner"].type_name, f"{first}.Item")
                        self.assertEqual(right.fields["inner"].type_name, f"{second}.Item")
                        self.assertEqual(left.fields["inner"].type_args, ("Int",))
                        self.assertEqual(right.fields["inner"].type_args, ("Int",))
                        self.assertEqual(left.fields["inner"].fields["value"], 31)
                        self.assertEqual(right.fields["inner"].fields["value"], 47)

    def test_json_scalar_metamorphism_for_nested_generic_provider(self):
        """Altering only a JSON scalar changes its value, never nominal origin."""
        provider, _ = self.nested_schema_project(2, generic=True)
        modules = {
            "pkg.core": provider,
            "gateway": "public import {pkg.core.Layer2 as Selected}\n",
        }
        for value in (-991, 0, 2**31 - 1):
            payload = {"inner": {"inner": {"value": value}}}
            raw_json = json.dumps(payload, separators=(",", ":"))
            source = ("import {gateway.Selected}\nimport {std.json}\n"
                      f"json.decode[Selected[Int]]({json.dumps(raw_json)}) ?!\n")
            for layout in ("source", "compiled"):
                with self.subTest(value=value, layout=layout):
                    for [result] in self.run_project(modules, source, layout):
                        middle = result.fields["inner"]
                        leaf = middle.fields["inner"]
                        self.assertEqual(result.type_name, "Selected")
                        self.assertEqual(middle.type_name, "pkg.core.Layer1")
                        self.assertEqual(leaf.type_name, "pkg.core.Layer0")
                        self.assertEqual(leaf.type_args, ("Int",))
                        self.assertEqual(leaf.fields["value"], value)

    def test_generic_provider_aliases_do_not_make_independent_types_assignable(self):
        """Equal shape and generic arguments are insufficient for nominal identity."""
        modules = {
            "alpha": ("public object[T] Box=>public $value:T end\n"
                      "public define read(box:Box[Int])->Int=>$box.value end\n"),
            "beta": "public object[T] Box=>public $value:T end\n",
        }
        source = ("import {alpha.[Box as First,read],beta.Box as Second}\n"
                  "read(Second(7))\n")
        with TemporaryDirectory(prefix="valiance-distinct-generic-types-") as folder:
            root = Path(folder)
            for name, code in modules.items():
                (root / f"{name}.vlnc").write_text(code, encoding="utf-8")
            for layout in ("source", "compiled"):
                if layout == "compiled":
                    coordinator = CompilationCoordinator(root)
                    for name in modules:
                        path = root / f"{name}.vlnc"
                        coordinator.build_module(path, path.with_suffix(".vbcm"), module_name=name)
                        path.unlink()
                analyser = Analyser(source_file=root / "main.vlnc")
                analyser.analyse(parse(source))
                self.assertTrue(any("no overloads" in str(d) for d in analyser.diagnostics),
                                (layout, analyser.diagnostics))

    def test_internal_json_schema_dependencies_never_become_public_facade_names(self):
        """Runtime schema closure must not expand the source import surface."""
        modules = {
            "core": ("public object Internal=>public $value:Int end\n"
                     "public object Wrapper=>public $inner:Internal end\n"),
            "gateway": "public import {core.Wrapper as PublicWrapper}\n",
        }
        source = "import {gateway.PublicWrapper}\nInternal(7)\n"
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout), TemporaryDirectory() as folder:
                root = Path(folder)
                for name, code in modules.items():
                    (root / f"{name}.vlnc").write_text(code, encoding="utf-8")
                if layout == "compiled":
                    coordinator = CompilationCoordinator(root)
                    for name in modules:
                        path = root / f"{name}.vlnc"
                        coordinator.build_module(path, path.with_suffix(".vbcm"),
                                                 module_name=name)
                        path.unlink()
                analyser = Analyser(source_file=root / "main.vlnc")
                analyser.analyse(parse(source))
                self.assertTrue(analyser.diagnostics, layout)

    def test_incremental_nested_schema_change_invalidates_compiled_facade(self):
        """A private transitive field-type change must invalidate linked JSON code.

        The main source and its JSON input are intentionally unchanged; only an
        indirectly referenced provider's declaration changes from Int to String.
        """
        with TemporaryDirectory(prefix="valiance-json-schema-invalidation-") as folder:
            root = Path(folder)
            provider = root / "core.vlnc"
            provider.write_text(
                "public object Item=>public $value:Int end\n"
                "public object Container=>public $item:Item end\n", encoding="utf-8")
            facade = root / "api.vlnc"
            facade.write_text("public import {core.Container as Selected}\n", encoding="utf-8")
            main = root / "main.vlnc"
            main.write_text(
                'import {api.Selected}\nimport {std.json}\n'
                'json.decode[Selected]("{\\"item\\":{\\"value\\":7}}")\n',
                encoding="utf-8",
            )
            coordinator = CompilationCoordinator(root)
            for name in ("core", "api"):
                path = root / f"{name}.vlnc"
                coordinator.build_module(path, path.with_suffix(".vbcm"), module_name=name)
            output = root / "main.vbc"
            coordinator.build_executable(main, output, target_identity="main")
            initial = run(loads(output.read_bytes()))
            self.assertEqual(initial[0].type_name, "Selected")
            self.assertEqual(initial[0].fields["item"].fields["value"], 7)
            # The consumer file is unchanged. The next result must be a JSON
            # validation error, not a stale constructor accepting the old Int.
            provider.write_text(
                "public object Item=>public $value:String end\n"
                "public object Container=>public $item:Item end\n", encoding="utf-8")
            for name in ("core", "api"):
                path = root / f"{name}.vlnc"
                coordinator.build_module(path, path.with_suffix(".vbcm"), module_name=name)
            coordinator.build_executable(main, output, target_identity="main")
            updated = run(loads(output.read_bytes()))
            self.assertEqual(updated[0].type_name, "ValueError")
            self.assertIn("expected JSON string", updated[0].fields["message"])

    def test_schema_dependency_stays_invisible_to_source_imports(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "core.vlnc").write_text(
                "public object Item=>public $value:Int end\n"
                "public object Container=>public $item:Item end\n",
                encoding="utf-8",
            )
            analyser = Analyser(source_file=root / "main.vlnc")
            analyser.analyse(parse("import {core.Container}\nItem(7)\n"))
            self.assertTrue(analyser.diagnostics)

    def test_invalid_nested_json_is_rejected_in_each_provider(self):
        modules = {
            "alpha": ("public object Item=>public $value:Int end\n"
                      "public object Container=>public $item:Item end\n"),
            "beta": ("public object Item=>public $text:String end\n"
                     "public object Container=>public $item:Item end\n"),
        }
        source = (
            "import {alpha.Container as First,beta.Container as Second}\n"
            "import {std.json}\n"
            'json.decode[First]("{\\"item\\":{\\"text\\":\\"bad\\"}}")\n'
            'json.decode[Second]("{\\"item\\":{\\"value\\":7}}")\n'
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                for left, right in self.run_project(modules, source, layout):
                    self.assertEqual(left.type_name, "ValueError")
                    self.assertEqual(right.type_name, "ValueError")
                    self.assertIn("unexpected JSON fields", left.fields["message"])
                    self.assertIn("unexpected JSON fields", right.fields["message"])


if __name__ == "__main__":
    unittest.main()
