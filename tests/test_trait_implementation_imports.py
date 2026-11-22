import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.bytecode import Instruction, OpCode, ResolvedElementReference
from valiance.vtypes.symbols import Symbol


class TraitImplementationImportTests(unittest.TestCase):
    def test_concrete_generic_trait_arguments_survive_import(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources.vlnc").write_text('''
public trait[T] Source => extend read -> T end
public object Box => $value: Int end
object Box as Source[Int] => define read => $self.value end
''', encoding="utf-8")
            prelude = "import { sources.[Box, Source] }\n"
            valid = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
            typed = valid.analyse(parse(prelude + '''
define accept(item: Source[Int]) -> Int => $item read end
Box(7) accept
'''))
            self.assertEqual(valid.diagnostics, [])
            self.assertEqual(run(loads(dumps(compile_program(typed)))), [7])
            invalid = self.analyse(root, prelude + '''
define accept(item: Source[String]) -> String => $item read end
Box(7) accept
''')
            self.assertTrue(any("accept" in message for message in invalid.diagnostics),
                            invalid.diagnostics)

    def analyse(self, root: Path, source: str) -> Analyser:
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        analyser.analyse(parse(source))
        return analyser

    def write_shapes(self, root: Path, name: str = "shapes") -> None:
        (root / f"{name}.vlnc").write_text(
            "public trait Shape => end\n"
            "public object Rectangle =>\n  $width: Number\nend\n"
            "object Rectangle as Shape => end\n",
            encoding="utf-8",
        )

    def test_explicit_import_installs_trait_implementation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root)
            analyser = self.analyse(
                root,
                "import { shapes.[Rectangle, Shape, object Rectangle as Shape] }\n",
            )
            self.assertEqual(analyser.diagnostics, [])
            self.assertIn(
                Symbol("Shape"),
                analyser.env.context.trait_impls.get(Symbol("Rectangle"), set()),
            )

    def test_missing_trait_implementation_is_an_error(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root)
            analyser = self.analyse(
                root,
                "import { shapes.[object Rectangle as Missing] }\n",
            )
            self.assertTrue(any("defines no implementation" in d for d in analyser.diagnostics))

    def test_conflicting_behaviour_set_imports_fail_without_a_call(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root, "first")
            (root / "second.vlnc").write_text(
                "import { first.[Rectangle, Shape] }\n"
                "object Rectangle as Shape => end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root,
                "import {\n"
                "  first.object Rectangle as Shape,\n"
                "  second.object Rectangle as Shape\n"
                "}\n",
            )
            self.assertEqual(len(analyser.diagnostics), 1)
            diagnostic = analyser.diagnostics[0]
            self.assertTrue(diagnostic.startswith("3:10:"))
            self.assertIn("ambiguous behaviour set imports for Rectangle as Shape", diagnostic)
            self.assertIn("first.Shape (previous import at 2:9)", diagnostic)
            self.assertIn("second.Shape", diagnostic)

    def test_namespace_import_does_not_install_trait_implementation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root)
            analyser = self.analyse(root, "import { shapes }\n")
            self.assertEqual(analyser.diagnostics, [])
            self.assertNotIn(
                Symbol("Shape"),
                analyser.env.context.trait_impls.get(Symbol("Rectangle"), set()),
            )

    def test_multiple_behaviour_sets_can_dispatch_through_qualified_casts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (("first", 1), ("second", 2)):
                (root / f"{name}.vlnc").write_text(
                    "public trait Shape => extend area -> Number end\n"
                    "public object Rectangle => $width: Number end\n"
                    "object Rectangle as Shape => "
                    f"define area -> Number => {value} end\n",
                    encoding="utf-8",
                )
            source = (
                "import { first, second }\n"
                "if true =>\n"
                "  import { first.object Rectangle as Shape }\n"
                "  Rectangle(4) as[first.Shape] | area\n"
                "else => 0 end\n"
                "if true =>\n"
                "  import { second.object Rectangle as Shape }\n"
                "  Rectangle(4) as[second.Shape] | area\n"
                "else => 0 end\n"
            )
            analyser = Analyser(
                module_loader=ModuleLoader(),
                source_file=root / "main.vlnc",
            )
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(run(compile_program(typed)), [1, 2])

    def test_multiple_behaviour_sets_expose_qualified_elements(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (("first", 1), ("second", 2)):
                (root / f"{name}.vlnc").write_text(
                    "public trait Shape => extend area -> Number end\n"
                    "public object Rectangle => $width: Number end\n"
                    "object Rectangle as Shape => "
                    f"define area -> Number => {value} end\n",
                    encoding="utf-8",
                )
            source = (
                "import { first, second }\n"
                "if true =>\n"
                "  import { first.object Rectangle as Shape }\n"
                "  Rectangle(4) first.Shape.area\n"
                "else => 0 end\n"
                "if true =>\n"
                "  import { second.object Rectangle as Shape }\n"
                "  Rectangle(4) second.Shape.area\n"
                "else => 0 end\n"
            )
            analyser = Analyser(
                module_loader=ModuleLoader(),
                source_file=root / "main.vlnc",
            )
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(run(compile_program(typed)), [1, 2])

    def test_block_scoped_behaviour_set_does_not_escape(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traits.vlnc").write_text(
                "public trait Shape => extend area -> Number end\n",
                encoding="utf-8",
            )
            (root / "shapes.vlnc").write_text(
                "import { traits.Shape }\n"
                "public object Rectangle => $width: Number end\n"
                "object Rectangle as Shape => "
                "define area -> Number => $self.width end\n",
                encoding="utf-8",
            )
            source = (
                "import { shapes.Rectangle }\n"
                "if true =>\n"
                "  import { shapes.object Rectangle as Shape }\n"
                "  Rectangle(4) as[shapes.Shape] | area\n"
                "else => 0\n"
                "end\n"
                "Rectangle(4) as[shapes.Shape]\n"
            )
            analyser = self.analyse(root, source)
            self.assertTrue(
                any(
                    "no overloads for element 'area' match stack [shapes.Shape]"
                    in diagnostic
                    for diagnostic in analyser.diagnostics
                )
            )

    def test_unknown_behaviour_set_cast_reports_missing_relationship(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root)
            analyser = self.analyse(
                root,
                "import { shapes.Rectangle }\n"
                "Rectangle(1) as[missing.Shape]\n",
            )
            self.assertTrue(
                any(
                    "behaviour set missing.Shape does not provide "
                    "Rectangle as Shape" in diagnostic
                    for diagnostic in analyser.diagnostics
                )
            )

    def test_generic_trait_implementation_exports_its_pattern(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "producers.vlnc").write_text(
                "public trait[T] Producer => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root,
                "import { producers.object Box as Producer }\n",
            )
            self.assertEqual(analyser.diagnostics, [])
            patterns = analyser.env.context.trait_impl_patterns
            self.assertEqual(len(patterns), 1)
            pattern = patterns[0]
            self.assertEqual(pattern.provider, Symbol("producers"))
            self.assertEqual(pattern.generic_names, (Symbol("T"),))
            self.assertEqual(str(pattern.object_pattern.name), "Box")
            self.assertEqual(str(pattern.trait_pattern.name), "Producer")

    def test_generic_behaviour_set_cast_matches_instantiated_types(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "producers.vlnc").write_text(
                "public trait[T] Producer => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root,
                "import { producers.Box, "
                "producers.object Box as Producer }\n"
                "Box(1) as[producers.Producer[Int]]\n",
            )
            self.assertEqual(analyser.diagnostics, [])

    def test_generic_behaviour_set_preserves_correlated_arguments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "producers.vlnc").write_text(
                "public trait[T] Producer => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root,
                "import { producers.Box, "
                "producers.object Box as Producer }\n"
                "Box(1) as[producers.Producer[String]]\n",
            )
            self.assertTrue(
                any(
                    "behaviour set producers.Producer does not provide "
                    "Box as Producer" in diagnostic
                    for diagnostic in analyser.diagnostics
                )
            )

    def test_automatic_provider_can_be_qualified_alongside_an_explicit_import(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "first.vlnc").write_text(
                "public trait[T] Producer => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n",
                encoding="utf-8",
            )
            (root / "second.vlnc").write_text(
                "import { first.[Box, Producer] }\n"
                "object[T] Box as Producer[T] => end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root,
                "import { first.[Box, Producer], second.object Box as Producer }\n"
                "Box(1) as[first.Producer[Int]]\n"
                "Box(2) as[second.Producer[Int]]\n",
            )
            self.assertEqual(analyser.diagnostics, [])


if __name__ == "__main__":
    unittest.main()


class TraitToTraitImplementationTests(unittest.TestCase):
    def analyse(self, root: Path, source: str) -> Analyser:
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        analyser.analyse(parse(source))
        return analyser

    def write_traits(self, root: Path) -> None:
        (root / "formatting.vlnc").write_text(
            "public trait Printable => end\n"
            "public trait Displayable => end\n"
            "trait Printable as Displayable => end\n",
            encoding="utf-8",
        )

    def test_owned_trait_implication_is_imported_from_its_subject(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_traits(root)
            analyser = self.analyse(
                root,
                "import { formatting.[Printable, Displayable] }\n"
                "object Document => $id: Int end\n"
                "object Document as Printable => end\n"
                "Document(1) as[Displayable]\n",
            )
            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(
                {pattern.subject_kind for pattern in analyser.env.context.trait_impl_patterns},
                {Symbol("trait")},
            )

    def test_explicit_trait_behaviour_set_import_is_supported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_traits(root)
            analyser = self.analyse(
                root,
                "import { formatting.[Printable, Displayable, "
                "trait Printable as Displayable] }\n"
                "object Document => $id: Int end\n"
                "object Document as Printable => end\n"
                "Document(1) as[formatting.Displayable]\n",
            )
            self.assertEqual(analyser.diagnostics, [])

    def test_trait_implication_chains_are_cycle_safe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chain.vlnc").write_text(
                "public trait A => end\n"
                "public trait B => end\n"
                "public trait C => end\n"
                "trait A as B => end\n"
                "trait B as A => end\n"
                "trait B as C => end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root,
                "import { chain.[A, B, C] }\n"
                "object Item => $id: Int end\n"
                "object Item as A => end\n"
                "Item(1) as[C]\n",
            )
            self.assertEqual(analyser.diagnostics, [])


class GenericTraitToTraitImplementationTests(unittest.TestCase):
    def test_generic_trait_implication_correlates_arguments(self):
        analyser = Analyser()
        analyser.analyse(parse(
            "trait[T] Producer => end\n"
            "trait[T] Iterable => end\n"
            "trait[T] Producer as Iterable[T] => end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Producer[T] => end\n"
            "Box(1) as[Iterable[Int]]\n"
        ))
        self.assertEqual(analyser.diagnostics, [])

    def test_scoped_generic_object_implementation_respects_bounds(self):
        source = (
            "trait[T] Holder => end\n"
            "object[T] Box => $value: T end\n"
            "object[T: Number] Box as Holder[T] => end\n"
        )
        invalid = Analyser()
        invalid.analyse(parse(source + 'Box[String]("hi") as[Holder[String]]'))
        self.assertTrue(any(
            "cannot safely cast Box[String] to Holder[String]" in message
            for message in invalid.diagnostics
        ))
        valid = Analyser()
        valid.analyse(parse(source + "Box[Int](1) as[Holder[Int]]"))
        self.assertEqual(valid.diagnostics, [])

    def test_scoped_generic_object_constraint_survives_import(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "holders.vlnc").write_text(
                "public trait[T] Holder => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T: Number] Box as Holder[T] => end\n",
                encoding="utf-8",
            )
            good = Analyser(module_loader=ModuleLoader(), source_file=root / "good.vlnc")
            good.analyse(parse(
                "import {holders.Holder, holders.Box}\n"
                "Box[Int](1) as[Holder[Int]]"
            ))
            self.assertEqual(good.diagnostics, [])
            bad = Analyser(module_loader=ModuleLoader(), source_file=root / "bad.vlnc")
            bad.analyse(parse(
                "import {holders.Holder, holders.Box}\n"
                'Box[String]("hi") as[Holder[String]]'
            ))
            self.assertTrue(any(
                "cannot safely cast Box[String] to Holder[String]" in message
                for message in bad.diagnostics
            ))

    def test_generic_trait_implication_enforces_constraints(self):
        analyser = Analyser()
        analyser.analyse(parse(
            "trait[T] Producer => end\n"
            "trait[T] Iterable => end\n"
            "trait[T: Number] Producer as Iterable[T] => end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Producer[T] => end\n"
            'Box("value") as[Iterable[String]]\n'
        ))
        self.assertTrue(any(
            "cannot safely cast Box[String] to Iterable[String]" in diagnostic
            for diagnostic in analyser.diagnostics
        ))

    def test_competing_trait_implication_providers_are_ambiguous(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "base.vlnc").write_text(
                "public trait Printable => end\n"
                "public trait Displayable => end\n",
                encoding="utf-8",
            )
            for module in ("plain", "rich"):
                (root / f"{module}.vlnc").write_text(
                    "import { base.[Printable, Displayable] }\n"
                    "trait Printable as Displayable => end\n",
                    encoding="utf-8",
                )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { base.[Printable, Displayable] }\n"
                "import { plain.trait Printable as Displayable, "
                "rich.trait Printable as Displayable }\n"
                "object Document => $id: Int end\n"
                "object Document as Printable => end\n"
                "Document(1) as[Displayable]\n"
            ))
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn("ambiguous behaviour set imports for Printable as Displayable", diagnostic)
            self.assertIn("plain.Displayable", diagnostic)
            self.assertIn("rich.Displayable", diagnostic)



class GenericImplementationElementDispatchTests(unittest.TestCase):
    def test_explicit_generic_element_return_specializes_from_receiver(self):
        source = (
            "trait[T] Producer => extend produce -> T end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Producer[T] => "
            "define produce -> T => $self.value end\n"
            "Box(1) produce\n"
        )
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(compile_program(typed)), [1])

    def test_inferred_generic_element_return_specializes_from_receiver(self):
        source = (
            "trait[T] Producer => extend produce -> T end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Producer[T] => "
            "define produce => $self.value end\n"
            "Box(1) produce\n"
        )
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(compile_program(typed)), [1])

    def test_qualified_generic_providers_dispatch_to_distinct_runtime_bodies(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for module, field in (("first", "first"), ("second", "second")):
                (root / f"{module}.vlnc").write_text(
                    "public trait Producer => extend produce -> Int end\n"
                    "public object Box =>\n"
                    "  $first: Int\n"
                    "  $second: Int\n"
                    "end\n"
                    "object Box as Producer => "
                    f"define produce -> Int => $self.{field} end\n",
                    encoding="utf-8",
                )
            source = (
                "import { first, second }\n"
                "if true =>\n"
                "  import { first.object Box as Producer }\n"
                "  Box(1, 2) as[first.Producer] | produce\n"
                "else => 0 end\n"
                "if true =>\n"
                "  import { second.object Box as Producer }\n"
                "  Box(1, 2) as[second.Producer] | produce\n"
                "else => 0 end\n"
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            program = compile_program(typed)
            self.assertEqual(run(program), [1, 2])
            selected = [
                instruction.arg
                for instruction in program.main.instructions
                if instruction.op is OpCode.CALL_RESOLVED_ELEMENT
                and isinstance(instruction.arg, ResolvedElementReference)
                and instruction.arg.name == "produce"
            ]
            self.assertEqual(len(selected), 2)
            self.assertTrue(all(item.implementation_id for item in selected))
            self.assertNotEqual(selected[0].implementation_id, selected[1].implementation_id)
            self.assertTrue(all(not hasattr(item, "overload_index") for item in selected))
            adversarial = program
            self.assertEqual(run(adversarial), [1, 2])
            self.assertEqual(run(loads(dumps(adversarial))), [1, 2])


class ImplementationElementGenericTests(unittest.TestCase):
    def test_element_generic_is_solved_independently_from_receiver_generic(self):
        source = (
            "trait[T] Holder => end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Holder[T] => "
            "define[U] choose(value: U) -> U => $value end\n"
            'choose(Box(1), "ok")\n'
        )
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(compile_program(typed)), ["ok"])

    def test_element_generic_cannot_shadow_implementation_generic(self):
        source = (
            "trait[T] Holder => end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Holder[T] => "
            "define[T] choose(value: T) -> T => $value end\n"
        )
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(any(
            "element generic parameter(s) shadow implementation generic parameter(s): T"
            in diagnostic
            for diagnostic in analyser.diagnostics
        ))


class TraitImplementationBinderTests(unittest.TestCase):
    def test_generic_trait_implementation_receives_stable_scope_identity(self):
        [producer, iterable, implementation] = parse(
                "trait[T] Producer => extend produce -> T end\n"
                "trait[T] Iterable => extend first -> T end\n"
                "trait[T] Producer as Iterable[T] => "
                "define first -> T => $self produce end\n"
        )
        analyser = Analyser()
        analyser.analyse((producer, iterable, implementation))
        self.assertEqual(analyser.diagnostics, [])


class TraitImplementationElementBodyTests(unittest.TestCase):
    def test_trait_implementation_element_can_call_source_trait_requirement(self):
        source = (
            "trait[T] Producer => extend produce -> T end\n"
            "trait[T] Iterable => extend first -> T end\n"
            "trait[T] Producer as Iterable[T] => "
            "define first -> T => $self produce end\n"
        )
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])


class TraitImplementationEvidencePathTests(unittest.TestCase):
    def test_transitive_generic_evidence_preserves_specialized_trait_path(self):
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait[T] Producer => end\n"
            "trait[T] Iterable => end\n"
            "trait[T] Producer as Iterable[T] => end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Producer[T] => end\n"
        ))
        evidence = T.implementation_pattern_evidence(
            T.N(Symbol("Box"), T.N(Symbol("Int"))),
            T.N(Symbol("Iterable"), T.N(Symbol("Int"))),
            analyser.env.context,
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].provider, Symbol("<local>"))
        self.assertEqual(
            evidence[0].traits,
            (
                T.N(Symbol("Producer"), T.N(Symbol("Int"))),
                T.N(Symbol("Iterable"), T.N(Symbol("Int"))),
            ),
        )

    def test_competing_trait_implication_paths_are_retained(self):
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait Printable => end\n"
            "trait Displayable => end\n"
            "trait Serializable => end\n"
            "trait Printable as Displayable => end\n"
            "trait Displayable as Serializable => end\n"
            "trait Printable as Serializable => end\n"
            "object Document => $id: Int end\n"
            "object Document as Printable => end\n"
        ))
        evidence = T.implementation_pattern_evidence(
            T.N(Symbol("Document")), T.N(Symbol("Serializable")), analyser.env.context
        )
        self.assertEqual(
            {tuple(str(trait) for trait in item.traits) for item in evidence},
            {
                ("Printable", "Serializable"),
                ("Printable", "Displayable", "Serializable"),
            },
        )

    def test_evidence_records_provider_for_each_implication_edge(self):
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait Printable => end\n"
            "trait Displayable => end\n"
            "trait Serializable => end\n"
            "trait Printable as Displayable => end\n"
            "trait Displayable as Serializable => end\n"
            "object Document => $id: Int end\n"
            "object Document as Printable => end\n"
        ))
        [evidence] = T.implementation_pattern_evidence(
            T.N(Symbol("Document")),
            T.N(Symbol("Serializable")),
            analyser.env.context,
        )
        self.assertEqual(
            evidence.edge_providers,
            (Symbol("<local>"), Symbol("<local>"), Symbol("<local>")),
        )


class TraitImplementationBehaviourRegistryTests(unittest.TestCase):
    def test_local_trait_implementation_registers_supplied_element_names(self):
        analyser = Analyser()
        analyser.analyse(parse(
            "trait[T] Producer => extend produce -> T end\n"
            "trait[T] Iterable => extend first -> T end\n"
            "trait[T] Producer as Iterable[T] => "
            "define first -> T => $self produce end\n"
        ))
        self.assertEqual(analyser.diagnostics, [])
        [behaviour] = analyser.env.context.trait_impl_behaviours
        self.assertEqual(behaviour.provider, Symbol("<local>"))
        self.assertEqual(behaviour.subject_kind, Symbol("trait"))
        self.assertEqual(behaviour.element_names, (Symbol("first"),))

    def test_imported_trait_implementation_registers_supplied_element_names(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "iteration.vlnc").write_text(
                "public trait[T] Producer => extend produce -> T end\n"
                "public trait[T] Iterable => extend first -> T end\n"
                "trait[T] Producer as Iterable[T] => "
                "define first -> T => $self produce end\n",
                encoding="utf-8",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { iteration.[Producer, Iterable, "
                "trait Producer as Iterable] }\n"
            ))
            self.assertEqual(analyser.diagnostics, [])
            behaviours = analyser.env.context.trait_impl_behaviours
            self.assertEqual(len(behaviours), 1)
            self.assertEqual(behaviours[0].provider, Symbol("iteration"))
            self.assertEqual(behaviours[0].element_names, (Symbol("first"),))


class TraitImplementationBehaviourJoinTests(unittest.TestCase):
    def test_evidence_edge_joins_to_generic_trait_behaviour(self):
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait[T] Producer => extend produce -> T end\n"
            "trait[T] Iterable => extend first -> T end\n"
            "trait[T] Producer as Iterable[T] => "
            "define first -> T => $self produce end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Producer[T] => "
            "define produce -> T => $self.value end\n"
        ))
        [evidence] = T.implementation_pattern_evidence(
            T.N(Symbol("Box"), T.N(Symbol("Int"))),
            T.N(Symbol("Iterable"), T.N(Symbol("Int"))),
            analyser.env.context,
        )
        joined = T.implementation_evidence_behaviours(
            evidence, Symbol("first"), analyser.env.context
        )
        self.assertEqual(len(joined), 1)
        edge, behaviour = joined[0]
        self.assertEqual(str(edge.source), "Producer[Int]")
        self.assertEqual(str(edge.target), "Iterable[Int]")
        self.assertEqual(behaviour.element_names, (Symbol("first"),))

    def test_evidence_join_ignores_unrelated_elements_and_edges(self):
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait Printable => extend display -> String end\n"
            "trait Serializable => extend serialize -> String end\n"
            "trait Printable as Serializable => "
            'define serialize -> String => "serialized" end\n'
            "object Document => $id: Int end\n"
            "object Document as Printable => "
            'define display -> String => "document" end\n'
        ))
        [evidence] = T.implementation_pattern_evidence(
            T.N(Symbol("Document")),
            T.N(Symbol("Serializable")),
            analyser.env.context,
        )
        self.assertEqual(
            T.implementation_evidence_behaviours(
                evidence, Symbol("missing"), analyser.env.context
            ),
            (),
        )
        [(edge, behaviour)] = T.implementation_evidence_behaviours(
            evidence, Symbol("serialize"), analyser.env.context
        )
        self.assertEqual(edge.subject_kind, Symbol("trait"))
        self.assertEqual(behaviour.provider, Symbol("<local>"))

    def test_join_returns_exact_executable_definition_body(self):
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait Printable => extend display -> String end\n"
            "trait Serializable => extend serialize -> String end\n"
            "trait Printable as Serializable => "
            'define serialize -> String => "selected-body" end\n'
            "object Document => $id: Int end\n"
            "object Document as Printable => "
            'define display -> String => "document" end\n'
        ))
        [evidence] = T.implementation_pattern_evidence(
            T.N(Symbol("Document")),
            T.N(Symbol("Serializable")),
            analyser.env.context,
        )
        [(edge, definition)] = T.implementation_evidence_definitions(
            evidence, Symbol("serialize"), analyser.env.context
        )
        self.assertEqual(edge.subject_kind, Symbol("trait"))
        self.assertEqual(definition.name, Symbol("serialize"))
        self.assertEqual(
            getattr(definition.function.body[0], "value", None),
            "selected-body",
        )


class TraitImplementationEndToEndDispatchTests(unittest.TestCase):
    def test_generic_transitive_behaviour_dispatch_runs_and_retains_definition(self):
        source = (
            "trait[T] Producer => extend produce -> T end\n"
            "trait[T] Iterable => extend first -> T end\n"
            "trait[T] Producer as Iterable[T] => "
            "define first -> T => $self produce end\n"
            "object[T] Box => $value: T end\n"
            "object[T] Box as Producer[T] => "
            "define produce -> T => $self.value end\n"
            "Box(7) first\n"
        )
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        call = typed[-1]
        self.assertEqual(call.behaviour_definition.name, Symbol("first"))
        self.assertEqual(call.behaviour_provider, Symbol("<local>"))
        self.assertEqual(run(compile_program(typed)), [7])

    def test_distinct_provider_paths_remain_ambiguous(self):
        from dataclasses import replace
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait Printable => end\n"
            "trait Serializable => extend serialize -> String end\n"
            "trait Printable as Serializable => "
            'define serialize -> String => "one" end\n'
            "object Document => $id: Int end\n"
            "object Document as Printable => end\n"
        ))
        context = analyser.env.context
        behaviour = context.trait_impl_behaviours[0]
        context.trait_impl_patterns.append(
            replace(context.trait_impl_patterns[-1], provider=Symbol("other"))
        )
        context.trait_impl_behaviours.append(
            replace(behaviour, provider=Symbol("other"))
        )
        evidence = T.implementation_pattern_evidence(
            T.N(Symbol("Document")), T.N(Symbol("Serializable")), context
        )
        selected = {
            definition.function.body[0].value
            for path in evidence
            for _, definition in T.implementation_evidence_definitions(
                path, Symbol("serialize"), context
            )
        }
        self.assertEqual(selected, {"one"})
        self.assertEqual({path.provider for path in evidence}, {
            Symbol("<local>"), Symbol("other")
        })



class TraitImplementationVisibilityAndWitnessTests(unittest.TestCase):
    def analyse(self, root: Path, source: str) -> Analyser:
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        analyser.analyse(parse(source))
        return analyser

    def test_object_owned_external_trait_imports_automatically(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traits.vlnc").write_text(
                "public trait Shape => extend area -> Real end\n",
                encoding="utf-8",
            )
            (root / "squares.vlnc").write_text(
                "import { traits.Shape }\n"
                "public object Square =>\n"
                "  $len: Real\n"
                "  public define area -> Real => $self.len ** 2\n"
                "end\n"
                "object Square as Shape => end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(root, "import { squares.Square }\nSquare(5.0) area\n")
            self.assertEqual(analyser.diagnostics, [])

    def test_foreign_object_implementation_requires_explicit_import(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "base.vlnc").write_text(
                "public object Square => $len: Real end\n"
                "public trait Shape => extend area -> Real end\n",
                encoding="utf-8",
            )
            (root / "provider.vlnc").write_text(
                "import { base.[Square, Shape] }\n"
                "object Square as Shape => "
                "define area -> Real => $self.len ** 2 end\n",
                encoding="utf-8",
            )
            implicit = self.analyse(
                root, "import { base.[Square, Shape] }\nSquare(5.0) area\n"
            )
            self.assertTrue(any("area" in d for d in implicit.diagnostics))
            explicit = self.analyse(
                root,
                "import { base.[Square, Shape], "
                "provider.object Square as Shape }\nSquare(5.0) area\n",
            )
            self.assertEqual(explicit.diagnostics, [])

    def test_private_endpoint_blocks_explicit_behaviour_import(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "provider.vlnc").write_text(
                "public object Square => $len: Real end\n"
                "trait HiddenShape => extend area -> Real end\n"
                "object Square as HiddenShape => "
                "define area -> Real => $self.len ** 2 end\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root, "import { provider.[object Square as HiddenShape] }\n"
            )
            self.assertTrue(any("not public" in d for d in analyser.diagnostics))


class ExplicitBehaviourPrecedenceTests(unittest.TestCase):
    def write_modules(self, root: Path, **modules: str) -> None:
        for name, source in modules.items():
            (root / f"{name}.vlnc").write_text(source, encoding="utf-8")

    def analyse(self, root: Path, source: str):
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        typed = analyser.analyse(parse(source))
        return analyser, typed

    def assert_program(self, root: Path, source: str, expected: list[object]) -> None:
        analyser, typed = self.analyse(root, source)
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(loads(dumps(compile_program(typed)))), expected)

    def test_owner_upgrade_preserves_explicit_dispatch_across_trait_boundaries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_modules(
                root,
                text="public trait Display => extend render -> String end\n",
                money="public object Money => $cents: Number end\n",
                box="public object[T] Box => $value: T end\n",
                invoice_format=(
                    "import { money.Money, text.Display }\n"
                    "object Money as Display => define render -> String => \"invoice\" end\n"
                ),
            )
            source = (
                "import { money.Money, text.Display, box.Box, invoice_format.object Money as Display }\n"
                "define show(value: Display) -> String => $value render\n"
                "define to_display(value: Money) -> Display => $value\n"
                "define[T: Display] show_generic(value: T) -> String => $value render\n"
                "define[T: Display] show_box(value: Box[T]) -> String => $value.value render\n"
                "Money(1250) render\n"
                "Money(1250) as[Display] | render\n"
                "$display: Display = Money(1250)\n"
                "$display render\n"
                "Money(1250) show\n"
                "Money(1250) to_display | render\n"
                "Money(1250) show_generic\n"
                "Box(Money(1250)) show_box\n"
            )
            self.assert_program(root, source, ["invoice"] * 7)
            self.write_modules(
                root,
                money=(
                    "import { text.Display }\n"
                    "public object Money =>\n"
                    "  $cents: Number\n"
                    "  public define render -> String => \"money\"\n"
                    "end\n"
                    "public object Coupon => $cents: Number end\n"
                    "object Money as Display => end\n"
                    "object Coupon as Display => define render -> String => \"coupon\" end\n"
                ),
            )
            self.assert_program(root, source, ["invoice"] * 7)
            self.assert_program(
                root,
                "import { money, money as currency, money.[Money, Coupon], text.Display, "
                "invoice_format.object Money as Display }\n"
                "Money(1250) render\n"
                "Money(1250) money::render\n"
                "Coupon(200) money::render\n"
                "Money(1250) currency::render\n"
                "Money(1250) as[money.Display] | render\n",
                ["invoice", "money", "coupon", "money", "money"],
            )
            self.assert_program(
                root,
                "import { money.[Money, Coupon], text.Display, "
                "invoice_format.object Money as Display }\n"
                "define show(value: Money|Coupon) -> String => $value render\n"
                "Money(1250) show\n"
                "Coupon(200) show\n"
                "[Money(1250), Money(1250)] render\n",
                ["invoice", "coupon", ["invoice", "invoice"]],
            )
            self.assert_program(
                root,
                "import { money, money.Money as Currency, text.Display as View, "
                "invoice_format.object Money as Display }\n"
                "Currency(1250) render\n"
                "Currency(1250) as[View] | render\n"
                "Currency(1250) money::render\n",
                ["invoice", "invoice", "money"],
            )
            self.assert_program(
                root,
                "import { money.Money, text.Display }\n"
                "if true =>\n"
                "  import { invoice_format.object Money as Display }\n"
                "  Money(1250) as[Display] | render\n"
                "else => \"unused\" end\n"
                "Money(1250) as[Display] | render\n",
                ["invoice", "money"],
            )

    def test_explicit_implication_overrides_direct_owner_implementation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_modules(
                root,
                text=(
                    "public trait Printable => extend printer_text -> String end\n"
                    "public trait Display => extend render -> String end\n"
                ),
                receipts=(
                    "import { text.[Printable, Display] }\n"
                    "public object Receipt => $id: Int end\n"
                    "object Receipt as Printable => "
                    "define printer_text -> String => \"printer\" end\n"
                    "object Receipt as Display => define render -> String => \"screen\" end\n"
                ),
                printer_format=(
                    "import { text.[Printable, Display] }\n"
                    "trait Printable as Display => define render -> String => $self printer_text end\n"
                ),
            )
            self.assert_program(
                root,
                "import { receipts, receipts.Receipt, text.[Printable, Display], "
                "printer_format.trait Printable as Display }\n"
                "Receipt(42) render\n"
                "Receipt(42) as[Display] | render\n"
                "Receipt(42) receipts::render\n",
                ["printer", "printer", "screen"],
            )

    def test_generic_domains_control_precedence_and_import_conflicts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_modules(
                root,
                text=(
                    "public trait Display => extend render -> String end\n"
                    "public trait Printable => end\n"
                    "public trait Labelled => end\n"
                ),
                boxes=(
                    "import { text.Display }\n"
                    "public object[T] Box => $value: T end\n"
                    "object[T] Box as Display => define render -> String => \"default\" end\n"
                ),
            )
            for provider, bound in (
                ("numeric", "Number"), ("strings", "String"), ("integer", "Int"),
                ("printable", "Printable"), ("labelled", "Labelled"),
            ):
                self.write_modules(root, **{
                    provider: (
                        f"import {{ boxes.Box, text.[Display, Printable, Labelled] }}\n"
                        f"object[T: {bound}] Box as Display => "
                        f"define render -> String => \"{provider}\" end\n"
                    ),
                })
            calls = 'Box[Int](7) render\nBox[String]("hello") render\n'
            self.assert_program(
                root, "import { boxes.Box, text.Display, numeric.object Box as Display }\n" + calls,
                ["numeric", "default"],
            )
            self.assert_program(
                root,
                "import { boxes.Box, text.Display, numeric.object Box as Display, "
                "strings.object Box as Display }\n" + calls,
                ["numeric", "strings"],
            )
            for first, second in (("numeric", "integer"), ("printable", "labelled")):
                with self.subTest(first=first, second=second):
                    analyser, _ = self.analyse(
                        root,
                        f"import {{ boxes.Box, text.Display, {first}.object Box as Display, "
                        f"{second}.object Box as Display }}\n",
                    )
                    self.assertEqual(len(analyser.diagnostics), 1)
                    self.assertIn("ambiguous behaviour set imports", analyser.diagnostics[0])

    def test_source_free_reexport_and_imported_trait_callable_preserve_evidence(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from valiance.main import main_vln

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_modules(
                root,
                text="public trait Display => extend render -> String end\n",
                money=(
                    "import { text.Display }\n"
                    "public object Money => $cents: Number end\n"
                    "object Money as Display => define render -> String => \"money\" end\n"
                ),
                invoice=(
                    "import { money.Money, text.Display }\n"
                    "object Money as Display => define render -> String => \"invoice\" end\n"
                ),
                facade="public import { money.Money, text.Display, invoice.object Money as Display }\n",
                client=(
                    "import { text.Display }\n"
                    "public define show(value: Display) -> String => $value render\n"
                    "public define[T: Display] show_generic(value: T) -> String => $value render\n"
                ),
            )
            for path in sorted(root.glob("*.vlnc")):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main_vln([
                        "compile-module", "--file", str(path), "--output", str(path.with_suffix(".vbcm")),
                    ]), 0)
            for path in root.glob("*.vlnc"):
                path.unlink()
            self.assert_program(
                root,
                "import { money, facade.[Money, Display, object Money as Display], "
                "invoice.object Money as Display, client.[show, show_generic] }\n"
                "Money(1250) show\n"
                "Money(1250) show_generic\n"
                "Money(1250) money::render\n",
                ["invoice", "invoice", "money"],
            )
