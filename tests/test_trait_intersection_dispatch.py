"""Regression tests for trait requirement calls through intersections and collections."""

import unittest

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.interface_serialization import dumps_interface, loads_interface


SOURCE = """
trait Named =>
  extend getName -> String
end
trait Colourful =>
  extend getColour -> String
end
object Square =>
  $length: Real
  define getName => "Square"
  define getColour => "Red"
end
object Square as Named => end
object Square as Colourful => end
define draw(obj: Named & Colourful) =>
  "Drawing a ${getName $obj} which is coloured ${getColour $obj}"
end
Square 10
draw top
"""


def test_trait_requirements_are_available_from_intersection_receiver():
    analyser = Analyser()
    typed = analyser.analyse(parse(SOURCE))

    assert analyser.diagnostics == []
    assert T.same(typed[-1].typ, T.String)


def test_trait_requirements_dispatch_from_intersection_receiver_at_runtime():
    analyser = Analyser()
    typed = analyser.analyse(parse(SOURCE))

    assert analyser.diagnostics == []
    assert run(compile_program(typed, optimize=False)) == [
        "Drawing a Square which is coloured Red"
    ]


DIRECTORY_SOURCE = """
trait Entry =>
  extend storedBytes -> Int
end

object File =>
  $fileName: String
  $fileSize: Int
end
object File as Entry => define storedBytes => $self.fileSize

object Shortcut =>
  $name: String
  $target: String
end
object Shortcut as Entry => define storedBytes => 0

object Folder =>
  $folderName: String
  $entries: Entry+
end
object Folder as Entry => define storedBytes => sum storedBytes $self.entries

const $documents = Folder("Documents", [
  File("notes.txt", 1200),
  File("essay.pdf", 4500),
  Folder("Images", [
    File("cat.png", 8000),
    File("dog.png", 6000)
  ]),
  Shortcut("Latest Essay", "essay.pdf")
])

storedBytes $documents
"""


class TraitCollectionDispatchTests(unittest.TestCase):
    def assert_executes(self, source, expected):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        for optimize in (False, True):
            with self.subTest(optimize=optimize):
                program = compile_program(typed, optimize=optimize)
                self.assertEqual(run(program), expected)
                self.assertEqual(run(loads(dumps(program))), expected)
                restored = loads_interface(dumps_interface(tuple(typed)))
                self.assertEqual(run(compile_program(restored, optimize=optimize)), expected)

    def test_recursive_heterogeneous_directory(self):
        self.assert_executes(DIRECTORY_SOURCE, [19700])

    def test_scalar_trait_intersection(self):
        self.assert_executes(
            SOURCE, ["Drawing a Square which is coloured Red"],
        )

    def test_empty_trait_collection(self):
        self.assert_executes(
            DIRECTORY_SOURCE.rsplit('storedBytes $documents', 1)[0]
            + 'storedBytes Folder("Empty", [] as[Entry+])',
            [0],
        )

    def test_nested_trait_collection_and_single_implementation(self):
        self.assert_executes("""
trait Sized => extend size -> Int end
object Item => $value: Int end
object Item as Sized => define size => $self.value
$items: Sized++ = [[Item(3), Item(4)], [Item(5), Item(6)]]
size $items
""", [[[3, 4], [5, 6]]])

    def test_collection_of_trait_intersections(self):
        self.assert_executes(SOURCE.rsplit('Square 10', 1)[0] + """
$items: (Named & Colourful)+ = [Square(10), Square(20)]
getName $items
getColour $items
""", [["Square", "Square"], ["Red", "Red"]])

    def test_generic_trait_collection(self):
        self.assert_executes("""
trait[T] Source => extend read -> T end
object Item => $value: Int end
object Item as Source[Int] => define read => $self.value
$items: Source[Int]+ = [Item(3), Item(4)]
read $items
""", [[3, 4]])

    def test_trait_collection_with_broadcast_method_argument(self):
        self.assert_executes("""
trait Sized => extend add(:Int) -> Int end
object Item => $value: Int end
object Item as Sized => define add(n: Int) => $self.value + $n
object Other => $value: Int end
object Other as Sized => define add(n: Int) => $self.value + $n + 1
$items: Sized+ = [Item(3), Other(4)]
10 | add $items
""", [[13, 15]])

    def test_invalid_trait_method_argument_is_rejected(self):
        analyser = Analyser()
        analyser.analyse(parse("""
trait Sized => extend size -> Int end
object Item => $value: Int end
object Item as Sized => define size => $self.value
$items: Sized+ = [Item(3)]
size("invalid", $items)
"""))
        self.assertTrue(analyser.diagnostics)
        self.assertTrue(any("no overloads" in str(error) for error in analyser.diagnostics))
