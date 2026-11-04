"""Regression tests for cross-module failures resolved before Valiance 0.1.

All former expected failures in this file now run as ordinary positive tests.
A future regression in aliases, reflection or destruction must fail CI.
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
                          "public enum[Int] Code => GOOD = 20 end\n"
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


if __name__ == "__main__":
    unittest.main()
