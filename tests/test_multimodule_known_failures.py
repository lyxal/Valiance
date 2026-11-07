"""Cross-module runtime linking and nominal-identity regressions.

These tests require verified runtime behavior in source and compiled layouts;
previously unresolved linker scenarios run as ordinary positive tests.
"""

from __future__ import annotations

import unittest
from tools.multimodule_fuzzing import ProjectCase
from tools.multimodule_interactions import execute_interaction
from tools.multimodule_lifetime_concurrency_contracts import IMPORTED_DESTRUCTOR_REGRESSIONS


class KnownMultiModuleFailures(unittest.TestCase):
    def test_mm_link_stdjson_001_transitive_std_json_helper(self):
        project = ProjectCase(
            "MM-LINK-STDJSON-001", "direct", 7,
            (
                ("core", "public object Item => public $value:Int end\n"),
                ("logic", "import {core.Item}\nimport {std.json}\n"
                          "public define encode(value:Item)->String=>"
                          "json.encode($value) ?! end\n"),
            ),
            "import {core.Item,logic.encode}\nencode(Item(7))\n",
            ('{"value":7}',),
            ("core", "logic"),
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                self.assertEqual(execute_interaction(project, layout), 4)


    def test_mm_alias_enum_overload_identity(self):
        """Renaming a nominal enum through a facade must retain overload identity.

        Re-exported callable contracts must use the selected nominal spelling.
        """
        project = ProjectCase(
            "MM-ALIAS-ENUM-OVERLOAD-001", "alias", 20,
            (
                ("core", "public trait Named => public define label -> String => "
                         "\"default\" end end\n"
                         "public object Item => public $value:Int end\n"
                         "object Item as Named => end\n"),
                ("logic", "import {core.[Named,Item]}\n"
                          "public variant Code => GOOD = 20 end\n"
                          "public define show(x:Item)->String=>$x as[Named] | label end\n"
                          "public define show(x:Code)->Int=>$x.value+1 end\n"),
                ("gateway", "public import {core.[Named,Item],logic.[Code,show]}\n"),
                ("api", "public import {gateway.[Named,Item,Code as AliasCode,show]}\n"),
            ),
            "import {api.[Named,Item,AliasCode,show]}\nshow(AliasCode.\\GOOD)\n",
            (21,), ("core", "logic", "gateway", "api"),
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                self.assertEqual(execute_interaction(project, layout), 4)


    def _assert_imported_destructor(self, family):
        """A module-imported destructor must run at its final owned release.

        A module-qualified constructor must retain its private lifecycle hook.
        """
        (_features, provider, provider_names, dependent, dependent_names,
         consumer, values, stdout) = IMPORTED_DESTRUCTOR_REGRESSIONS[family]
        selections = f"core.[{','.join(provider_names)}],logic.[{','.join(dependent_names)}]"
        project = ProjectCase(
            family, "direct", 73,
            (("core", provider), ("logic", dependent)),
            f"import {{{selections}}}\n{consumer.replace('@N@', '17')}",
            tuple(int(value) for value in values),
            ("core", "logic"),
            stdout.replace('@N@', '17'),
        )
        for layout in ("source", "compiled"):
            with self.subTest(family=family, layout=layout):
                self.assertEqual(execute_interaction(project, layout), 4)

    def test_imported_trait_destructor_runs_on_release(self):
        self._assert_imported_destructor("destructor_through_trait")

    def test_imported_generic_destructor_runs_on_release(self):
        self._assert_imported_destructor("destructor_generic_forward")

    def test_imported_nested_destructor_runs_on_release(self):
        self._assert_imported_destructor("destructor_nested_owner")

    def test_public_facade_retains_private_destructors_in_binary_modules(self):
        """An intermediate public import must preserve the final-release hook."""
        for family in ("destructor_through_trait", "destructor_generic_forward",
                       "destructor_nested_owner"):
            (_features, provider, provider_names, dependent, dependent_names,
             consumer, values, stdout) = IMPORTED_DESTRUCTOR_REGRESSIONS[family]
            selections = (f"core.[{','.join(provider_names)}],"
                          f"logic.[{','.join(dependent_names)}]")
            public = ",".join((*provider_names, *dependent_names))
            project = ProjectCase(
                family + "_facade", "facade", 73,
                (("core", provider), ("logic", dependent),
                 ("gateway", f"public import {{{selections}}}\n")),
                f"import {{gateway.[{public}]}}\n{consumer.replace('@N@', '17')}",
                tuple(int(value) for value in values),
                ("core", "logic", "gateway"), stdout.replace('@N@', '17'),
            )
            for layout in ("source", "compiled"):
                with self.subTest(family=family, layout=layout):
                    self.assertEqual(execute_interaction(project, layout), 4)

    def _generic_reflection_reexport_alias(self, layout: str):
        """An exported generic function must accept its owner's selected alias."""
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs

        modules = {
            "core": "public object[T] Box=>public $value:T end\n",
            "logic": (
                "import {core.Box}\n"
                "public define[T] reflect(x:Box[T])->T =>\n"
                ' $field=fields[Box[T]]["value"]\n'
                " read?($x,$field) ?!\nend\n"
            ),
            "gateway": "public import {core.Box as Renamed,logic.reflect}\n",
        }
        return ReconstructedMultiModuleRepairs.run_project(
            modules, "import {gateway.[Renamed,reflect]}\nreflect(Renamed(9))\n", layout
        )

    def test_generic_reflection_reexport_alias_source(self):
        self.assertEqual(self._generic_reflection_reexport_alias("source"), [[9]] * 4)

    def test_generic_reflection_reexport_alias_compiled(self):
        self.assertEqual(self._generic_reflection_reexport_alias("compiled"), [[9]] * 4)

    def _renamed_object_lifecycle(self, layout: str):
        """An alias must retain the original destructor's final-release hook."""
        (_features, provider, provider_names, dependent, dependent_names,
         consumer, values, stdout) = IMPORTED_DESTRUCTOR_REGRESSIONS[
             "destructor_through_trait"
         ]
        selections = (f"core.[{','.join(provider_names)}],"
                      f"logic.[{','.join(dependent_names)}]")
        project = ProjectCase(
            "renamed_object_lifecycle", "alias", 73,
            (("core", provider), ("logic", dependent),
             ("gateway", f"public import {{{selections}}}\n"),
             ("api", "public import {gateway.[Marker,Item as Renamed,consume]}\n")),
            "import {api.[Marker,Renamed,consume]}\nconsume(Renamed(17))\n",
            (1,), ("core", "logic", "gateway", "api"), "released 17\n",
        )
        self.assertEqual(execute_interaction(project, layout), 4)

    def test_renamed_object_destructor_keeps_nominal_dispatch(self):
        self._renamed_object_lifecycle("source")

    def test_renamed_object_destructor_compiled_alias_dispatch(self):
        self._renamed_object_lifecycle("compiled")


    def _public_constructor_needed_by_private_provider_body(self, layout: str):
        """A public helper must link constructors it uses without reimporting them."""
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs

        modules = {
            "core": (
                "public object Box=>public $value:Int end\n"
                "public define fetch(value:Int)->Int=>\n"
                " $b=Box($value)\n $b.value\nend\n"
            ),
        }
        values = ReconstructedMultiModuleRepairs.run_project(
            modules, "import {core.fetch}\nfetch(9)\n", layout,
        )
        self.assertEqual(values, [[9]] * 4)

    def test_unselected_public_constructor_still_needed_by_helper_source(self):
        self._public_constructor_needed_by_private_provider_body("source")

    def test_unselected_public_constructor_still_needed_by_helper_compiled(self):
        self._public_constructor_needed_by_private_provider_body("compiled")


    def test_provider_helper_constructor_is_independent_of_import_selection(self):
        """A provider's checked body keeps its constructor through aliases.

        Importing the object and helper in separate clauses must behave just
        like importing the helper alone. The object remains unimported unless
        the consumer explicitly selects it.
        """
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs
        from contextlib import redirect_stdout
        from io import StringIO

        modules = {"core": (
            "public object Box=>\n public $value:Int\n"
            ' define ~Box=>"released" println\nend\n'
            "public define fetch(value:Int)->Int=>\n"
            " $box=Box($value)\n $box.value\nend\n"
        )}
        consumers = (
            ("import {core.fetch}\nfetch(9)\n", 4),
            ("import {core.[Box,fetch]}\nBox(4) pop\nfetch(9)\n", 8),
            ("import {core.Box as Alias,core.fetch}\nAlias(4) pop\nfetch(9)\n", 8),
        )
        for layout in ("source", "compiled"):
            for source, releases in consumers:
                with self.subTest(layout=layout, source=source):
                    stdout = StringIO()
                    with redirect_stdout(stdout):
                        result = ReconstructedMultiModuleRepairs.run_project(
                            modules, source, layout,
                        )
                    self.assertEqual(result, [[9]] * 4)
                    self.assertEqual(stdout.getvalue(), "released\n" * releases)
            with self.subTest(layout=layout, visibility=True):
                with self.assertRaisesRegex(AssertionError, "unknown element 'Box'"):
                    ReconstructedMultiModuleRepairs.run_project(
                        modules, "import {core.fetch}\nBox(9)\n", layout,
                    )

    def test_public_provider_constructor_survives_deep_compiled_facades(self):
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs

        modules = {
            "core": (
                "public object Box=>public $value:Int end\n"
                "public define fetch(value:Int)->Int=>\n"
                " $box=Box($value)\n $box.value\nend\n"
            ),
            "gateway": "public import {core.fetch}\n",
            "api": "public import {gateway.fetch}\n",
        }
        for layout in (
            "source", "compiled", frozenset({"core", "api"}),
            frozenset({"gateway"}),
        ):
            with self.subTest(layout=layout):
                self.assertEqual(
                    ReconstructedMultiModuleRepairs.run_project(
                        modules, "import {api.fetch}\nfetch(9)\n", layout,
                    ),
                    [[9]] * 4,
                )

    def test_private_runtime_constructors_keep_independent_providers(self):
        """Identically named public objects are distinct executable dependencies."""
        from contextlib import redirect_stdout
        from io import StringIO
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs

        modules = {
            name: (
                "public object Box=>\n public $value:Int\n"
                f' define ~Box=>"{name}" println\nend\n'
                f"public define fetch{name.title()}(value:Int)->Int=>\n"
                " $box=Box($value)\n $box.value\nend\n"
            )
            for name in ("alpha", "beta")
        }
        source = (
            "import {alpha.fetchAlpha,beta.fetchBeta}\n"
            "fetchAlpha(1)\nfetchBeta(2)\n"
        )
        for layout in ("source", "compiled", frozenset({"alpha"})):
            with self.subTest(layout=layout):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    actual = ReconstructedMultiModuleRepairs.run_project(
                        modules, source, layout,
                    )
                self.assertEqual(actual, [[1, 2]] * 4)
                self.assertEqual(stdout.getvalue(), "alpha\nbeta\n" * 4)


if __name__ == "__main__":
    unittest.main()
