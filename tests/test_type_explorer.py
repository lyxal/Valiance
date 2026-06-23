import unittest

from valiance.type_explorer import command, parse_type
from valiance.types import ArrayMinType, C, Fn, ListExactType, N, U, optional

Number = N("Number")
String = N("String")


class TypeExplorerTests(unittest.TestCase):
    def test_parse_collection_ranks(self):
        self.assertEqual(parse_type("Number++"), C(ListExactType, Number, 2))
        self.assertEqual(parse_type("Number+2"), C(ListExactType, Number, 2))
        self.assertEqual(parse_type("Number>2"), C(ArrayMinType, Number, 2))
        self.assertEqual(str(parse_type("Number++*")), "Number*3")
        self.assertEqual(str(parse_type("Number+~")), "Number~2")
        self.assertEqual(str(parse_type("Number^^>")), "Number>3")

    def test_parse_union_and_optional(self):
        self.assertEqual(parse_type("Number|String"), U(Number, String))
        self.assertEqual(parse_type("Number?"), optional(Number))
        self.assertEqual(str(parse_type("Shape&Drawable")), "Drawable & Shape")

    def test_parse_function(self):
        self.assertEqual(
            parse_type("Function[Number+, Number+ -> Number+]"),
            Fn((C(ListExactType, Number), C(ListExactType, Number)), (C(ListExactType, Number),)),
        )

    def test_commands(self):
        self.assertEqual(command("assignable Number^ -> Number+"), "True")
        self.assertIn("True", command("compatible Number+ -> Number"))
        self.assertIn("T: [Number+] => Number+", command("solve T+ <- Number++"))
        self.assertIn("(Number, Number) -> Number", command("overload + (Number, Number)"))
        self.assertIn("(Number, Number) -> Number", command("overload + (Number+, Number)"))
        self.assertIn("True", command("compatible + -> Function[Number+, Number+ -> Number+]"))
        self.assertIn("1. (Number, Number) -> Number", command("overloads +"))
        self.assertIn("length:", command("overloads"))
        self.assertEqual(
            command("infer fn => + end"),
            "OverloadSet[Function[Number, Number -> Number], Function[String, String -> String]]",
        )
        self.assertEqual(command("infer fn => + 2 / end"), "Function[Number, Number -> Number]")
        self.assertIn("(T+) -> Number", command("overload length (Number++)"))
        self.assertIn("(T+) -> Number", command("overload length (Number*)"))
        self.assertIn("(T+) -> Number", command("overload length (Number~)"))
        self.assertIn("defined foo overload", command("defover foo (Number|String) -> String"))
        self.assertIn("(Number | String) -> String", command("overload foo (Number)"))
        self.assertIn("defined reduce overload", command("defover reduce (T+, Function[T, T -> T]) -> T"))
        self.assertIn("(T+, Function[T, T -> T]) -> T", command("overloads reduce"))
        self.assertIn(
            "(T+, Function[T, T -> T]) -> T",
            command("overload reduce (Number++, Function[Number, Number -> Number])"),
        )
        reduce_result = command("overload reduce (Number++, Function[Number, Number -> Number])")
        self.assertIn("T = Number+", reduce_result)
        self.assertIn("instantiated: (Number+2, Function[Number+, Number+ -> Number+]) -> Number+", reduce_result)

    def test_trait_commands_and_intersections(self):
        self.assertEqual(command("impl Circle Shape"), "Circle implements Shape")
        self.assertEqual(command("impl Circle Drawable"), "Circle implements Drawable")
        self.assertIn("Circle implements Shape", command("traits"))
        self.assertIn("True", command("compatible Circle -> Shape&Drawable"))

    def test_trait_parent_command(self):
        self.assertEqual(command("impl FileLogger Logger"), "FileLogger implements Logger")
        self.assertEqual(command("trait Logger Resource"), "Logger implements Resource")
        self.assertIn("True", command("compatible FileLogger -> Resource"))


if __name__ == "__main__":
    unittest.main()
