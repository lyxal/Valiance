import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.elements.stdlib_native import (
    attach_native_object_elements,
    native_function,
    native_module_exports,
)
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.runtime import compile_program, run
from valiance.runtime.runtime_values import ObjectValue, RuntimeNumber
from valiance.vtypes.symbols import Symbol


def _double(args, ctx):
    del ctx
    receiver, = args
    return (receiver.fields["value"] * RuntimeNumber(2),)


class NativeObjectElementTests(unittest.TestCase):
    def setUp(self):
        self.function = native_function(
            "double",
            (),
            (T.Number,),
            _double,
            owner="Box",
        )

    def test_attaches_native_friendly_definition_to_vlnc_object(self):
        program = parse("public object Box => $value: Number end")

        with patch(
            "valiance.elements.stdlib_native._native_functions",
            return_value=(self.function,),
        ):
            attached = attach_native_object_elements(program, "native_test")

        [box] = attached
        self.assertEqual([definition.name.text for definition in box.definitions], ["double"])
        self.assertEqual(box.definitions[0].function.params, ())

    def test_attached_element_exports_receiver_aware_runtime_wrapper(self):
        with patch(
            "valiance.elements.stdlib_native._native_functions",
            return_value=(self.function,),
        ):
            exports = native_module_exports("native_test")

        self.assertIsNotNone(exports)
        [definition] = exports.definitions
        [typing] = definition.typed.overloads
        self.assertEqual(typing.overload.params, (T.N(Symbol("Box")),))

    def test_executes_python_friendly_element_on_vlnc_object(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native_test.vlnc").write_text(
                "public object Box => $value: Number end\n",
                encoding="utf-8",
            )
            analyser = Analyser(module_loader=ModuleLoader(std_root=root))
            source = "import { std.native_test }\nnative_test.Box(21) native_test.double"

            with patch(
                "valiance.elements.stdlib_native._native_functions",
                return_value=(self.function,),
            ):
                typed = analyser.analyse(parse(source))

            self.assertEqual(analyser.diagnostics, [])
            with patch(
                "valiance.elements.stdlib_native._all_native_modules",
                return_value={"native_test": (self.function,)},
            ):
                stack = run(compile_program(typed, optimize=False))

        self.assertEqual(stack, [RuntimeNumber(42)])

    def test_reports_missing_vlnc_owner(self):
        with patch(
            "valiance.elements.stdlib_native._native_functions",
            return_value=(self.function,),
        ):
            with self.assertRaisesRegex(ValueError, "owner.*Box"):
                attach_native_object_elements([], "native_test")


if __name__ == "__main__":
    unittest.main()
