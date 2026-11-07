"""Bounded exhaustive project-level *interaction* matrix for Valiance.

A case composes declarations from two separately compiled modules. Its oracle
checks values flowing across distinct feature kinds, not merely the presence
of two unrelated declarations. All valid combinations in the *declared finite*
family × import-graph × artifact-layout product are enumerated before repeats.
This is not an exhaustive proof of the unbounded Valiance language.
"""

from __future__ import annotations

import argparse
from functools import cache
from dataclasses import dataclass
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import random
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any

from tools.multimodule_fuzzing import ProjectCase, write_project
from valiance.analysis import Analyser
from valiance.incremental import CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.concurrency import Receive
from valiance.runtime.runtime_values import ObjectValue


@dataclass(frozen=True)
class InteractionSpec:
    """Real type/data flow crossing two module owner boundaries."""

    features: tuple[str, ...]
    core: str
    core_exports: tuple[str, ...]
    dependent: str
    dependent_exports: tuple[str, ...]
    consumer: str
    output: tuple[Any, ...]
    stdout: str = ""


# @N@ and @LABEL@ are exclusively literal-data substitutions, never syntax
# templates generated from arbitrary user strings. Each output is an oracle
# independent of the analyser, bytecode, and VM.
SPECS: dict[str, InteractionSpec] = {
    "generic_object_variant": InteractionSpec(
        ("generic", "object", "variant", "match"),
        "public object[T] Box => public $value:T end\n", ("Box",),
        "import {core.Box}\npublic variant[T] Choice =>\n"
        " Present => public $value:Box[T] end\n Empty => end\nend\n", ("Choice",),
        "Choice.Present(Box(@N@)) match =>\n"
        " as :Present(box) => $box.value\n as :Empty => 0\nend\n", ("@N@",),
    ),
    "generic_enum_identity": InteractionSpec(
        ("generic", "element", "enum", "nominal"),
        "public variant Token =>\n YES = \"yes\"\n NO = \"no\"\nend\n", ("Token",),
        "public define[T] identity(value:T)->T=>$value end\n", ("identity",),
        "$v=identity(Token.\\YES)\n$v.value\n", ("yes",),
    ),
    "constructed_tag_generic": InteractionSpec(
        ("tag", "constructed", "generic", "element"),
        "public tag #sticky as constructed\n", ("#sticky",),
        "public define[T] identity(value:T)->T=>$value end\n", ("identity",),
        "@N@ #sticky | identity | #-sticky\n", ("@N@",),
    ),
    "enum_object_field": InteractionSpec(
        ("enum", "object", "field", "construction"),
        "public variant Token =>\n YES = \"yes\"\n NO = \"no\"\nend\n", ("Token",),
        "public object Envelope => public $code:String end\n", ("Envelope",),
        "$v=Envelope(Token.\\YES.value)\n$v.code\n", ("yes",),
    ),
    "trait_variant_dispatch": InteractionSpec(
        ("trait", "object", "variant", "match", "dispatch"),
        "public trait Sized => extend size -> Int end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Sized => define size => $self.value end\n", ("Sized", "Item"),
        "import {core.Item}\npublic variant Choice =>\n"
        " Present => public $value:Item end\n Empty => end\nend\n", ("Choice",),
        "Choice.Present(Item(@N@)) match =>\n"
        " as :Present(item) => $item as[Sized] | size\n as :Empty => 0\nend\n", ("@N@",),
    ),
    "generic_trait_variant": InteractionSpec(
        ("generic", "trait", "object", "variant", "match", "dispatch"),
        "public trait[T] Source => extend read -> T end\n"
        "public object[T] Box => public $value:T end\n"
        "object[T] Box as Source[T] => define read -> T => $self.value end\n", ("Source", "Box"),
        "import {core.Box}\npublic variant[T] Choice =>\n"
        " Present => public $value:Box[T] end\n Empty => end\nend\n", ("Choice",),
        "Choice.Present(Box(@N@)) match =>\n"
        " as :Present(box) => $box as[Source[Int]] | read\n as :Empty => 0\nend\n", ("@N@",),
    ),
    "overload_enum": InteractionSpec(
        ("overload", "enum", "element", "dispatch"),
        "public variant Token =>\n YES = \"yes\"\n NO = \"no\"\nend\n", ("Token",),
        "import {core.Token}\n"
        "public define inspect(value:Int)->Int=>$value+1 end\n"
        "public define inspect(value:Token)->String=>$value.value end\n", ("inspect",),
        "inspect(@N@)\ninspect(Token.\\YES)\n", ("@N_PLUS_ONE@", "yes"),
    ),
    "overload_generic_object": InteractionSpec(
        ("overload", "generic", "object", "element", "dispatch"),
        "public object[T] Box => public $value:T end\n", ("Box",),
        "import {core.Box}\n"
        "public define read(value:Box[Int])->Int=>$value.value end\n"
        "public define read(value:Box[String])->String=>$value.value end\n", ("read",),
        'read(Box(@N@))\nread(Box("@LABEL@"))\n', ("@N@", "@LABEL@"),
    ),
    "tag_variant_payload": InteractionSpec(
        ("tag", "computed", "variant", "match"),
        "public tag #marked as computed\n", ("#marked",),
        "public variant Choice =>\n"
        " Present => public $value:Int end\n Empty => end\nend\n", ("Choice",),
        "Choice.Present(@N@) match =>\n"
        " as :Present(value) => $value #marked | #-marked\n as :Empty => 0\nend\n", ("@N@",),
    ),
    "generic_variant_identity": InteractionSpec(
        ("generic", "variant", "match", "element"),
        "public variant[T] Choice =>\n"
        " Present => public $value:T end\n Empty => end\nend\n", ("Choice",),
        "public define[T] identity(value:T)->T=>$value end\n", ("identity",),
        "identity(Choice.Present(@N@)) match =>\n"
        " as :Present(value) => $value\n as :Empty => 0\nend\n", ("@N@",),
    ),
    "object_tag_result": InteractionSpec(
        ("object", "element", "tag", "computed", "return_type"),
        "public object Item => public $value:Int end\n", ("Item",),
        "import {core.Item}\npublic tag #marked as computed\n"
        "public define inspect(item:Item)->#marked Int=>$item.value #marked end\n",
        ("#marked", "inspect"),
        "inspect(Item(@N@)) | #-marked\n", ("@N@",),
    ),
}

# Independently specified connected contracts, kept separate so future
# generators can evolve without changing the stable project runner.
from tools.multimodule_additional_contracts import ADDITIONAL_CONTRACTS
from tools.multimodule_composition_contracts import COMPOSITION_CONTRACTS
from tools.multimodule_deep_contracts import DEEP_CONTRACTS, INVALID_DEEP_CONTRACTS
from tools.multimodule_boundary_contracts import BOUNDARY_CONTRACTS, INVALID_BOUNDARY_CONTRACTS
from tools.multimodule_lifetime_concurrency_contracts import (
    LIFETIME_CONCURRENCY_CONTRACTS, INVALID_LIFETIME_CONCURRENCY_CONTRACTS,
)
SPECS.update({name: InteractionSpec(*values)
              for catalog in (ADDITIONAL_CONTRACTS, COMPOSITION_CONTRACTS, LIFETIME_CONCURRENCY_CONTRACTS, DEEP_CONTRACTS, BOUNDARY_CONTRACTS)
              for name, values in catalog.items()})

INTERACTIONS = tuple(SPECS)
GRAPHS = ("direct", "facade", "deep_facade", "diamond", "nested", "alias")
LAYOUTS = ("source", "compiled", "core_binary", "dependent_binary", "alternating_binary")
EXECUTION_MODES = ("unoptimized_live", "unoptimized_serialized", "optimized_live", "optimized_serialized")


# Planning inventory, not a claim that all features below are implemented or
# covered by these tests. Every unordered pair gets a machine-readable backlog
# entry; semantic validity and interaction direction are reviewed separately.
LANGUAGE_AXES = (
    "lexing", "parsing", "stack", "numbers", "strings", "tuple", "record", "dictionary",
    "option", "nominal", "return_type", "finite_list", "lazy_list", "rank", "union", "intersection",
    "casts", "type_inference", "higher_order", "vectorisation", "element",
    "overload", "generic", "generic_bounds", "generic_specialisation", "object",
    "construction", "field", "method", "destructor", "trait", "trait_default",
    "trait_inheritance", "dispatch", "variant", "enum", "match", "branch",
    "loop", "exception", "ownership", "borrowing", "tag", "constructed",
    "computed", "unit", "tag_overlay", "tag_validator", "reflection", "json",
    "task", "channel", "concurrency", "ffi", "module_visibility", "reexport",
    "package", "namespace", "binary_artifact", "incremental", "diagnostics",
)


def interaction_case(family: str, graph: str, seed: int) -> ProjectCase:
    """Generate an executable, two-owner dataflow project with a value oracle."""
    if family not in SPECS or graph not in GRAPHS:
        raise ValueError((family, graph))
    spec = SPECS[family]
    rng = random.Random(f"{seed}:{family}")
    number = rng.randrange(2, 99)
    label = f"text_{seed % 997}"
    replacements = {"@N_PLUS_ONE@": str(number + 1), "@N_DOUBLE@": str(number * 2), "@N_TRIPLE@": str(number * 3), "@N@": str(number), "@LABEL@": label}

    def expand(value: str) -> str:
        for k, v in replacements.items():
            value = value.replace(k, v)
        return value

    core = "pkg.core" if graph == "nested" else "core"
    logic = "logic"  # root-level helper imports the nested provider via pkg.core
    modules = [(core, expand(spec.core)),
               (logic, expand(spec.dependent.replace("core.", core + ".")))]
    order = [core, logic]
    names = spec.core_exports + spec.dependent_exports
    selections = f"{core}.[{','.join(spec.core_exports)}],{logic}.[{','.join(spec.dependent_exports)}]"
    consumer = expand(spec.consumer)
    if graph in ("direct", "nested"):
        consumer = f"import {{{selections}}}\n{consumer}"
    else:
        modules.append(("gateway", f"public import {{{selections}}}\n"))
        order.append("gateway")
        selection = ",".join(names)
        endpoint = "gateway"
        if graph in ("facade", "deep_facade", "diamond", "alias"):
            if graph == "alias":
                # A renamed symbol must be an ordinary value, not a data tag.
                renamed = next(n for n in spec.dependent_exports if not n.startswith("#"))
                alias = "Reexported" + renamed
                export_aliases = ",".join(
                    n + " as " + alias if n == renamed else n for n in names)
                modules.append(("api", f"public import {{gateway.[{export_aliases}]}}\n"))
                order.append("api")
                selection = ",".join(alias if n == renamed else n for n in names)
                consumer = re.sub(rf"\b{re.escape(renamed)}\b", alias, consumer)
                endpoint = "api"
            elif graph in ("deep_facade", "diamond"):
                modules.append(("api", f"public import {{gateway.[{selection}]}}\n"))
                order.append("api")
                endpoint = "api"
            if graph == "deep_facade":
                modules.append(("entry", f"public import {{api.[{selection}]}}\n"))
                order.append("entry")
                endpoint = "entry"
            if graph == "diamond":
                modules.append(("other", f"public import {{gateway.[{selection}]}}\n"))
                order.append("other")
                consumer = f"import {{{endpoint}.[{selection}],other as alternate}}\n{consumer}"
            else:
                consumer = f"import {{{endpoint}.[{selection}]}}\n{consumer}"
    # Receive is an observable channel protocol result, not a plain scalar.
    # Construct expected payloads from the seed and declared source schema,
    # independent of VM output (including object type arguments and fields).
    receive_values = {
        "@RECEIVE_BOX@": Receive.Value(ObjectValue("Box", {"value": number}, ("Int",))),
        "@RECEIVE_ITEM@": Receive.Value(ObjectValue("Item", {"value": number})),
        "@RECEIVE_CODE@": Receive.Value(ObjectValue("Code.GOOD", {"value": number})),
        "@RECEIVE_CHOICE@": Receive.Value(ObjectValue("Choice.Present", {"value": number})),
        "@RECEIVE_NUMBER@": Receive.Value(number),
    }
    expected = tuple(receive_values[v] if v in receive_values else
                     int(expand(v)) if re.fullmatch(r"-?\d+", expand(v)) else expand(v)
                     for v in spec.output)
    return ProjectCase(family, graph, seed, tuple(modules), consumer, expected, tuple(order), expand(spec.stdout))


def prepare_layout(root: Path, case: ProjectCase, layout: str) -> tuple[str, ...]:
    """Compile *all* modules first, then delete selected sources to force link paths."""
    if layout not in LAYOUTS:
        raise ValueError(layout)
    if layout == "source":
        return ()
    coordinator = CompilationCoordinator(root)
    for module in case.build_order:
        source = root.joinpath(*module.split(".")).with_suffix(".vlnc")
        coordinator.build_module(source, source.with_suffix(".vbcm"), module_name=module)
    if layout == "compiled":
        binaries = case.build_order
    elif layout == "core_binary":
        binaries = case.build_order[:1]
    elif layout == "dependent_binary":
        binaries = case.build_order[1:2]
    else:
        binaries = case.build_order[::2]
    for module in binaries:
        root.joinpath(*module.split(".")).with_suffix(".vlnc").unlink()
    return tuple(binaries)


def execute_interaction(case: ProjectCase, layout: str = "source") -> int:
    with TemporaryDirectory(prefix="valiance-interactions-") as folder:
        root = Path(folder)
        write_project(root, case)
        prepare_layout(root, case, layout)
        analyser = Analyser(source_file=root / "main.vlnc")
        typed = analyser.analyse(parse(case.consumer))
        if analyser.diagnostics:
            raise AssertionError(f"{case.identity}/{layout}: {analyser.diagnostics}")
        count = 0
        for optimized in (False, True):
            compiled = compile_program(typed, optimize=optimized)
            for serialized, candidate in ((False, compiled), (True, loads(dumps(compiled)))):
                if case.expected_stdout:
                    output = StringIO()
                    with redirect_stdout(output):
                        actual = tuple(run(candidate))
                    if output.getvalue() != case.expected_stdout:
                        raise AssertionError(f"{case.identity}/{layout} optimize={optimized} "
                                             f"serialized={serialized}: stdout "
                                             f"{output.getvalue()!r} != {case.expected_stdout!r}")
                else:
                    actual = tuple(run(candidate))
                count += 1
                if actual != case.expected:
                    raise AssertionError(f"{case.identity}/{layout} optimize={optimized} "
                                         f"serialized={serialized}: {actual!r} != {case.expected!r}")
        return count



# Each mutation must fail with the same *semantic diagnostic class* when source
# and compiled providers are mixed. Parse failures or unrelated exceptions are
# not accepted as a successful negative test.
INVALID_INTERACTIONS = {
    "generic_object_variant": (
        '$wrong:Choice[Int]=Choice.Present(Box("bad"))\n', "cannot assign"),
    "overload_enum": ('inspect("bad")\n', "no overloads"),
    "object_tag_result": ('inspect(Item(42)) | #-marked | #-marked\n',
                          "cannot remove absent tag"),
    "generic_variant_identity": (
        '$wrong:Choice[Int]=identity(Choice.Present("bad"))\n', "cannot assign"),
    "constructed_tag_generic": ('42 #unknown | identity\n', "unknown data tag"),
    "enum_object_field": ('Envelope(Token.\\MISSING.value)\n', "unknown element"),
    "overload_generic_object": ('read(Box(true))\n', "no overloads"),
    "trait_variant_dispatch": ('Item("bad") as[Sized] | size\n', "no overloads"),
    "generic_trait_variant": ('Box("bad") as[Source[Int]] | read\n', "cannot safely cast"),
}


# Negative contracts specifically exercise the new connected families.  They
# deliberately reference imported nominal types or overloads (not arbitrary
# undeclared names), and must fail with the designated analyser category.
INVALID_INTERACTIONS.update({
    "inherited_default_enum": ('Item("bad") as[Parent] | doubled\n', "no overloads"),
    "bounded_generic_trait": ('measure("bad")\n', "no overloads"),
    "union_object_match": ('inspect("bad")\n', "no overloads"),
    "optional_cast_union": ('extract("bad")\n', "no overloads"),
    "asserted_cast_trait": ('extract("bad")\n', "no overloads"),
    "higher_order_generic_object": ('apply(fn(x:Int)->Int=>$x end, Box(@N@))\n', "no overloads"),
    "structural_record_generic": ('lift(record{y=>@N@})\n', "no overloads"),
    "record_object_field": ('read("bad")\n', "no overloads"),
    "dictionary_object_field": ('read("bad")\n', "no overloads"),
    "list_variant_match": ('read("bad")\n', "no overloads"),
    "dictionary_variant_match": ('read("bad")\n', "no overloads"),
    "optional_generic_object": ('read("bad")\n', "no overloads"),
    "tuple_generic_object": ('read("bad")\n', "no overloads"),
    "branch_enum_generic": ('Code.\\MISSING.value\n', "unknown element"),
    "while_generic_object": ('count("bad")\n', "no overloads"),
    "exception_trait_dispatch": ('safe("bad")\n', "no overloads"),
    "higher_order_object_method": ('apply("bad", Item(@N@))\n', "no overloads"),
    "unit_tag_enum": ('Distance.\\MISSING.value #metres\n', "unknown element"),
    "variant_tag_trait": ('Item(@N@) as[Sized] | #unknown\n', "unknown data tag"),
    "overload_trait_default": ('describe(\"bad\")\n', "no overloads"),
    "intersection_trait_object": ('evaluate("bad")\n', "no overloads"),
    "list_generic_overload": ('head("bad")\n', "no overloads"),
    "tag_validator_overload": ('1 #unknown\n', "unknown data tag"),
    "tag_overlay_generic": ('1 #sticky | #-sticky | #-sticky\n', "cannot remove absent tag"),
    "reflected_object_field": ('reflect("bad")\n', "no overloads"),
    "json_object_roundtrip": ('relay("bad")\n', "no overloads"),
    "lazy_variant_map": ('select("bad")\n', "no overloads"),
    "list_object_mutation": ('update("bad")\n', "no overloads"),
    "dict_object_mutation": ('update("bad")\n', "no overloads"),
    "tag_variant_match": ('classify("bad")\n', "no overloads"),
})

# Adversarial counterparts to composition contracts. Each negative case
# references a real imported declaration, type, constructor, or tag, and
# asserts an exact analyser diagnostic class after re-export/artifact changes.
INVALID_INTERACTIONS.update(
    {'enum_in_generic_box': ('Box(Token.\\MISSING)\n', 'unknown element'),
     'enum_in_generic_variant': ('Choice.Present(Token.\\MISSING)\n', 'unknown element'),
     'enum_through_list': ('select("bad")\n', 'no overloads'),
     'enum_through_dictionary': ('Code.\\MISSING\n', 'unknown element'),
     'enum_through_record': ('Code.\\MISSING\n', 'unknown element'),
     'enum_through_tuple': ('Code.\\MISSING\n', 'unknown element'),
     'variant_stored_in_object': ('Holder("bad")\n', 'no overloads'),
     'variant_through_tuple': ('Choice.\\MISSING\n', 'unknown element'),
     'variant_through_record': ('Choice.\\MISSING\n', 'unknown element'),
     'variant_in_option': ('inspect("bad")\n', 'no overloads'),
     'variant_in_list_mutation': ('update("bad")\n', 'no overloads'),
     'variant_in_dictionary_mutation': ('update("bad")\n', 'no overloads'),
     'trait_default_via_bounded_generic': ('labelOf("bad")\n', 'no overloads'),
     'trait_inherited_default_in_variant': ('Item("bad")\n', 'no overloads'),
     'trait_dispatch_in_higher_order': ('apply("bad",Item(@N@))\n', 'no overloads'),
     'trait_dispatch_after_option': ('inspect("bad")\n', 'no overloads'),
     'trait_dispatch_from_list': ('inspect("bad")\n', 'no overloads'),
     'trait_dispatch_from_dictionary': ('inspect("bad")\n', 'no overloads'),
     'trait_default_overload_from_enum': ('show(true)\n', 'no overloads'),
     'union_object_list_match': ('inspect("bad")\n', 'no overloads'),
     'union_object_dictionary_match': ('inspect("bad")\n', 'no overloads'),
     'union_optional_cast_through_generic': ('Item("bad")\n', 'no overloads'),
     'tuple_of_generic_boxes': ('$bad:Box[Int]=Box("bad")\n', 'cannot assign'),
     'record_of_generic_boxes': ('$bad:Box[Int]=Box("bad")\n', 'cannot assign'),
     'dictionary_of_generic_boxes': ('$bad:Box[Int]=Box("bad")\n', 'cannot assign'),
     'generic_specialization_overload_tuple': ('select(record{x=>true})\n', 'no overloads'),
     'generic_specialization_overload_record': ('select(record{x=>true})\n', 'no overloads'),
     'generic_option_overload': ('unpack("bad")\n', 'no overloads'),
     'generic_variant_in_loop': ('repeat("bad")\n', 'no overloads'),
     'enum_in_branch_loop': ('Code.\\MISSING\n', 'unknown element'),
     'variant_in_exception_handler': ('safe("bad")\n', 'no overloads'),
     'tag_on_enum_via_generic': ('@N@ #unknown\n', 'unknown data tag'),
     'constructed_tag_through_tuple': ('@N@ #sticky | #-sticky | #-sticky\n',
                                       'cannot remove absent tag'),
     'constructed_tag_through_record': ('@N@ #sticky | #-sticky | #-sticky\n',
                                        'cannot remove absent tag'),
     'computed_tag_through_dictionary': ('@N@ #unknown\n', 'unknown data tag'),
     'unit_tag_through_generic_object': ('@N@ #metres | #-metres | #-metres\n',
                                         'cannot remove absent tag'),
     'validator_on_object_field': ('@N@ #unknown\n', 'unknown data tag'),
     'overlay_on_object_field': ('@N@ #sticky | #-sticky | #-sticky\n',
                                 'cannot remove absent tag'),
     'reflection_generic_object': ('read("bad")\n', 'no overloads'),
     'json_enum_value': ('Holder("bad")\n', 'no overloads'),
     'lazy_enum_list': ('select(["bad"])\n', 'no overloads'),
     'lazy_object_trait_dispatch': ('select(["bad"])\n', 'no overloads')}
)


INVALID_INTERACTIONS.update(INVALID_LIFETIME_CONCURRENCY_CONTRACTS)
INVALID_INTERACTIONS.update(INVALID_DEEP_CONTRACTS)
INVALID_INTERACTIONS.update(INVALID_BOUNDARY_CONTRACTS)


def check_invalid_interaction(family: str, graph: str, seed: int,
                              layout: str = "source") -> str:
    """Expect the designated analyser error, never an incidental parser fault."""
    if family not in INVALID_INTERACTIONS:
        raise ValueError(family)
    case = interaction_case(family, graph, seed)
    statement, diagnostic = INVALID_INTERACTIONS[family]
    statement = statement.replace("@N@", str(random.Random(f"{seed}:{family}").randrange(2, 99)))
    # The alias graph *renames* the dependent's public symbol; using the
    # original name would test name resolution rather than the intended fault.
    if graph == "alias":
        imported = next(n for n in SPECS[family].dependent_exports
                        if not n.startswith("#"))
        statement = re.sub(rf"\b{re.escape(imported)}\b",
                           "Reexported" + imported, statement)
    import_line = case.consumer.partition("\n")[0]
    with TemporaryDirectory(prefix="valiance-interactions-invalid-") as folder:
        root = Path(folder)
        write_project(root, case)
        prepare_layout(root, case, layout)
        analyser = Analyser(source_file=root / "main.vlnc")
        analyser.analyse(parse(import_line + "\n" + statement))
        rendered = "\n".join(map(str, analyser.diagnostics))
        if diagnostic not in rendered:
            raise AssertionError(f"{family}/{graph}/{layout}: expected {diagnostic!r}, "
                                 f"actual {rendered!r}")
        return diagnostic


@cache
def combinations() -> tuple[tuple[str, str, str], ...]:
    """Exact finite testing universe, independently of sampling order."""
    return tuple((family, graph, layout)
                 for family in INTERACTIONS for graph in GRAPHS for layout in LAYOUTS)


@cache
def _positive_order(seed: int) -> tuple[int, ...]:
    indices = list(range(len(combinations())))
    random.Random(seed).shuffle(indices)
    return tuple(indices)


@cache
def _negative_order(seed: int) -> tuple[int, ...]:
    indices = list(range(len(negative_combinations())))
    random.Random(seed).shuffle(indices)
    return tuple(indices)


def candidate_at(seed: int, absolute_index: int) -> tuple[ProjectCase, str]:
    """Stable replay indexing, with complete coverage before the next seed cycle."""
    universe = combinations()
    cycle, index = divmod(absolute_index, len(universe))
    # Reuse the same deterministic shuffled index for each cycle. Previously
    # every candidate regenerated/shuffled the entire 5k+ universe (quadratic
    # work when building the inventory). Caching changes no replay identity.
    order = _positive_order(seed + cycle)
    family, graph, layout = universe[order[index]]
    return interaction_case(family, graph, seed + cycle), layout


@cache
def negative_combinations() -> tuple[tuple[str, str, str], ...]:
    return tuple((f, g, l) for f in INVALID_INTERACTIONS for g in GRAPHS for l in LAYOUTS)


def negative_at(seed: int, absolute_index: int) -> tuple[str, str, str]:
    universe = negative_combinations()
    cycle, offset = divmod(absolute_index, len(universe))
    return universe[_negative_order(seed + cycle)[offset]]


def coverage_inventory() -> dict[str, Any]:
    """Disclose exactly which finite pairs are represented by real source cases."""
    universe = combinations()
    measured_features = sorted({f for spec in SPECS.values() for f in spec.features})
    covered_feature_pairs = sorted({tuple(sorted((a, b))) for spec in SPECS.values()
                                    for i, a in enumerate(spec.features)
                                    for b in spec.features[i + 1:]})
    possible_pairs = [(a, b) for i, a in enumerate(measured_features)
                      for b in measured_features[i + 1:]]
    uncovered = sorted(set(possible_pairs) - set(covered_feature_pairs))
    planned_pairs = [(a, b) for i, a in enumerate(LANGUAGE_AXES)
                     for b in LANGUAGE_AXES[i + 1:]]
    recorded_pairs = set(covered_feature_pairs)
    # "fixture_defined" denotes a runnable contract, not a passing result.
    # The latter must come from a specific campaign report.
    pair_backlog = [
        {"features": [a, b],
         "status": "fixture_defined" if (a, b) in recorded_pairs or (b, a) in recorded_pairs
         else "needs_semantic_contract",
         "fixtures": [name for name, spec in SPECS.items()
                      if a in spec.features and b in spec.features]}
        for a, b in planned_pairs
    ]
    return {
        "claim": "Finite matrix enumeration; NOT exhaustive language coverage",
        "interaction_families": len(INTERACTIONS),
        "dependency_graphs": list(GRAPHS), "source_artifact_layouts": list(LAYOUTS),
        "execution_modes": list(EXECUTION_MODES),
        "project_combinations": len(universe),
        "possible_execution_checks": len(universe) * len(EXECUTION_MODES),
        "semantic_features": measured_features,
        "feature_pairs_with_a_connected_fixture": [list(pair) for pair in covered_feature_pairs],
        "uncovered_feature_pairs": [list(pair) for pair in uncovered],
        "pair_coverage": {"covered": len(covered_feature_pairs),
                          "possible": len(possible_pairs)},
        "families": {key: list(value.features) for key, value in SPECS.items()},
        "negative_semantic_mutations": list(INVALID_INTERACTIONS),
        "total_planned_axes": len(LANGUAGE_AXES),
        "total_planned_unordered_pairs": len(planned_pairs),
        "semantic_pair_backlog": pair_backlog,
        "pair_status_meaning": "fixture_defined does not mean passed; verified status requires a campaign report",
        "pair_backlog_counts": {
            "fixture_defined": sum(v["status"] == "fixture_defined" for v in pair_backlog),
            "needs_semantic_contract": sum(v["status"] == "needs_semantic_contract" for v in pair_backlog),
        },
        "possible_negative_graph_layout_cases": len(negative_combinations()),
        "scheduled_positive_cases": [
            {"index": i, "family": case.family, "graph": case.graph,
             "layout": layout, "seed": case.seed}
            for i in range(len(universe))
            for case, layout in (candidate_at(49103, i),)
        ],
        "scheduled_negative_cases": [
            {"index": i, "family": family, "graph": graph, "layout": layout,
             "seed": 49103, "expected_diagnostic": INVALID_INTERACTIONS[family][1]}
            for i in range(len(negative_combinations()))
            for family, graph, layout in (negative_at(49103, i),)
        ],
        "limitations": [
            "Feature co-occurrence is not a proof of every ordering or type-level interaction.",
            "Only the explicit finite templates and graph/layout axes are enumerated.",
            "The matrix does not cover scheduling, FFI, packages, cycles, arbitrary depths or all parser grammar.",
            "A case is verified only after its independent expected output passes in that mode.",
        ],
    }


def campaign(*, seed: int = 49103, start: int = 0, iterations: int = 30,
             fail_fast: bool = False, negative: bool = False) -> dict[str, Any]:
    if start < 0 or iterations < 0:
        raise ValueError("start and iterations must be non-negative")
    completed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    checks = 0
    for index in range(start, start + iterations):
        if negative:
            family, graph, layout = negative_at(seed, index)
            case = interaction_case(family, graph, seed)
        else:
            case, layout = candidate_at(seed, index)
            family, graph = case.family, case.graph
        try:
            if negative:
                check_invalid_interaction(family, graph, seed, layout)
                checks += 1
            else:
                checks += execute_interaction(case, layout)
        except Exception as exc:
            failures.append({"index": index, "identity": case.identity,
                             "layout": layout, "error": repr(exc),
                             "sources": dict(case.modules), "consumer": case.consumer,
                             "expected": list(case.expected),
                             "expected_diagnostic": INVALID_INTERACTIONS[family][1] if negative else None,
                             "build_order": list(case.build_order)})
            if fail_fast:
                break
        else:
            completed.append({"index": index, "family": family,
                              "graph": graph, "layout": layout})
    observed = {(x["family"], x["graph"], x["layout"]) for x in completed}
    return {"seed": seed, "start": start, "iterations_requested": iterations,
            "negative": negative,
            "cases_passed": len(completed), "cases_failed": len(failures),
            "execution_checks_passed": 0 if negative else checks,
            "diagnostic_checks_passed": checks if negative else 0,
            "covered_combinations": len(observed),
            "possible_combinations": len(negative_combinations() if negative else combinations()),
            "cases_not_verified": iterations - len(completed),
            "passed": completed, "failures": failures}


def batched_campaign(*, seed: int, start: int, iterations: int,
                     batch_size: int, timeout: int, fail_fast: bool,
                     negative: bool = False) -> dict[str, Any]:
    if batch_size <= 0 or timeout <= 0:
        raise ValueError("batch_size and timeout must be positive")
    combined: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    checks = 0
    diagnostics = 0
    for offset in range(start, start + iterations, batch_size):
        n = min(batch_size, start + iterations - offset)
        argv = [sys.executable, "-m", "tools.multimodule_interactions", "--seed", str(seed),
                "--start", str(offset), "--iterations", str(n), "--batch-size", "0"]
        if negative:
            argv.append("--negative")
        try:
            worker = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            result = json.loads(worker.stdout)
        except (subprocess.TimeoutExpired, ValueError) as exc:
            failed.append({"index_range": [offset, offset + n], "error": repr(exc)})
        else:
            combined.extend(result["passed"])
            failed.extend(result["failures"])
            checks += result["execution_checks_passed"]
            diagnostics += result["diagnostic_checks_passed"]
            if worker.returncode and not result["failures"]:
                failed.append({"index_range": [offset, offset + n],
                               "error": f"worker exited {worker.returncode}: {worker.stderr[-1000:]}"})
        if fail_fast and failed:
            break
    return {"seed": seed, "start": start, "iterations_requested": iterations,
            "cases_passed": len(combined), "cases_failed": len(failed),
            "negative": negative,
            "execution_checks_passed": checks,
            "diagnostic_checks_passed": diagnostics,
            "covered_combinations": len({(x["family"], x["graph"], x["layout"]) for x in combined}),
            "possible_combinations": len(negative_combinations() if negative else combinations()),
            "cases_not_verified": iterations - len(combined),
            "passed": combined, "failures": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=49103)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--batch-timeout", type=int, default=70)
    parser.add_argument("--inventory", action="store_true", help="report finite coverage and missing interactions")
    parser.add_argument("--all", action="store_true", dest="entire_matrix",
                        help="execute the exact current positive/negative universe (no hard-coded counts)")
    parser.add_argument("--negative", action="store_true", help="enumerate semantic rejection contracts")
    parser.add_argument("--replay-index", type=int)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.entire_matrix:
        if args.start or args.replay_index is not None:
            parser.error("--all cannot be combined with --start or --replay-index")
        args.iterations = len(negative_combinations() if args.negative else combinations())
    if args.inventory:
        report = coverage_inventory()
    elif args.replay_index is not None:
        if args.negative:
            family, graph, layout = negative_at(args.seed, args.replay_index)
            case = interaction_case(family, graph, args.seed)
        else:
            case, layout = candidate_at(args.seed, args.replay_index)
        try:
            if args.negative:
                check_invalid_interaction(case.family, case.graph, args.seed, layout)
                checks = 1
            else:
                checks = execute_interaction(case, layout)
        except Exception as exc:
            report = {"case": case.identity, "layout": layout, "error": repr(exc), "failed": True}
        else:
            report = {"case": case.identity, "layout": layout,
                      "diagnostic_checks_passed" if args.negative else "execution_checks_passed": checks,
                      "failed": False}
    else:
        args_dict = dict(seed=args.seed, start=args.start, iterations=args.iterations,
                         fail_fast=args.fail_fast, negative=args.negative)
        report = (batched_campaign(**args_dict, batch_size=args.batch_size,
                                   timeout=args.batch_timeout)
                  if args.batch_size and args.iterations > args.batch_size
                  else campaign(**args_dict))
    result = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)
    return int(bool(report.get("cases_failed") or report.get("failed")))


if __name__ == "__main__":
    sys.exit(main())
