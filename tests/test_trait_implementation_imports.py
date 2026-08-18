import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.runtime import compile_program, run
from valiance.vtypes.symbols import Symbol


class TraitImplementationImportTests(unittest.TestCase):
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

    def test_multiple_behaviour_set_imports_preserve_their_providers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root, "first")
            self.write_shapes(root, "second")
            analyser = self.analyse(
                root,
                "import { first.[object Rectangle as Shape], "
                "second.[object Rectangle as Shape] }\n",
            )
            self.assertEqual(analyser.diagnostics, [])
            providers = analyser.env.context.implementation_providers(
                Symbol("Rectangle"),
                Symbol("Shape"),
            )
            self.assertEqual(providers, {Symbol("first"), Symbol("second")})

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
                "import { first.Rectangle, "
                "first.object Rectangle as Shape, "
                "second.[object Rectangle as Shape] }\n"
                "Rectangle(4) as[first.Shape] | area\n"
                "Rectangle(4) as[second.Shape] | area\n"
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
                "import { first.Rectangle, "
                "first.object Rectangle as Shape, "
                "second.object Rectangle as Shape }\n"
                "Rectangle(4) first.Shape.area\n"
                "Rectangle(4) second.Shape.area\n"
            )
            analyser = Analyser(
                module_loader=ModuleLoader(),
                source_file=root / "main.vlnc",
            )
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(run(compile_program(typed)), [1, 2])

    def test_trait_argument_reports_ambiguous_behaviour_sets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traits.vlnc").write_text(
                "public trait Shape => end\n",
                encoding="utf-8",
            )
            for name in ("first", "second"):
                (root / f"{name}.vlnc").write_text(
                    "import { traits.Shape }\n"
                    "public object Rectangle => $width: Number end\n"
                    "object Rectangle as Shape => end\n",
                    encoding="utf-8",
                )
            source = (
                "import { traits.Shape, first.Rectangle, "
                "first.object Rectangle as Shape, "
                "second.object Rectangle as Shape }\n"
                "define useShape(value: Shape) => 1 end\n"
                "Rectangle(4) useShape\n"
            )
            analyser = self.analyse(root, source)
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn(
                "ambiguous implementation of Shape for Rectangle",
                diagnostic,
            )
            self.assertIn("first.Shape", diagnostic)
            self.assertIn("second.Shape", diagnostic)

    def test_trait_variable_reports_ambiguous_behaviour_sets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traits.vlnc").write_text(
                "public trait Shape => end\n",
                encoding="utf-8",
            )
            for name in ("first", "second"):
                (root / f"{name}.vlnc").write_text(
                    "import { traits.Shape }\n"
                    "public object Rectangle => $width: Number end\n"
                    "object Rectangle as Shape => end\n",
                    encoding="utf-8",
                )
            source = (
                "import { traits.Shape, first.Rectangle, "
                "first.object Rectangle as Shape, "
                "second.object Rectangle as Shape }\n"
                "$shape: Shape = Rectangle(4)\n"
            )
            analyser = self.analyse(root, source)
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn(
                "ambiguous implementation of Shape for Rectangle",
                diagnostic,
            )
            self.assertIn("first.Shape", diagnostic)
            self.assertIn("second.Shape", diagnostic)

    def test_trait_return_reports_ambiguous_behaviour_sets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traits.vlnc").write_text(
                "public trait Shape => end\n",
                encoding="utf-8",
            )
            for name in ("first", "second"):
                (root / f"{name}.vlnc").write_text(
                    "import { traits.Shape }\n"
                    "public object Rectangle => $width: Number end\n"
                    "object Rectangle as Shape => end\n",
                    encoding="utf-8",
                )
            source = (
                "import { traits.Shape, first.Rectangle, "
                "first.object Rectangle as Shape, "
                "second.object Rectangle as Shape }\n"
                "define makeShape -> Shape => Rectangle(4) end\n"
            )
            analyser = self.analyse(root, source)
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn(
                "ambiguous implementation of Shape for Rectangle",
                diagnostic,
            )
            self.assertIn("first.Shape", diagnostic)
            self.assertIn("second.Shape", diagnostic)

    def test_generic_trait_constraint_reports_ambiguous_behaviour_sets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traits.vlnc").write_text(
                "public trait Shape => end\n",
                encoding="utf-8",
            )
            for name in ("first", "second"):
                (root / f"{name}.vlnc").write_text(
                    "import { traits.Shape }\n"
                    "public object Rectangle => $width: Number end\n"
                    "object Rectangle as Shape => end\n",
                    encoding="utf-8",
                )
            source = (
                "import { traits.Shape, first.Rectangle, "
                "first.object Rectangle as Shape, "
                "second.object Rectangle as Shape }\n"
                "define[T: Shape] useShape(value: T) => 1 end\n"
                "Rectangle(4) useShape\n"
            )
            analyser = self.analyse(root, source)
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn(
                "ambiguous implementation of Shape for Rectangle",
                diagnostic,
            )
            self.assertIn("first.Shape", diagnostic)
            self.assertIn("second.Shape", diagnostic)

    def test_owned_implementation_does_not_outrank_behaviour_set(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root, "owned")
            self.write_shapes(root, "alternative")
            analyser = self.analyse(
                root,
                "import { owned.Rectangle, "
                "alternative.object Rectangle as Shape }\n"
                "Rectangle(1) as[Shape]\n",
            )
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn(
                "ambiguous implementation of Shape for Rectangle",
                diagnostic,
            )
            self.assertIn("owned.Shape", diagnostic)
            self.assertIn("alternative.Shape", diagnostic)

    def test_local_implementation_does_not_outrank_behaviour_set(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root, "alternative")
            source = (
                "trait Shape => end\n"
                "object Rectangle => $width: Number end\n"
                "object Rectangle as Shape => end\n"
                "import { alternative.object Rectangle as Shape }\n"
                "Rectangle(1) as[Shape]\n"
            )
            analyser = self.analyse(root, source)
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn(
                "ambiguous implementation of Shape for Rectangle",
                diagnostic,
            )
            self.assertIn("<local>.Shape", diagnostic)
            self.assertIn("alternative.Shape", diagnostic)

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
                    "behaviour set shapes.Shape does not provide Rectangle as Shape" in diagnostic
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
                "Box(1) as[producers.Producer[Integer]]\n",
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

    def test_overlapping_generic_behaviour_sets_require_qualification(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("first", "second"):
                (root / f"{name}.vlnc").write_text(
                    "public trait[T] Producer => end\n"
                    "public object[T] Box => $value: T end\n"
                    "object[T] Box as Producer[T] => end\n",
                    encoding="utf-8",
                )
            analyser = self.analyse(
                root,
                "import { first.Box, "
                "first.object Box as Producer, "
                "second.object Box as Producer }\n"
                "Box(1) as[Producer[Integer]]\n",
            )
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn(
                "ambiguous implementation of Producer[Integer] for Box[Integer]",
                diagnostic,
            )
            self.assertIn("first.Producer[Integer]", diagnostic)
            self.assertIn("second.Producer[Integer]", diagnostic)

    def test_overlapping_generic_behaviour_sets_can_be_qualified(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("first", "second"):
                (root / f"{name}.vlnc").write_text(
                    "public trait[T] Producer => end\n"
                    "public object[T] Box => $value: T end\n"
                    "object[T] Box as Producer[T] => end\n",
                    encoding="utf-8",
                )
            analyser = self.analyse(
                root,
                "import { first.Box, "
                "first.object Box as Producer, "
                "second.object Box as Producer }\n"
                "Box(1) as[first.Producer[Integer]]\n"
                "Box(2) as[second.Producer[Integer]]\n",
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
                "object Document => $id: Integer end\n"
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
                "object Document => $id: Integer end\n"
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
                "object Item => $id: Integer end\n"
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
            "Box(1) as[Iterable[Integer]]\n"
        ))
        self.assertEqual(analyser.diagnostics, [])

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
                "object Document => $id: Integer end\n"
                "object Document as Printable => end\n"
                "Document(1) as[Displayable]\n"
            ))
            diagnostic = "\n".join(analyser.diagnostics)
            self.assertIn("ambiguous implementation of Displayable for Document", diagnostic)
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
            for module, value in (("first", 1), ("second", 2)):
                (root / f"{module}.vlnc").write_text(
                    "public trait[T] Producer => extend produce -> T end\n"
                    "public object[T] Box => $value: T end\n"
                    "object[T] Box as Producer[T] => "
                    f"define produce => {value} end\n",
                    encoding="utf-8",
                )
            source = (
                "import { first.Box, first.object Box as Producer, "
                "second.object Box as Producer }\n"
                "Box(9) as[first.Producer[Integer]] | produce\n"
                "Box(9) as[second.Producer[Integer]] | produce\n"
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(run(compile_program(typed)), [1, 2])


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
            "trait[T] Producer => end\n"
            "trait[T] Iterable => extend first -> T end\n"
            "trait[T] Producer as Iterable[T] => define first => 7 end\n"
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
            T.N(Symbol("Box"), T.N(Symbol("Integer"))),
            T.N(Symbol("Iterable"), T.N(Symbol("Integer"))),
            analyser.env.context,
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].provider, Symbol("<local>"))
        self.assertEqual(
            evidence[0].traits,
            (
                T.N(Symbol("Producer"), T.N(Symbol("Integer"))),
                T.N(Symbol("Iterable"), T.N(Symbol("Integer"))),
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
            "object Document => $id: Integer end\n"
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
            "object Document => $id: Integer end\n"
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
            T.N(Symbol("Box"), T.N(Symbol("Integer"))),
            T.N(Symbol("Iterable"), T.N(Symbol("Integer"))),
            analyser.env.context,
        )
        joined = T.implementation_evidence_behaviours(
            evidence, Symbol("first"), analyser.env.context
        )
        self.assertEqual(len(joined), 1)
        edge, behaviour = joined[0]
        self.assertEqual(str(edge.source), "Producer[Integer]")
        self.assertEqual(str(edge.target), "Iterable[Integer]")
        self.assertEqual(behaviour.element_names, (Symbol("first"),))

    def test_evidence_join_ignores_unrelated_elements_and_edges(self):
        import valiance.vtypes as T

        analyser = Analyser()
        analyser.analyse(parse(
            "trait Printable => extend display -> String end\n"
            "trait Serializable => extend serialize -> String end\n"
            "trait Printable as Serializable => "
            'define serialize -> String => "serialized" end\n'
            "object Document => $id: Integer end\n"
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
            "object Document => $id: Integer end\n"
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
            "object Document => $id: Integer end\n"
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

