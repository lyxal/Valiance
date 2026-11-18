"""Source-level contracts for trait analysis and closed variant semantics.

Keep acceptance, diagnostic and executable expectations together so a permissive
analyser cannot hide incorrect dispatch or lost generic evidence.
"""

import unittest

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import RuntimeError as ValianceRuntimeError, compile_program, dumps, loads, run
from valiance.runtime.interface_serialization import dumps_interface, loads_interface
from valiance.runtime.runtime_values import BorrowedValue, ObjectValue


class SemanticTestCase(unittest.TestCase):
    def analyse(self, source):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [], source)
        return typed

    def assert_executes(self, source, expected):
        typed = self.analyse(source)
        restored = loads_interface(dumps_interface(tuple(typed)))
        for optimize in (False, True):
            for interface, nodes in ((False, typed), (True, restored)):
                with self.subTest(optimize=optimize, interface=interface):
                    program = compile_program(nodes, optimize=optimize)
                    self.assertEqual(run(program), expected)
                    self.assertEqual(run(loads(dumps(program))), expected)

    def assert_rejected(self, source, message):
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(
            any(message in diagnostic for diagnostic in analyser.diagnostics),
            analyser.diagnostics,
        )


class TraitSemanticTests(SemanticTestCase):
    def test_imported_generic_map_alias_preserves_explicit_native_list_escape(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, mapIterable as map]}
object Count => $limit: Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
[2,3] *::map: fn(n:Int)->Int => $n*2 end
  foreach(item) => break $item end
map(Count(2), fn(item:Int)->Int => $item+1 end)
  foreach(item) => break $item end
''', [4, 11])

    def test_imported_iterable_aliases_share_native_map_and_filter_overloads(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, mapIterable as map, filterIterable as filter]}
object Count => $limit: Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
[2,3] map: fn(n:Int)->Int => $n*2 end
  foreach(item) => break $item end
[2,3] filter: fn(n:Int)->#boolean Int => $n==3 end
  foreach(item) => break $item end
map(Count(2), fn(item:Int)->Int => $item+1 end)
  foreach(item) => break $item end
filter(Count(2), fn(item:Int)->#boolean Int => $item>10 end)
  foreach(item) => break $item end
''', [4, 3, 11, 11])

    def test_native_list_foreach_index_precedes_value_and_retains_unary_value(self):
        self.assert_executes(r'''
[7, 8] foreach (index, value) => break {$index, $value} end
[7, 8] foreach (item) => break $item end
[7, 8] foreach (index, value) =>
  if ($index == 1) => break $value end
end
''', [(0, 7), 7, 8])

    def test_json_object_member_view_adapts_to_generic_traversal(self):
        self.assert_executes(r'''
import {std.json}
import {std.iterable}
import {std.json_iterable}
$empty = json.load("{}") ?!
$empty match =>
  as obj: json.Json.Object =>
    json_iterable.memberView($obj) foreach(key) => break $key end
  _ => "not object"
end
$doc = json.load("{\"first\":1,\"second\":2}") ?!
$doc match =>
  as obj: json.Json.Object =>
    $view = json_iterable.memberView($obj)
    $view foreach(key) => break $key end
    $view foreach(key,value) => break {$key, json.stringify($value) ?!} end
    iterable.filterIterable($view, fn(key:String)->#boolean Int => $key=="second" end)
      foreach(key) => break $key end
    iterable.mapIterable($view,
      fn(key:String,value:json.Json)->String => $key+"="+(json.stringify($value) ?!) end)
      foreach(index,value) => break {$index,$value} end
    iterable.foldIterable($view, "", fn(acc:String,key:String)->String => $acc+$key end)
  _ => "not object" "not object" "not object" "not object" "not object"
end
''', [ObjectValue("None", {}), "first", ("first", "1"), "second",
     ("first", "first=1"), "firstsecond"])

    def test_selective_iterable_functions_use_provider_evidence_without_trait_import(self):
        # Importing a function does not import its nominal parameter types as
        # source-visible names, but must keep those types' canonical identity.
        self.assert_executes(r'''
import {std.json, std.json_iterable}
import {std.iterable.[mapIterable as transform, filterIterable, foldIterable, reduceIterable]}
$doc = json.load("{\"first\":1,\"second\":2}") ?!
$doc match =>
  as obj: json.Json.Object =>
    $view = json_iterable.memberView($obj)
    $selected = filterIterable($view,
      fn(key:String)->#boolean Int => $key=="second" end)
    $mapped = transform($selected, fn(key:String)->String => $key+"!" end)
    foldIterable($mapped, "", fn(acc:String,item:String)->String => $acc+$item end)
    reduceIterable($selected, fn(acc:String,key:String)->String => $acc+$key end) ?!
    foldIterable($view, "", fn(acc:String,key:String)->String => $acc+$key end)
  _ => "not object" "not object" "not object"
end
''', ["second!", "second", "firstsecond"])

    def test_callable_import_reuses_trait_alias_from_earlier_import(self):
        self.assert_executes(r'''
import {std.json, std.json_iterable}
import {std.iterable.[Iterable as Flow, SingleView as Projection]}
import {std.iterable.[mapIterable]}
$doc = json.load("{\"key\":1}") ?!
$doc match =>
  as obj: json.Json.Object =>
    $view = json_iterable.memberView($obj)
    $mapped: Flow[String,String,{{String,json.Json}+,Int,Int}] =
      mapIterable($view, fn(key:String)->String => $key end)
    $mapped foreach(item) => break $item end
  _ => "not object"
end
''', ["key"])

    def test_selective_callable_import_does_not_expose_or_conflate_traits(self):
        self.assert_rejected(r'''
import {std.iterable.[foldIterable]}
fn(source: Iterable[Int,Int,Int]) -> Int => 0 end
''', "undefined type 'Iterable'")
        self.assert_rejected(r'''
import {std.iterable.[foldIterable]}
trait[I,V,S] Iterable =>
  extend initial -> S
  extend advance(state:S) -> {I,V,S}?
end
object Count => $limit:Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state,$state+1})
    else => \None end
  end
end
foldIterable(Count(2),0,fn(total:Int,value:Int)->Int=>$total+$value end)
''', "no overloads for element 'foldIterable'")

    def test_json_member_view_requires_a_matched_object_case(self):
        self.assert_rejected(r'''
import {std.json}
import {std.json_iterable}
json_iterable.memberView(json.load("[]") ?!)
''', "no overloads")

    def test_json_variant_base_is_not_automatically_iterable(self):
        self.assert_rejected(r'''
import {std.json}
$doc = json.load("[1,2]") ?!
$doc foreach(item) => break $item end
''', "for loop iterable must actually be iterable")

    def test_namespaced_iterable_import_resolves_generic_and_projected_calls(self):
        self.assert_executes(r'''
import {std.iterable}
object Count => $limit: Int end
object Count as iterable.Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int,Int,Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
object Count as iterable.SingleView[Int,Int,String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end
define total(source: iterable.Iterable[Int,Int,Int]) -> Int =>
  iterable.foldIterable($source, 0, fn(acc:Int,value:Int)->Int => $acc+$value end)
end
Count(3) foreach (item) => break $item end
Count(3) foreach (index,value) => break {$index,$value} end
iterable.mapIterable(Count(3), fn(item:String)->Int => 4 end)
  foreach (item) => break $item end
iterable.filterIterable(Count(4), fn(item:String)->#boolean Int => $item=="2" end)
  foreach (item) => break $item end
iterable.foldIterable(Count(3), "", fn(acc:String,item:String)->String => $acc+$item end)
total(Count(3))
''', ["0", (0, 10), 4, "2", "012", 33])

    def test_renamed_namespace_and_selected_trait_aliases_retain_identity(self):
        self.assert_executes(r'''
import {std.iterable as it}
object Count => $limit:Int end
object Count as it.Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
it.filterIterable(Count(3), fn(index:Int,value:Int)->#boolean Int => $index>0 end)
  foreach (index,value) => break {$index,$value} end
it.foldIterable(Count(3), 0, fn(acc:Int,index:Int,value:Int)->Int => $acc+$index end)
''', [(1, 11), 3])
        self.assert_executes(r'''
import {std.iterable.[Iterable as Flow, SingleView as Projection, mapIterable, foldIterable]}
object Count => $limit:Int end
object Count as Flow[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
object Count as Projection[Int,Int,String] =>
  define single(index:Int,value:Int)->String => $index to[String]
end
Count(3) foreach (item) => break $item end
mapIterable(Count(3), fn(item:String)->Int => 7 end)
  foreach (item) => break $item end
foldIterable(Count(3),"",fn(acc:String,item:String)->String => $acc+$item end)
''', ["0", 7, "012"])

    def test_std_iterable_lazy_map_and_filter_unary_and_indexed(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, mapIterable, filterIterable]}
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
mapIterable(Count(3), fn(value: Int) -> String => $value to[String] end)
  foreach (index, value) => break {$index, $value} end
mapIterable(Count(3), fn(index: Int, value: Int) -> String => $index to[String] end)
  foreach (item) => break $item end
filterIterable(Count(4), fn(value: Int) -> #boolean Int => $value > 11 end)
  foreach (index, value) => break {$index, $value} end
filterIterable(Count(4), fn(index: Int, value: Int) -> #boolean Int => $index >= 2 end)
  foreach (index, value) => break {$index, $value} end
''', [(0, "10"), "0", (2, 12), (2, 12)])

    def test_std_iterable_adapters_compose_retain_indices_and_handle_empty(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, mapIterable, filterIterable]}
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
$empty = mapIterable(Count(0), fn(value: Int) -> Int => $value + 1 end)
$empty foreach (value) => break $value end
$none = filterIterable(Count(3), fn(value: Int) -> #boolean Int => $value > 100 end)
$none foreach (item) => break $item end
$chained = mapIterable(
  filterIterable(Count(4), fn(value: Int) -> #boolean Int => $value > 11 end),
  fn(index: Int, value: Int) -> String => $index to[String] end
)
$chained foreach (index, value) => break {$index, $value} end
$chained foreach (index, value) => break {$index, $value} end
''', [ObjectValue("None", {}), ObjectValue("None", {}), (2, "2"), (2, "2")])

    def test_std_iterable_adapter_uses_value_not_optional_source_single_view(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, SingleView, mapIterable, filterIterable]}
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
object Count as SingleView[Int, Int, String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end
Count(3) foreach (item) => break $item end
mapIterable(Count(3), fn(value: Int) -> Int => $value + 1 end)
  foreach (item) => break $item end
filterIterable(Count(3), fn(value: Int) -> #boolean Int => $value > 10 end)
  foreach (item) => break $item end
''', ["0", 11, 11])

    def test_std_iterable_projected_map_and_filter_keep_original_indices(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, SingleView, mapIterable, filterIterable]}
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
object Count as SingleView[Int, Int, String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end
mapIterable(Count(4), fn(key: String) -> String => $key end)
  foreach (index, value) => break {$index, $value} end
filterIterable(Count(4), fn(key: String) -> #boolean Int => $key == "2" end)
  foreach (key) => break $key end
filterIterable(Count(4), fn(key: String) -> #boolean Int => $key == "2" end)
  foreach (index, value) => break {$index, $value} end
mapIterable(Count(4), fn(value: Int) -> Int => $value end)
  foreach (item) => break $item end
filterIterable(Count(4), fn(value: Int) -> #boolean Int => $value > 11 end)
  foreach (item) => break $item end
''', [(0, "0"), "2", (2, 12), 10, 12])

    def test_std_iterable_projected_overload_wins_for_same_item_type(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, SingleView, mapIterable, filterIterable]}
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
object Count as SingleView[Int, Int, Int] =>
  define single(index: Int, value: Int) -> Int => $index + 100
end
mapIterable(Count(3), fn(item: Int) -> Int => $item * 2 end)
  foreach (item) => break $item end
filterIterable(Count(3), fn(item: Int) -> #boolean Int => $item > 100 end)
  foreach (item) => break $item end
''', [200, 101])

    def test_std_iterable_projected_adapters_compose_and_keep_evidence(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, SingleView, mapIterable, filterIterable]}
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
object Count as SingleView[Int, Int, String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end
define selected(source: Iterable[Int,Int,Int]&SingleView[Int,Int,String]) =>
  $filtered = filterIterable($source, fn(item: String) -> #boolean Int => $item != "0" end)
  $filtered foreach (item) => break $item end
end
define mapped(source: Iterable[Int,Int,Int]&SingleView[Int,Int,String]) =>
  mapIterable(filterIterable($source, fn(item: String) -> #boolean Int => $item != "0" end),
              fn(item: String) -> String => $item end)
end
selected(Count(3))
mapped(Count(3)) foreach (index, value) => break {$index, $value} end
mapped(Count(3)) foreach (index, value) => break {$index, $value} end
''', ["1", (1, "1"), (1, "1")])

    def test_std_iterable_projected_object_items_keep_exactly_one_owner(self):
        source = r'''
import {std.iterable.[Iterable, SingleView, mapIterable, filterIterable]}
object Token => $label: String end
object Holder => $token: Token end
object Holder as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int,Int,Int}? =>
    if ($state == 0) => Some({0,10,1}) else => \None end
  end
end
object Holder as SingleView[Int,Int,Token] =>
  define single(index: Int, value: Int) -> Token => $self.token
end
mapIterable(Holder(Token("mapped")), fn(token:Token)->Token=>$token end)
  foreach (item) => break $item end
filterIterable(Holder(Token("filtered")), fn(token:Token)->#boolean Int=>$token.label=="filtered" end)
  foreach (item) => break $item end
'''
        typed = self.analyse(source)
        restored = loads_interface(dumps_interface(tuple(typed)))
        for optimize in (False, True):
            for nodes in (typed, restored):
                compiled = compile_program(nodes, optimize=optimize)
                for serialized in (False, True):
                    with self.subTest(optimize=optimize, interface=nodes is restored,
                                      serialized=serialized):
                        values = run(loads(dumps(compiled)) if serialized else compiled)
                        self.assertEqual([item.fields["label"] for item in values],
                                         ["mapped", "filtered"])
                        for item in values:
                            self.assertIsInstance(item, ObjectValue)
                            self.assertNotIsInstance(item, BorrowedValue)
                            self.assertFalse(item.destroyed)
                            self.assertEqual(item.refcount, 1)

    def test_std_iterable_projected_factory_requires_matching_trait_evidence(self):
        self.assert_rejected(r'''
import {std.iterable.[Iterable, SingleView, mapIterable]}
object Mismatch => $limit: Int end
object Mismatch as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end
object Mismatch as SingleView[String, Int, String] =>
  define single(index: String, value: Int) -> String => $index
end
mapIterable(Mismatch(3), fn(item: String) -> Int => 1 end)
''', "mapIterable")

    def test_std_iterable_fold_unary_indexed_empty_and_lazy_composition(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, mapIterable, filterIterable, foldIterable]}
object Count => $limit: Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int) -> {Int,Int,Int}? =>
    if ($state < $self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
foldIterable(Count(3), 0, fn(acc:Int,value:Int)->Int => $acc+$value end)
foldIterable(Count(3), 0, fn(acc:Int,index:Int,value:Int)->Int => $acc+$index end)
foldIterable(Count(0), 42, fn(acc:Int,value:Int)->Int => $acc+$value end)
foldIterable(filterIterable(Count(4), fn(index:Int,value:Int)->#boolean Int => $index>=2 end),
  0, fn(acc:Int,index:Int,value:Int)->Int => $acc+($index*100)+$value end)
foldIterable(mapIterable(Count(3), fn(index:Int,value:Int)->Int => $value+$index end),
  0, fn(acc:Int,value:Int)->Int => $acc+$value end)
foldIterable(Count(3), 0, fn(acc:Int,value:Int)->Int => $acc+$value end)
''', [33, 3, 42, 525, 36, 33])

    def test_std_iterable_fold_supports_noninteger_indices(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, foldIterable]}
object Named => $limit:Int end
object Named as Iterable[String,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int) -> {String,Int,Int}? =>
    if ($state < $self.limit) => Some({"k"+($state to[String]),$state+5,$state+1})
    else => \None end
  end
end
foldIterable(Named(3), "", fn(acc:String,key:String,value:Int)->String => $acc+$key end)
foldIterable(Named(3), 0, fn(acc:Int,value:Int)->Int => $acc+$value end)
''', ["k0k1k2", 18])

    def test_std_iterable_fold_uses_explicit_single_view_evidence(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, SingleView, foldIterable]}
object Count => $limit:Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int) -> {Int,Int,Int}? =>
    if ($state < $self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
object Count as SingleView[Int,Int,Int] =>
  define single(index:Int,value:Int) -> Int => $index+100
end
define onlyValues(source: Iterable[Int,Int,Int]) -> Int =>
  foldIterable($source, 0, fn(acc:Int,value:Int)->Int => $acc+$value end)
end
foldIterable(Count(3), 0, fn(acc:Int,item:Int)->Int => $acc+$item end)
foldIterable(Count(3), 0, fn(acc:Int,index:Int,value:Int)->Int => $acc+$value+$index end)
onlyValues(Count(3))
''', [303, 36, 33])
        self.assert_executes(r'''
import {std.iterable.[Iterable, SingleView, filterIterable, foldIterable]}
object Count => $limit:Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int) -> {Int,Int,Int}? =>
    if ($state < $self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
object Count as SingleView[Int,Int,String] =>
  define single(index:Int,value:Int) -> String => $index to[String]
end
$filtered = filterIterable(Count(4), fn(item:String)->#boolean Int => $item != "0" end)
foldIterable($filtered, "", fn(acc:String,item:String)->String => $acc+$item end)
foldIterable(Count(4), 0, fn(acc:Int,value:Int)->Int => $acc+$value end)
''', ["123", 46])

    def test_std_iterable_fold_rejects_unmatched_source_and_callback(self):
        self.assert_rejected(r'''
import {std.iterable.[Iterable, foldIterable]}
object Unrelated => $value:Int end
foldIterable(Unrelated(5), 0, fn(acc:Int,value:Int)->Int => $acc+$value end)
''', "foldIterable")
        self.assert_rejected(r'''
import {std.iterable.[Iterable, foldIterable]}
object Count => $limit:Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int) -> {Int,Int,Int}? => \None
end
foldIterable(Count(5), 0, fn(acc:Int,value:String)->Int => $acc end)
''', "foldIterable")

    def test_std_iterable_fold_object_accumulator_retains_one_owner(self):
        source = r'''
import {std.iterable.[Iterable, foldIterable]}
object Count => $limit:Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int) -> {Int,Int,Int}? =>
    if ($state < $self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
object Token => $label:String end
foldIterable(Count(3), Token("same"), fn(acc:Token,value:Int)->Token => $acc end)
foldIterable(Count(2), Token("changed"), fn(acc:Token,value:Int)->Token => Token($acc.label+"!") end)
foldIterable(Count(0), Token("empty"), fn(acc:Token,value:Int)->Token => Token("unexpected") end)
'''
        typed = self.analyse(source)
        restored = loads_interface(dumps_interface(tuple(typed)))
        for optimize in (False, True):
            for nodes in (typed, restored):
                program = compile_program(nodes, optimize=optimize)
                for serialized in (False, True):
                    with self.subTest(optimize=optimize, interface=nodes is restored,
                                      serialized=serialized):
                        values = run(loads(dumps(program)) if serialized else program)
                        self.assertEqual([value.fields["label"] for value in values],
                                         ["same", "changed!!", "empty"])
                        for value in values:
                            self.assertIsInstance(value, ObjectValue)
                            self.assertNotIsInstance(value, BorrowedValue)
                            self.assertFalse(value.destroyed)
                            self.assertEqual(value.refcount, 1)

    def test_namespaced_projected_overloads_win_when_item_equals_value_type(self):
        self.assert_executes(r'''
import {std.iterable as it}
object Count => $limit:Int end
object Count as it.Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
object Count as it.SingleView[Int,Int,Int] =>
  define single(index:Int,value:Int)->Int => $index+100
end
it.mapIterable(Count(3),fn(item:Int)->Int => $item end)
  foreach (item) => break $item end
it.filterIterable(Count(3),fn(item:Int)->#boolean Int => $item>100 end)
  foreach (item) => break $item end
it.foldIterable(Count(3),0,fn(total:Int,item:Int)->Int => $total+$item end)
it.reduceIterable(Count(3),fn(acc:Int,item:Int)->Int => $acc+$item end) ?!
''', [100, 101, 303, 303])

    def test_std_iterable_reduce_empty_single_indexed_and_composed(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, mapIterable, filterIterable, reduceIterable]}
object Count => $limit:Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
reduceIterable(Count(0),fn(acc:Int,value:Int)->Int => $acc+$value end)
reduceIterable(Count(1),fn(acc:Int,value:Int)->Int => $acc+$value end) ?!
reduceIterable(Count(3),fn(acc:Int,value:Int)->Int => $acc+$value end) ?!
reduceIterable(Count(3),fn(acc:Int,index:Int,value:Int)->Int => $acc+$index+$value end) ?!
reduceIterable(filterIterable(Count(4),fn(index:Int,value:Int)->#boolean Int => $index>=2 end),
  fn(acc:Int,value:Int)->Int => $acc+$value end) ?!
reduceIterable(mapIterable(Count(3),fn(index:Int,value:Int)->Int => $value+$index end),
  fn(acc:Int,value:Int)->Int => $acc+$value end) ?!
reduceIterable(Count(2),fn(acc:Int,value:Int)->Int => $acc+$value end) match =>
  as :Some(value) => $value
  as :None => -1
end
''', [ObjectValue("None", {}), 10, 33, 36, 25, 36, 21])

    def test_std_iterable_reduce_preserves_indices_and_projected_evidence(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, SingleView, reduceIterable]}
object Keyed => $count:Int end
object Keyed as Iterable[String,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int) -> {String,Int,Int}? =>
    if ($state<$self.count) =>
      Some({"k"+($state to[String]), $state+10, $state+1})
    else => \None end
  end
end
reduceIterable(Keyed(3), fn(acc:Int,index:String,value:Int)->Int =>
  $acc+$value
end) ?!
''', [33])
        self.assert_executes(r'''
import {std.iterable as it}
object Count => $limit:Int end
object Count as it.Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? =>
    if ($state<$self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
object Count as it.SingleView[Int,Int,Int] =>
  define single(index:Int,value:Int)->Int => $index+100
end
define plain(source:it.Iterable[Int,Int,Int])->Int? =>
  it.reduceIterable($source, fn(acc:Int,value:Int)->Int => $acc+$value end)
end
it.reduceIterable(Count(3), fn(acc:Int,item:Int)->Int => $acc+$item end) ?!
it.reduceIterable(Count(3), fn(acc:Int,index:Int,value:Int)->Int => $acc+$index+$value end) ?!
plain(Count(3)) ?!
''', [303, 36, 33])

    def test_std_iterable_reduce_rejects_invalid_source_or_callback(self):
        self.assert_rejected(r'''
import {std.iterable.[Iterable, reduceIterable]}
object Data => $value:Int end
reduceIterable(Data(3), fn(acc:Int,value:Int)->Int => $acc+$value end)
''', "reduceIterable")
        self.assert_rejected(r'''
import {std.iterable.[Iterable, reduceIterable]}
object Count => $limit:Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Int,Int}? => \None
end
reduceIterable(Count(3), fn(acc:Int,value:String)->Int => $acc end)
''', "reduceIterable")

    def test_std_iterable_reduce_returns_owned_object_accumulator(self):
        source = r'''
import {std.iterable.[Iterable, reduceIterable]}
object Token => $label:String end
object Count => $limit:Int end
object Count as Iterable[Int,Token,Int] =>
  define initial -> Int => 0
  define advance(state:Int)->{Int,Token,Int}? =>
    if ($state<$self.limit) => Some({$state,Token($state to[String]),$state+1})
    else => \None end
  end
end
reduceIterable(Count(3),fn(acc:Token,value:Token)->Token => $acc end)
reduceIterable(Count(2),fn(acc:Token,value:Token)->Token => Token($acc.label+$value.label) end)
'''
        typed = self.analyse(source)
        restored = loads_interface(dumps_interface(tuple(typed)))
        for optimize in (False, True):
            for nodes in (typed, restored):
                program = compile_program(nodes, optimize=optimize)
                for serialized in (False, True):
                    with self.subTest(optimize=optimize, interface=nodes is restored,
                                      serialized=serialized):
                        results = run(loads(dumps(program)) if serialized else program)
                        self.assertEqual([item.fields["value"].fields["label"]
                                          for item in results], ["0", "01"])
                        for maybe in results:
                            token = maybe.fields["value"]
                            self.assertIsInstance(token, ObjectValue)
                            self.assertNotIsInstance(token, BorrowedValue)
                            self.assertFalse(token.destroyed)
                            self.assertEqual(token.refcount, 1)

    def test_std_iterable_exports_do_not_shadow_native_list_overloads(self):
        self.assert_executes(r'''
import {std.iterable.[Iterable, mapIterable, filterIterable]}
$mapped = [1, 2, 3] map: double
$mapped foreach (value) => break $value end
$filtered = [1, 2, 3] filter: fn(value: Int) -> #boolean Int => $value > 1 end
$filtered foreach (value) => break $value end
''', [2, 2])

    def test_constructor_retains_borrowed_object_field_across_return(self):
        source = r'''
object Item => $label: String end
object Envelope => $item: Item end
define wrap(item: Item) -> Envelope => Envelope($item) end
wrap(Item("live"))
'''
        typed = self.analyse(source)
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            for serialized in (False, True):
                with self.subTest(optimize=optimize, serialized=serialized):
                    executable = loads(dumps(program)) if serialized else program
                    envelope, = run(executable)
                    item = envelope.fields["item"]
                    self.assertIsInstance(item, ObjectValue)
                    self.assertNotIsInstance(item, BorrowedValue)
                    self.assertEqual(item.fields["label"], "live")
                    self.assertFalse(item.destroyed)
                    self.assertEqual(item.refcount, 1)

    def test_std_iterable_rejects_non_iterable_source(self):
        self.assert_rejected(r'''
import {std.iterable.[Iterable, mapIterable]}
object NotIterable => $value: Int end
mapIterable(NotIterable(1), fn(value: Int) -> Int => $value + 1 end)
''', "mapIterable")

    def test_iterable_and_single_view_protocol_signatures_are_valid(self):
        # Protocol methods are ordinary statically checked trait methods.
        # foreach integration is a separate capability, not inferred from names.
        self.assert_executes(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
trait[I, V, Item] SingleView =>
  extend single(index: I, value: V) -> Item
end
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None
    end
  end
end
object Count as SingleView[Int, Int, String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end
Count(3) initial
Count(3) advance(0) ?!
Count(3) single(2, 12)
''', [0, (0, 10, 1), "2"])

    def test_iterable_cursor_type_and_noninteger_index_are_independent(self):
        self.assert_executes(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
trait[I, V, Item] SingleView =>
  extend single(index: I, value: V) -> Item
end
object Keyed => $seed: Int end
object Keyed as Iterable[String, Int, Int] =>
  define initial -> Int => $self.seed
  define advance(state: Int) -> {String, Int, Int}? =>
    if ($state == 0) => Some({"key", 8, 1})
    else => \None
    end
  end
end
object Keyed as SingleView[String, Int, String] =>
  define single(index: String, value: Int) -> String => $index
end
Keyed(0) initial
Keyed(0) advance(0) ?!
Keyed(0) single("hello", 7)
''''', [0, ("key", 8, 1), "hello"])

    def test_iterable_protocol_rejects_incompatible_step_signature(self):
        self.assert_rejected(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
object Bad => end
object Bad as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> String => "invalid"
end
''''', "does not satisfy required signature")

    def test_custom_iterable_foreach_unary_binary_projection_and_empty(self):
        self.assert_executes(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
trait[I, V, Item] SingleView =>
  extend single(index: I, value: V) -> Item
end
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None
    end
  end
end
object Count as SingleView[Int, Int, String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end
Count(3) foreach (index, value) => break {$index, $value} end
Count(3) foreach (item) => break $item end
Count(0) foreach (item) => $item end
Count(0) foreach (index, value) => $value end
''', [(0, 10), "0", ObjectValue("None", {})])

    def test_custom_iterable_index_need_not_be_the_cursor_type(self):
        self.assert_executes(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
object Keyed => $seed: Int end
object Keyed as Iterable[String, Int, Int] =>
  define initial -> Int => $self.seed
  define advance(state: Int) -> {String, Int, Int}? =>
    if ($state == 0) => Some({"hello", 15, 1})
    else => \None
    end
  end
end
Keyed(0) foreach (index, value) => break {$index, $value} end
Keyed(0) foreach (item) => break $item end
''', [("hello", 15), 15])

    def test_trait_typed_foreach_does_not_infer_unstated_single_view(self):
        self.assert_executes(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
trait[I, V, Item] SingleView =>
  extend single(index: I, value: V) -> Item
end
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None
    end
  end
end
object Count as SingleView[Int, Int, String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end
define view(thing: Iterable[Int, Int, Int]) =>
  $thing foreach (item) => break $item end
end
define keys(thing: Iterable[Int, Int, Int]) =>
  $thing foreach (index, value) => break {$index, $value} end
end
define[I, V, S] first(thing: Iterable[I, V, S]) =>
  $thing foreach (index, value) => break $value end
end
define projected(thing: Iterable[Int, Int, Int]&SingleView[Int, Int, String]) =>
  $thing foreach (item) => break $item end
end
view(Count(2))
keys(Count(2))
first(Count(2))
projected(Count(2))
Count(2) foreach (item) => break $item end
''', [10, (0, 10), 10, "0", "0"])

    def test_custom_iterable_break_return_and_nested_loop(self):
        self.assert_executes(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None
    end
  end
end
define find(n: Int) =>
  Count($n) foreach (index, value) =>
    if ($index == 1) => return $value end
  end
  0
end
find(3)
$total = 0
Count(2) foreach (index, value) =>
  Count(2) foreach (other, data) =>
    $total := $total + $value + $data
  end
end
$total
''', [11, 84])

    def test_object_cursor_state_and_returned_item_are_owned(self):
        source = r'''
trait[I,V,S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I,V,S}?
end
object Cursor => $pos: Int end
object Item => $label: String end
object Items => $count: Int end
object Items as Iterable[String, Item, Cursor] =>
  define initial -> Cursor => Cursor(0)
  define advance(state: Cursor) -> {String, Item, Cursor}? =>
    if ($state.pos < $self.count) =>
      Some({"key", Item("alive"), Cursor($state.pos + 1)})
    else => \None
    end
  end
end
Items(3) foreach (index, value) => break $value end
'''
        typed = self.analyse(source)
        for optimize in (False, True):
            for serialized in (False, True):
                with self.subTest(optimize=optimize, serialized=serialized):
                    program = compile_program(typed, optimize=optimize)
                    result = run(loads(dumps(program)) if serialized else program)
                    self.assertEqual(result, [ObjectValue("Item", {"label": "alive"})])
                    self.assertFalse(result[0].destroyed)
                    self.assertEqual(result[0].refcount, 1)

    def test_iterable_advance_panic_is_propagated(self):
        source = r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S)<Panic[RuntimeFault]> -> {I, V, S}?
end
object Fails => $n: Int end
object Fails as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int)<Panic[RuntimeFault]> -> {Int, Int, Int}? =>
    if ($state < 1) => Some({$state, $state + 10, $state + 1})
    else => RuntimeFault("iteration fault") panic
    end
  end
end
Fails(3) foreach (item) => $item end
'''
        typed = self.analyse(source)
        for optimize in (False, True):
            for serialized in (False, True):
                with self.subTest(optimize=optimize, serialized=serialized):
                    program = compile_program(typed, optimize=optimize)
                    executable = loads(dumps(program)) if serialized else program
                    with self.assertRaisesRegex(ValianceRuntimeError, "iteration fault"):
                        run(executable)

    def test_single_view_does_not_make_an_object_iterable(self):
        self.assert_rejected(r'''
trait[I, V, Item] SingleView =>
  extend single(index: I, value: V) -> Item
end
object Lonely => $seed: Int end
object Lonely as SingleView[Int, Int, Int] =>
  define single(index: Int, value: Int) -> Int => $value
end
Lonely(0) foreach (item) => $item end
''', "must actually be iterable")

    def test_incompatible_single_view_rejected_for_unary_iteration(self):
        self.assert_rejected(r'''
trait[I, V, S] Iterable =>
  extend initial -> S
  extend advance(state: S) -> {I, V, S}?
end
trait[I, V, Item] SingleView =>
  extend single(index: I, value: V) -> Item
end
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? => \None
end
object Count as SingleView[String, Int, String] =>
  define single(index: String, value: Int) -> String => $index
end
Count(2) foreach (item) => $item end
''', "incompatible or ambiguous SingleView")

    def test_variant_implementation_target_diagnostic(self):
        for target in ("Entry", "Entry[Int]"):
            with self.subTest(target=target):
                source = (
                    "variant[T] Entry => File => end end\n"
                    "object \\NotAFile => end\n"
                    f"object \\NotAFile as {target} => end\n"
                )
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertIn(
                    "3:21: cannot implement 'Entry': it is a variant, not a trait",
                    analyser.diagnostics,
                )

    def test_invalid_implementation_target_location(self):
        analyser = Analyser()
        analyser.analyse(parse(
            "object \\Item => end\nobject \\Item as Missing => end\n"
        ))
        self.assertIn(
            "2:17: trait implementation target must be a named trait",
            analyser.diagnostics,
        )

    def test_multiple_requirements_and_receiver_arguments(self):
        self.assert_executes('''
trait Counter =>
  extend count -> Int
  extend plus(:Int) -> Int
end
object Item => $value: Int end
object Item as Counter =>
  define count => $self.value
  define plus(n: Int) => $self.value + $n
end
define total(item: Counter) -> Int => ($item count) + (5 | plus $item) end
Item(7) total
''', [19])

    def test_default_can_be_overridden_per_object(self):
        self.assert_executes('''
trait Named => define label -> String => "default" end
object First => $id: Int end
object Second => $id: Int end
object First as Named => end
object Second as Named => define label -> String => "override" end
First(1) label
Second(2) label
First(3) label
''', ['default', 'override', 'default'])

    def test_inherited_requirement_dispatch(self):
        self.assert_executes('''
trait Parent => extend size -> Int end
trait Child as Parent => end
object Item => $value: Int end
object Item as Child => define size => $self.value end
define measure(item: Parent) -> Int => $item size end
Item(11) measure
''', [11])

    def test_missing_abstract_requirement_is_rejected(self):
        self.assert_rejected('''
trait Named => extend label -> String end
object Item => $id: Int end
object Item as Named => end
''', 'label')

    def test_inherited_default_calls_inherited_requirement(self):
        self.assert_executes('''
trait Parent =>
  extend size -> Int
  define doubled -> Int => ($self size) * 2
end
trait Child as Parent => end
object Item => $value: Int end
object Item as Child => define size => $self.value end
Item(6) doubled
''', [12])

    def test_requirement_signature_rejections(self):
        for implementation in (
            'define convert(value: String) -> Int => 1',
            'define convert(value: Int) -> String => "wrong"',
            'define convert -> Int => 1',
        ):
            with self.subTest(implementation=implementation):
                self.assert_rejected(
                    'trait Converter => extend convert(:Int) -> Int end\n'
                    'object Item => end\n'
                    f'object Item as Converter => {implementation} end',
                    'convert',
                )

    def test_nominal_trait_requires_explicit_implementation(self):
        self.assert_rejected('''
trait Named => extend name -> String end
object Item =>
  $label: String
  public define name => $self.label
end
define accept(item: Named) => $item name end
Item("item") accept
''', 'accept')

    def test_implementation_can_reuse_object_method(self):
        self.assert_executes('''
trait Named => extend name -> String end
object Item =>
  $label: String
  public define name => $self.label
end
object Item as Named => end
define describe(item: Named) => $item name end
Item("item") describe
''', ['item'])

    def test_default_calls_abstract_requirement(self):
        self.assert_executes('''
trait Sized =>
  extend size -> Int
  define doubled -> Int => ($self size) * 2
end
object Item => $value: Int end
object Item as Sized => define size => $self.value end
Item(7) doubled
''', [14])

    def test_generic_trait_keeps_independent_instantiations(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
object[T] Box => $value: T end
object[T] Box as Source[T] => define read -> T => $self.value end
define readInt(item: Source[Int]) -> Int => $item read end
define readText(item: Source[String]) -> String => $item read end
Box(7) readInt
Box("text") readText
Box(9) readInt
''', [7, 'text', 9])

    def test_generic_trait_rejects_wrong_instantiation(self):
        self.assert_rejected('''
trait[T] Source => extend read -> T end
object Box => $value: Int end
object Box as Source[Int] => define read => $self.value end
define accept(item: Source[String]) => $item read end
Box(7) accept
''', 'accept')

    def test_concrete_object_implements_exact_generic_trait(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
trait[T] Reader => extend first -> T end
trait[T] Source as Reader[T] => define first -> T => $self read end
object Box => $value: Int end
object Box as Source[Int] => define read => $self.value end
define accept(item: Source[Int]) -> Int => $item read end
Box(7) accept
Box(8) first
''', [7, 8])

    def test_concrete_generic_trait_implementation_respects_variance(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
object Box => $value: Int end
object Box as Source[Int] => define read => $self.value end
define accept(item: Source[Number]) -> Number => $item read end
Box(7) accept
''', [7])

    def test_transitive_trait_behaviour_preserves_generic_result(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
trait[T] Reader => extend first -> T end
trait[T] Source as Reader[T] => define first -> T => $self read end
object[T] Box => $value: T end
object[T] Box as Source[T] => define read -> T => $self.value end
Box(8) first
Box("eight") first
''', [8, 'eight'])

    def test_trait_union_requires_every_alternative_to_support_call(self):
        self.assert_rejected('''
trait Sized => extend size -> Int end
object Item => end
object Item as Sized => define size => 1 end
define invalid(item: Sized | Int) => $item size end
''', 'size')

    def test_anonymous_trait_accepts_structural_method(self):
        self.assert_executes('''
object Item => $id: Int
  define size(item: $self) -> Int => 12 end
define measure(item: trait => extend size -> Int end) => $item size end
Item(0) measure
''', [12])

    def test_anonymous_trait_rejects_incompatible_method(self):
        self.assert_rejected('''
object Item => $id: Int
  define size(item: $self) -> String => "wrong" end
define measure(item: trait => extend size -> Int end) => $item size end
Item(0) measure
''', 'measure')


MAYBE = '''
variant[T] Maybe =>
  Some => $value: T end
  Empty => end
end
'''


class VariantSemanticTests(SemanticTestCase):
    def test_variant_case_names_can_overlap_with_incompatible_constructor_arities(self):
        source = r'''variant Message =>
  Conflict => $text: String end
end
variant Status =>
  Conflict
end
Message.Conflict("details")
Status.\Conflict'''
        typed = self.analyse(source)
        for optimized in (False, True):
            for serialized in (False, True):
                with self.subTest(optimized=optimized, serialized=serialized):
                    program = compile_program(typed, optimize=optimized)
                    result = run(loads(dumps(program)) if serialized else program)
                    self.assertEqual([value.type_name for value in result],
                                     ['Message.Conflict', 'Status.Conflict'])

    def test_vectorised_case_constructor_preserves_trait_dispatch(self):
        self.assert_executes('''
trait Stringable => extend toString -> String end
variant Entry =>
  Shortcut =>
    $name: String
    $target: String
  end
  Shortcut as Stringable =>
    define toString => "${$self.name} -> ${$self.target}"
  end
end
define display(:Stringable) -> String => toString end
["s1", "s2"]
Shortcut(_, "file.txt")
display
Shortcut(["s3", "s4"], "file.txt")
display
''', [['s1 -> file.txt', 's2 -> file.txt'], ['s3 -> file.txt', 's4 -> file.txt']])

    def test_short_case_type_preserves_identity_through_list_updates(self):
        self.assert_executes('''
trait Stringable => extend toString -> String end
variant Entry =>
  Shortcut =>
    $name: String
    $target: String
  end
  Shortcut as Stringable =>
    define toString => "Shortcut(${$self.name}) -> ${$self.target}"
  end
end
define first(values: Shortcut+) -> Entry.Shortcut => $values $[0] end
define display(:Stringable) -> String => toString end
$shortcuts: Shortcut+ = []
["s1", "s2"] foreach (name) =>
  $shortcuts := append Shortcut($name, "file.txt")
end
display(first($shortcuts))
display $shortcuts
''', ['Shortcut(s1) -> file.txt', ['Shortcut(s1) -> file.txt', 'Shortcut(s2) -> file.txt']])

    def test_case_constructors_pass_directly_to_trait_parameters(self):
        self.assert_executes(r'''
trait Drawable => extend draw -> String end
variant Colour =>
  RED
  RED as Drawable => define draw => "red" end
  BLUE
end
variant Shape =>
  Circle => $radius: Int end
  Circle as Drawable => define draw => "circle" end
end
define render(value: Drawable) -> String => $value draw end
render(Colour.\RED)
render(Shape.Circle(8))
''', ['red', 'circle'])

    def test_nested_case_trait_implementation_inherits_variant_generics(self):
        self.assert_executes('''
trait[T] Drawable => extend draw -> T end
variant[T] Shape =>
  Circle => $radius: T end
  Circle as Drawable[T] => define draw => $self.radius end
  Empty
end
define render(value: Drawable[Int]) -> Int => $value draw end
Shape.Circle(8) match =>
  as :Circle => render
  as :Empty => 0
end
''', [8])

    def test_nested_case_trait_implementation_checks_requirements(self):
        self.assert_rejected('''
trait Drawable => extend draw -> Int end
variant Shape => Circle Circle as Drawable => end end
''', 'does not satisfy required element')

    def test_nested_case_trait_implementation_resolves_short_case_types(self):
        self.assert_executes('''
trait[T] Combine => extend combine(other: T) -> Int end
variant First =>
  Item => $value: Int end
  Item as Combine[Item] =>
    define combine(other: Item) => $self.value + $other.value
  end
end
variant Second =>
  Item => $value: Int end
  Item as Combine[Item] =>
    define combine(other: Item) => $self.value * $other.value
  end
end
define[T] combined(item: Combine[T], other: T) -> Int => $other $item | combine end
combined(First.Item(3), First.Item(4))
combined(Second.Item(3), Second.Item(4))
''', [7, 12])

    def test_ambiguous_case_type_requires_qualification_outside_implementation(self):
        self.assert_rejected('''
trait Measurable => extend measure -> Int end
variant Shape =>
  Circle => $radius: Int end
  Circle as Measurable => define measure => $self.radius end
end
variant Outline => Circle => $radius: Int end end
define outside(circle: Circle) -> Int => 0
''', "undefined type 'Circle'")

    def test_bare_and_explicit_empty_cases_have_the_same_value_behavior(self):
        self.assert_executes(r'''
variant Colour =>
  RED
  GREEN => end
end
Colour.\RED Colour.\RED ===
Colour.\RED Colour.\GREEN ===
dict{Colour.\RED => 7, Colour.\GREEN => 9} $[Colour.\RED]
''', [1, 0, 7])

    def test_inline_bare_cases_are_exhaustive_objects(self):
        self.assert_executes(r'''
variant Colour => RED GREEN BLUE end
define code(value: Colour) -> Int =>
  $value match =>
    as :RED => 1
    as :GREEN => 2
    as :BLUE => 3
  end
end
Colour.\GREEN code
''', [2])

    def test_backing_values_are_readable_nominal_object_fields(self):
        self.assert_executes(r'''
variant Token =>
  NUMBER = "number"
  DIGIT = "number"
  SYMBOL = "+"
end
Token.\NUMBER.value
Token.\NUMBER Token.\DIGIT ===
dict{Token.\NUMBER => 7, Token.\DIGIT => 9} $[Token.\DIGIT]
''', ['number', 0, 9])

    def test_all_value_backed_variant_members_expose_projected_values(self):
        self.assert_executes(r'''
variant Colour =>
  RED = "red"
  BLUE = "blue"
  GREEN = "green"
end
const $myCols = [Colour.\RED, Colour.\BLUE, Colour.\GREEN]
$myCols.value
define readColour(colour: Colour) -> String => $colour.value end
Colour.\BLUE readColour
''', [['red', 'blue', 'green'], 'blue'])

    def test_partially_value_backed_variant_rejects_shared_value_access(self):
        self.assert_rejected(r'''
variant Colour =>
  RED = "red"
  BLUE
  GREEN = "green"
end
const $myCols = [Colour.\RED, Colour.\BLUE, Colour.\GREEN]
$myCols.value
''', 'value')

    def test_mixed_object_and_value_cases_reject_shared_value_access(self):
        self.assert_rejected(r'''
variant Choice =>
  Literal = 1
  Object => $name: String end
end
$choices = [Choice.\Literal, Choice.Object("named")]
$choices.value
''', 'value')

    def test_variant_without_backing_values_rejects_value_access(self):
        self.assert_rejected(r'''
variant Colour => RED BLUE GREEN end
$colours = [Colour.\RED, Colour.\BLUE, Colour.\GREEN]
$colours.value
''', 'value')

    def test_backing_value_cases_support_friendly_implementations(self):
        self.assert_executes(r'''
variant Colour =>
  extend label -> String
  define twice -> String => ($self label) + ($self label)
  RED = "red" =>
    define label => $self.value
  end
  GREEN = "green" =>
    define label => $self.value
  end
end
Colour.\RED twice
''', ['redred'])

    def test_bare_cases_mix_with_generic_payload_objects(self):
        self.assert_executes(r'''
variant[T] Choice =>
  Empty
  Item => $value: T end
end
define unwrap(value: Choice[Int]) -> Int =>
  $value match =>
    as :Empty => 0
    as :Item(item) => $item
  end
end
Choice.\Empty[Int] unwrap
Item(8) unwrap
dict{Item(3) => 9} $[Item(3)]
''', [0, 8, 9])

    def test_bare_cases_can_implement_additional_traits(self):
        self.assert_executes(r'''
trait Named => extend label -> String end
variant Colour => RED GREEN end
object Colour.RED as Named => define label => "red" end
define read(value: Named) -> String => $value label end
Colour.\RED match =>
  as :RED => read
  as :GREEN => "green"
end
''', ['red'])

    def test_backing_value_cannot_be_supplied_as_a_constructor_argument(self):
        self.assert_rejected('''
variant Token => NUMBER = "number" end
Token.NUMBER("other")
''', 'no overloads')

    def test_backing_value_cases_can_store_additional_payload_fields(self):
        self.assert_executes(r'''
variant Code =>
  Entry = 1 => $message: String end
end
$entry = Code.Entry("hello")
$entry.value
$entry.message
dict{Code.Entry("hello") => 7} $[Code.Entry("hello")]
''', [1, 'hello', 7])

    def test_tagged_object_payloads_preserve_dictionary_key_equality(self):
        self.assert_executes('''
variant Entry => Item => $value: #boolean Int end end
dict{Item(true) => 7} $[Item(true)]
''', [7])

    def test_parent_field_access_requires_the_field_on_every_case(self):
        self.assert_rejected(r'''
variant Choice =>
  Empty
  Item => $value: Int end
end
Choice.\Empty.value
''', 'value')

    def test_backing_value_is_read_only(self):
        self.assert_rejected(r'''
variant Token => NUMBER = "number" end
$token = Token.\NUMBER
$token.value = "other"
''', 'value')

    def test_bare_cases_still_require_exhaustive_matching(self):
        self.assert_rejected('''
variant Choice => First Second end
define choose(value: Choice) -> Int =>
  $value match => as :First => 1 end
end
''', 'exhaustive')

    def test_niladic_member_constructors(self):
        self.assert_executes(r'''
variant Colour =>
  \RED => end
  \GREEN => end
  \BLUE => end
end
define code(value: Colour) -> Int => 1 end
\RED code
\GREEN code
\BLUE code
''', [1, 1, 1])

    def test_nullary_and_payload_members_are_both_executed(self):
        self.assert_executes('''
variant Choice =>
  Present => $value: Int end
  Absent => end
end
define unwrap(value: Choice) -> Int =>
  $value match =>
    as :Present(item) => $item
    as :Absent => 0
  end
end
Present(8) unwrap
Absent
unwrap
''', [8, 0])

    def test_match_bindings_do_not_escape_case(self):
        self.assert_rejected(MAYBE + '''
Some(1) match =>
  as :Some(item) => $item
  as :Empty => 0
end
$item
''', 'item')

    def test_payload_field_order_is_preserved(self):
        self.assert_executes('''
variant Entry =>
  Pair =>
    $first: Int
    $second: String
  end
end
Pair(7, "seven") match =>
  as :Pair(number, text) => $number $text
end
''', [7, 'seven'])

    def test_distinct_variants_are_not_interchangeable(self):
        self.assert_rejected('''
variant Left => First => $value: Int end end
variant Right => Second => $value: Int end end
define accept(value: Left) => $value end
Second(1) accept
''', 'accept')

    def test_generic_constructor_runtime_evidence(self):
        typed = self.analyse(MAYBE + 'Some(1)\nSome("text")')
        for optimize in (False, True):
            with self.subTest(optimize=optimize):
                program = compile_program(typed, optimize=optimize)
                for candidate in (program, loads(dumps(program))):
                    values = run(candidate)
                    self.assertEqual([value.type_name for value in values],
                                     ['Maybe.Some', 'Maybe.Some'])
                    self.assertEqual([value.type_args for value in values],
                                     [('Int',), ('String',)])

    def test_constructor_inference_keeps_independent_arguments(self):
        for expression, expected in (('Some(1)', 'Maybe.Some[Int]'),
                                     ('Some("text")', 'Maybe.Some[String]'),
                                     ('Some([1, 2])', 'Maybe.Some[Int+]')):
            with self.subTest(expression=expression):
                typed = self.analyse(MAYBE + expression)
                self.assertEqual(T.show(typed[-1].typ), expected)

    def test_qualified_and_short_constructors_agree(self):
        typed = self.analyse(MAYBE + 'Some(4)\nMaybe.Some(9)')
        self.assertEqual(T.show(typed[-1].typ), 'Maybe.Some[Int]')
        values = run(loads(dumps(compile_program(typed))))
        self.assertEqual([value.fields['value'] for value in values], [4, 9])
        self.assertEqual([value.type_args for value in values], [('Int',), ('Int',)])

    def test_generic_match_specializes_payload_type(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int]) -> Int =>
  $value match =>
    as :Some(item) => $item
    as :Empty => 0
  end
end
Some(4) unwrap
Maybe.Some(9) unwrap
''', [4, 9])

    def test_constructor_argument_type_and_arity(self):
        declaration = 'variant Choice => Item => $value: Int end end\n'
        for expression in ('Item("wrong")', 'Item(1, 2)'):
            with self.subTest(expression=expression):
                self.assert_rejected(declaration + expression, 'Item')

    SHAPES = '''
variant Shape =>
  extend area -> Int
  Square =>
    $side: Int
    define area => $self.side * $self.side
  end
  Rectangle =>
    $width: Int
    $height: Int
    define area => $self.width * $self.height
  end
end
'''

    def test_all_members_dispatch_through_parent_interface(self):
        self.assert_executes(self.SHAPES + '''
define measure(shape: Shape) -> Int => $shape area end
Square(3) measure
Rectangle(4, 5) measure
''', [9, 20])

    def test_variant_default_calls_abstract_method_and_supports_overrides(self):
        self.assert_executes('''
variant Shape =>
  extend area -> Int
  define doubled -> Int => ($self area) * 2
  define label -> String => "shape"
  Square =>
    $side: Int
    define area => $self.side * $self.side
  end
  Rectangle =>
    $width: Int
    $height: Int
    define area => $self.width * $self.height
    define label -> String => "rectangle"
  end
end
define describe(shape: Shape) -> String => $shape label end
Square(3) doubled
Rectangle(2, 3) doubled
Square(2) label
Rectangle(2, 3) label
Square(4) describe
Rectangle(4, 5) describe
''', [18, 12, "shape", "rectangle", "shape", "rectangle"])

    def test_variant_extend_may_supply_default_and_be_overridden(self):
        self.assert_executes('''
variant Choice =>
  extend score -> Int
  define score -> Int => 2
  First => $id: Int end
  Second =>
    $id: Int
    define score => 9
  end
end
define value(item: Choice) -> Int => $item score end
First(1) score
Second(2) score
First(3) value
Second(4) value
''', [2, 9, 2, 9])

    def test_generic_variant_default_specializes_overrides(self):
        self.assert_executes('''
variant[T] Choice =>
  define score -> Int => 3
  First => $value: T end
  Second =>
    $value: T
    define score -> Int => 5
  end
end
First(1) score
Second(2) score
Second("text") score
First("text") score
''', [3, 5, 5, 3])

    def test_generic_variant_default_parameter_and_result_types(self):
        self.assert_executes('''
variant[T] Value =>
  define fallback(value: T) -> T => $value
  First => $value: T end
  Second =>
    $value: T
    define fallback(value: T) -> T => $self.value
  end
end
First(8) | fallback 9
Second(8) | fallback 9
First("a") | fallback "b"
Second("a") | fallback "b"
''', [9, 8, "b", "a"])

    def test_generic_variant_default_can_call_abstract_method(self):
        self.assert_executes('''
variant[T] Value =>
  extend read -> T
  define keep -> T => $self read
  First =>
    $value: T
    define read => $self.value
  end
  Second =>
    $value: T
    define read => $self.value
  end
end
First(8) keep
Second("a") keep
''', [8, "a"])

    def test_variant_default_override_must_match_signature(self):
        for implementation, diagnostic in (
            ('define score(value: String) -> Int => 2', 'parameter type'),
            ('define score(value: Int) -> String => "bad"', 'return signature'),
            ('define score -> Int => 2', 'explicit parameter'),
        ):
            with self.subTest(implementation=implementation):
                self.assert_rejected('''
variant Choice =>
  define score(value: Int) -> Int => $value
  First => $id: Int end
  Second =>
    $id: Int
''' + implementation + '''
  end
end
''', diagnostic)

    def test_heterogeneous_variant_collection_dispatch(self):
        self.assert_executes(self.SHAPES + '''
$shapes: Shape+ = [Square(2), Rectangle(3, 4)]
area $shapes
''', [[4, 12]])

    def test_nested_variant_collection_dispatch(self):
        self.assert_executes(self.SHAPES + '''
$shapes: Shape++ = [[Square(2), Rectangle(3, 4)], [Rectangle(4, 5), Square(3)]]
area $shapes
''', [[[4, 12], [20, 9]]])

    def test_recursive_variant_method_dispatches_collection_fields(self):
        self.assert_executes('''
variant Entry =>
  extend storedBytes -> Int
  File =>
    $fileSize: Int
    define storedBytes => $self.fileSize
  end
  Shortcut =>
    $target: String
    define storedBytes => 0
  end
  Folder =>
    $entries: Entry+
    define storedBytes => sum storedBytes $self.entries
  end
end
Folder([File(1200), File(4500), Folder([File(8000), File(6000)]), Shortcut("essay")]) storedBytes
Folder([] as[Entry+]) storedBytes
Folder([Folder([Shortcut("essay")]), File(3)]) storedBytes
''', [19700, 0, 3])

    def test_variant_collection_dispatch_broadcasts_other_arguments(self):
        self.assert_executes('''
variant Entry =>
  extend add(:Int) -> Int
  First =>
    $value: Int
    define add(n: Int) => $self.value + $n
  end
  Second =>
    $value: Int
    define add(n: Int) => $self.value + $n + 1
  end
end
$items: Entry+ = [First(3), Second(4)]
$items | add 10
''', [[13, 15]])

    def test_vectorized_multimethod_retains_scalar_fallback(self):
        self.assert_executes(self.SHAPES + '''
define describe(shape: Shape) -> String => "fallback" end
alt define describe(shape: Shape.Square) -> String => "square" end
$shapes: Shape+ = [Square(2), Rectangle(3, 4), Square(5)]
describe $shapes
''', [['square', 'fallback', 'square']])

    def test_generic_match_preserves_nested_field_arguments(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int+]) -> Int+ =>
  $value match =>
    as :Some(items) => $items
    as :Empty => [] as[Int+]
  end
end
Some([1, 2, 3]) unwrap
''', [[1, 2, 3]])

    def test_generic_object_destructuring_specializes_fields(self):
        self.assert_executes('''
object[T] Box => $value: T end
define unwrap(value: Box[Int]) -> Int =>
  $value match =>
    as :Box(item) => $item
    as _ => 0
  end
end
Box(19) unwrap
''', [19])

    def test_generic_match_extraction_specializes_named_capture(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int]) -> Int =>
  $value match =>
    extract as :Some(item) => $item * 2
    as :Empty => 0
  end
end
Some(7) unwrap
''', [14])

    def test_generic_match_specializes_whole_member_binding(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int]) -> Int =>
  $value match =>
    as item: Some => $item.value
    as :Empty => 0
  end
end
Some(21) unwrap
''', [21])

    def test_generic_match_retains_arguments_in_subject_union(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int] | Maybe[String]) -> Int | String =>
  $value match =>
    as :Some(item) => $item
    as _ => "empty"
  end
end
Some(3) unwrap
Some("three") unwrap
''', [3, 'three'])

    def test_member_requirement_diagnostics(self):
        for body, message in (
            ('', "must implement element 'convert'"),
            ('define convert(x: Int) =>\n $x\n end\n'
             'define convert(x: Int) =>\n $x\n end',
             'exactly once'),
            ('define convert => 1', 'explicit parameter'),
            ('define convert(x: String) => 1', 'parameter type'),
            ('define convert(x: Int) -> String => "wrong"', 'return'),
        ):
            with self.subTest(body=body):
                self.assert_rejected(
                    'variant Choice =>\n  extend convert(:Int) -> Int\n'
                    f'Item =>\n{body}\nend\nend', message,
                )

    def test_match_requires_every_member(self):
        self.assert_rejected(MAYBE + '''
define unpack(value: Maybe[Int]) -> Int =>
  $value match => as :Some(value) => $value end
end
''', 'missing cases: Maybe.Empty')

    def test_guard_and_literal_pattern_do_not_prove_exhaustiveness(self):
        for pattern in ('as :Some(1)', 'as :Some(_) if 0 1 =='):
            with self.subTest(pattern=pattern):
                self.assert_rejected(MAYBE + f'''
Some(2) match =>
  {pattern} => 1
  as :Empty => 0
end
''', 'missing cases: Maybe.Some')

    def test_destructure_arity_is_checked(self):
        self.assert_rejected(MAYBE + '''
Some(1) match =>
  as :Some(_, _) => 1
  as :Empty => 0
end
''', 'destructures 2 fields')

    def test_match_payload_types_are_checked(self):
        self.assert_rejected(MAYBE + '''
define invalid(value: Maybe[String]) -> Int =>
  $value match =>
    as :Some(item) => $item
    as :Empty => 0
  end
end
''', 'return')

    def test_wildcard_handles_unmatched_payload(self):
        self.assert_executes(MAYBE + '''
Some(2) match =>
  as :Some(1) => "one"
  as _ => "other"
end
''', ['other'])

    def test_nested_generic_payload_survives_matching(self):
        self.assert_executes(MAYBE + '''
define unpack(value: Maybe[Maybe[Int]]) -> Int =>
  $value match =>
    as :Some(inner) =>
      $inner match =>
        as :Some(item) => $item
        as :Empty => -1
      end
    as :Empty => -2
  end
end
Some(Some(17)) unpack
''', [17])

    def test_trait_dispatch_inside_variant_payload_match(self):
        self.assert_executes('''
trait Sized => extend size -> Int end
object Item => $value: Int end
object Item as Sized => define size => $self.value end
''' + MAYBE + '''
define measure(value: Maybe[Sized]) -> Int =>
  $value match =>
    as :Some(item) => $item size
    as :Empty => 0
  end
end
Some(Item(23) as[Sized]) measure
''', [23])


if __name__ == '__main__':
    unittest.main()
