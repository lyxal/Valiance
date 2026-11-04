"""Reproducible project-graph testing with independent source-level oracles.

Each case is a real temporary filesystem project.  The source-free mode builds
all dependency modules before removing their sources, rather than silently
falling back to source imports.  The expected stack is computed from seed
parameters, not from another Valiance execution mode.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import random
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any

from valiance.analysis import Analyser
from valiance.incremental import CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run


@dataclass(frozen=True)
class ProjectCase:
    family: str
    graph: str
    seed: int
    modules: tuple[tuple[str, str], ...]
    consumer: str
    expected: tuple[Any, ...]
    build_order: tuple[str, ...]
    expected_stdout: str = ""

    @property
    def identity(self) -> str:
        return f"{self.family}/{self.graph}/{self.seed}"


FAMILIES = (
    "element", "overloaded_element", "generic_element", "generic_object",
    "object_method", "trait", "trait_default", "trait_inheritance", "generic_trait",
    "variant", "nongeneric_variant", "enum", "plain_enum", "tag",
    "constructed_tag", "unit_tag", "variant_tag",
    "cross_module_trait", "cross_module_generic",
)
GRAPHS = ("direct", "facade", "deep_facade", "diamond", "nested", "alias")


def _fixture(family: str, seed: int) -> tuple[str, tuple[str, ...], str, tuple[Any, ...]]:
    """Build independent expected outcomes for a fully typed fixture."""
    rng = random.Random((seed << 12) ^ (sum(map(ord, family)) * 41))
    n = rng.randint(2, 80)
    m = rng.randint(1, 30)
    label = f"text_{seed % 1009}"
    if family == "element":
        return (f"public define bump(n:Int)->Int=>$n+{m} end\n", ("bump",),
                f"bump({n})\nbump({n + 1})\n", (n + m, n + m + 1))
    if family == "overloaded_element":
        return ("public define pick(value:Int)->Int=>$value+1 end\n"
                "public define pick(value:String)->String=>$value end\n", ("pick",),
                f'pick({n})\npick("{label}")\n', (n + 1, label))
    if family == "generic_element":
        return ("public define[T] identity(value:T)->T=>$value end\n", ("identity",),
                f'identity({n})\nidentity("{label}")\nidentity({m})\n',
                (n, label, m))
    if family == "generic_object":
        return ("public object[T] Box => public $value:T end\n", ("Box",),
                f'$a:Box[Int]=Box({n})\n$a.value\n'
                f'$b:Box[String]=Box("{label}")\n$b.value\n', (n, label))
    if family == "object_method":
        return ("public object Item =>\n  public $value:Int\n"
                "  public define read -> Int => $self.value end\nend\n", ("Item",),
                f"Item({n}) read\nItem({m}) read\n", (n, m))
    if family == "trait":
        return ("public trait Sized => extend size -> Int end\n"
                "public object Item => public $value:Int end\n"
                "object Item as Sized => define size => $self.value end\n",
                ("Sized", "Item"),
                f"Item({n}) as[Sized] | size\nItem({m}) as[Sized] | size\n",
                (n, m))
    if family == "trait_default":
        return ("public trait Named => public define label -> String => \"default\" end\n"
                "public object Item => public $value:Int end\n"
                "object Item as Named => end\n", ("Named", "Item"),
                f"Item({n}) as[Named] | label\n", ("default",))
    if family == "trait_inheritance":
        return ("public trait Parent => extend size -> Int end\n"
                "public trait Child as Parent => end\n"
                "public object Item => public $value:Int end\n"
                "object Item as Child => define size => $self.value end\n",
                ("Parent", "Child", "Item"),
                f"Item({n}) as[Parent] | size\n", (n,))
    if family == "generic_trait":
        return ("public trait[T] Source => extend read -> T end\n"
                "public object[T] Box => public $value:T end\n"
                "object[T] Box as Source[T] => define read -> T => $self.value end\n",
                ("Source", "Box"),
                f'Box({n}) as[Source[Int]] | read\n'
                f'Box("{label}") as[Source[String]] | read\n', (n, label))
    if family == "variant":
        return ("public variant[T] Maybe =>\n"
                "  Present => public $value:T end\n"
                "  Absent => end\nend\n", ("Maybe",),
                f"Maybe.Present({n}) match =>\n"
                "  as :Present(value) => $value\n  as :Absent => 0\nend\n"
                "Maybe.Absent match =>\n"
                "  as :Present(value) => $value\n  as :Absent => 0\nend\n",
                (n, 0))
    if family == "nongeneric_variant":
        return ("public variant Choice =>\n"
                "  Value => public $value:Int end\n"
                "  Empty => end\nend\n", ("Choice",),
                f"Choice.Value({n}) match =>\n"
                "  as :Value(value) => $value\n  as :Empty => 0\nend\n",
                (n,))
    if family == "enum":
        return ("public enum[String] Token =>\n"
                '  YES = "yes"\n  NO = "no"\nend\n', ("Token",),
                'Token.\\YES.value\nToken.\\NO.value\n', ('yes', 'no'))
    if family == "plain_enum":
        return ("public enum Colour => RED GREEN end\n", ("Colour",),
                'Colour.\\RED.name\nColour.\\GREEN.name\n', ('RED', 'GREEN'))
    if family == "tag":
        return ("public tag #marked as computed\n"
                "public define inspect(value:#marked Int)->Int=>$value+1 end\n",
                ("#marked", "inspect"),
                f"{n} #marked | inspect\n{m} #marked | inspect\n", (n + 1, m + 1))
    if family == "constructed_tag":
        return ("public tag #sticky as constructed\n"
                "public define preserve(value:#sticky Int)->#sticky Int=>$value end\n",
                ("#sticky", "preserve"),
                f"{n} #sticky | preserve | #-sticky\n", (n,))
    if family == "unit_tag":
        return ("public tag #metres as unit\n", ("#metres",),
                f"{n} #metres | #-metres\n", (n,))
    if family == "variant_tag":
        return ("public tag #sorted as computed\n"
                "public tag #ascending as #sorted\n",
                ("#sorted", "#ascending"),
                f"{n} #ascending match =>\n"
                "  as :#ascending Int => 1\n  _ => 0\nend\n", (1,))
    raise ValueError(f"Unknown base family: {family}")


def _aliases(exports: tuple[str, ...]) -> tuple[dict[str, str], str]:
    # Tag symbols have no import-alias syntax; rename a normal element instead.
    first = next((name for name in exports if not name.startswith("#")), None)
    if first is None:
        return {}, ",".join(exports)
    alias = "Alias" + first
    imports = ",".join(f"{name} as {alias}" if name == first else name
                       for name in exports)
    return {first: alias}, imports


def _rewrite(source: str, aliases: dict[str, str]) -> str:
    for original, alias in aliases.items():
        if original.startswith("#"):
            source = source.replace(original, alias)
        else:
            source = re.sub(rf"\b{re.escape(original)}\b", alias, source)
    return source


def project_case(family: str, graph: str, seed: int) -> ProjectCase:
    if family not in FAMILIES or graph not in GRAPHS:
        raise ValueError((family, graph))
    if family.startswith("cross_module"):
        return _cross_project(family, graph, seed)
    provider, exports, expression, expected = _fixture(family, seed)
    names = ",".join(exports)
    provider_name = "pkg.core" if graph == "nested" else "core"
    modules = [(provider_name, provider)]
    order = [provider_name]
    if graph in ("direct", "nested"):
        consumer = f"import {{{provider_name}.[{names}]}}\n{expression}"
    elif graph == "facade":
        modules.append(("api", f"public import {{{provider_name}.[{names}]}}\n"))
        order.append("api")
        consumer = f"import {{api.[{names}]}}\n{expression}"
    elif graph == "deep_facade":
        modules += [("middle", f"public import {{{provider_name}.[{names}]}}\n"),
                    ("api", f"public import {{middle.[{names}]}}\n")]
        order += ["middle", "api"]
        consumer = f"import {{api.[{names}]}}\n{expression}"
    elif graph == "diamond":
        modules += [("left", f"public import {{{provider_name}.[{names}]}}\n"),
                    ("right", f"public import {{{provider_name}.[{names}]}}\n")]
        order += ["left", "right"]
        # The right-hand import must not override the left's selected symbols.
        consumer = f"import {{left.[{names}],right as alternate}}\n{expression}"
    else:  # alias on a public facade, including renamed generic nominal/trait/tag.
        aliases, aliased = _aliases(exports)
        modules += [("api", f"public import {{{provider_name}.[{aliased}]}}\n")]
        order.append("api")
        consumer = f"import {{api.[{','.join(aliases.get(n, n) for n in exports)}]}}\n"
        consumer += _rewrite(expression, aliases)
    return ProjectCase(family, graph, seed, tuple(modules), consumer, expected, tuple(order))


def _cross_project(family: str, graph: str, seed: int) -> ProjectCase:
    rng = random.Random(seed * 211 + (31 if family == "cross_module_trait" else 7))
    n = rng.randint(1, 90)
    text = f"sample_{seed % 997}"
    if family == "cross_module_trait":
        modules = [
            ("models", "public trait Sized => extend size -> Int end\n"
                       "public object Item => public $value:Int end\n"
                       "object Item as Sized => define size => $self.value end\n"),
            ("helpers", "import {models.[Sized,Item]}\n"
                        "public define double(value:Sized)->Int=>($value size)*2 end\n"),
        ]
        expr = f"double(Item({n}) as[Sized])\n"
        names = "Item,Sized,double"
        expected = (n * 2,)
    else:
        modules = [
            ("models", "public object[T] Box => public $value:T end\n"),
            ("helpers", "import {models.Box}\n"
                        "public define[T] unwrap(box:Box[T])->T=>$box.value end\n"),
        ]
        expr = f'unwrap(Box({n}))\nunwrap(Box("{text}"))\n'
        names = "Box,unwrap"
        expected = (n, text)
    # The aggregator deliberately spans two provider modules.
    modules.append(("gateway", "public import {models.[" + (
        "Item,Sized" if family == "cross_module_trait" else "Box") +
        "],helpers.[" + ("double" if family == "cross_module_trait" else "unwrap") + "]}\n"))
    if graph in ("facade", "deep_facade", "alias", "diamond"):
        modules.append(("api", f"public import {{gateway.[{names}]}}\n"))
        first = "api"
    else:
        first = "gateway"
    if graph == "deep_facade":
        modules.append(("entry", f"public import {{api.[{names}]}}\n"))
        first = "entry"
    if graph == "diamond":
        modules.append(("second", f"public import {{gateway.[{names}]}}\n"))
        imported = f"import {{{first}.[{names}],second as other}}\n"
    elif graph == "alias":
        # Renaming the public API while retaining the provider-private imports.
        alias_name = "renamedDouble" if family == "cross_module_trait" else "renamedUnwrap"
        old = "double" if family == "cross_module_trait" else "unwrap"
        modules[-1] = (modules[-1][0], f"public import {{gateway.[{names.replace(old, old + ' as ' + alias_name)}]}}\n")
        imported = f"import {{api.[{names.replace(old, alias_name)}]}}\n"
        expr = expr.replace(old + "(", alias_name + "(")
    else:
        imported = f"import {{{first}.[{names}]}}\n"
    # Nested graph tests nested source-only ownership via the ordinary base family.
    return ProjectCase(family, graph, seed, tuple(modules), imported + expr,
                       expected, tuple(name for name, _ in modules))


@dataclass(frozen=True)
class RejectedProject:
    """Invalid consumer over otherwise valid project dependency modules."""

    name: str
    modules: tuple[tuple[str, str], ...]
    consumer: str
    diagnostic: str


def rejected_projects() -> tuple[RejectedProject, ...]:
    """Negative oracles for module visibility, identity and type checking."""
    obj = "public object[T] Box => public $value:T end\n"
    variant = ("public variant[T] Maybe =>\n"
               "  Present => public $value:T end\n"
               "  Absent => end\nend\n")
    return (
        RejectedProject("private_element", (("core", "define hidden(n:Int)->Int=>$n end\n"
                         "public define visible(n:Int)->Int=>hidden($n) end\n"),),
                        "import {core.[hidden]}\nhidden(1)", "no public component 'hidden'"),
        RejectedProject("private_object", (("core", "object Secret => $id:Int end\n"),),
                        "import {core.Secret}", "no public component 'Secret'"),
        RejectedProject("private_reexport", (
            ("core", "define hidden(n:Int)->Int=>$n end\n"
                     "public define visible(n:Int)->Int=>hidden($n) end\n"),
            ("api", "public import {core.visible}\n")),
                        "import {api.[hidden]}\nhidden(4)", "no public component 'hidden'"),
        RejectedProject("incorrect_generic_argument", (("core", obj),),
                        "import {core.Box}\n$value:Box[String]=Box(4)",
                        "cannot assign"),
        RejectedProject("incorrect_generic_variant", (("core", variant),),
                        'import {core.Maybe}\n$value:Maybe[Int]=Maybe.Present("wrong")',
                        "cannot assign"),
        RejectedProject("unimplemented_trait", (("core", "public trait Sized =>"
                        " extend size -> Int end\npublic object Box =>"
                        " public $value:Int end\n"),),
                        "import {core.[Sized,Box]}\nBox(2) as[Sized]",
                        "cannot safely cast"),
        RejectedProject("missing_export", (("core", obj),),
                        "import {core.NotExported}",
                        "no public component 'NotExported'"),
        RejectedProject("missing_module", (),
                        "import {missing.something}", "was not found"),
        RejectedProject("overload_collision", (
            ("a", "public define bump(n:Int)->Int=>$n end\n"),
            ("b", "public define bump(n:Int)->Int=>$n end\n")),
                        "import {a.bump,b.bump}\nbump(3)",
                        "conflicting imported overload 'bump'"),
        RejectedProject("nominal_collision", (("a", obj), ("b", obj)),
                        "import {a.Box,b.Box}",
                        "conflicting imported symbol 'Box'"),
        RejectedProject("tag_not_imported", (("core", "public tag #marked as computed\n"
                         "public define inspect(value:#marked Int)->Int=>$value+1 end\n"),),
                        "import {core.inspect}\n2 #marked | inspect",
                        "unknown data tag '#marked'"),
        RejectedProject("wrong_overload", (("core", "public define bump(n:Int)->Int=>"
                        "$n+1 end\n"),), "import {core.bump}\nbump(\"bad\")",
                        "no overloads"),
    )


def check_rejected(case: RejectedProject, *, source_free: bool = False) -> None:
    """Require *specific* semantic diagnostics, not an arbitrary parse error."""
    with TemporaryDirectory(prefix="valiance-multimodule-invalid-") as folder:
        root = Path(folder)
        for name, source in case.modules:
            path = root.joinpath(*name.split('.')).with_suffix('.vlnc')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding='utf-8')
        if source_free:
            coordinator = CompilationCoordinator(root)
            for name, _ in case.modules:
                path = root.joinpath(*name.split('.')).with_suffix('.vlnc')
                coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
                path.unlink()
        analyser = Analyser(source_file=root / "main.vlnc")
        analyser.analyse(parse(case.consumer))
        rendered = "\n".join(map(str, analyser.diagnostics))
        if case.diagnostic not in rendered:
            raise AssertionError(f"{case.name} source_free={source_free}: expected "
                                 f"{case.diagnostic!r}, actual {rendered!r}")


def write_project(root: Path, case: ProjectCase) -> None:
    for module_name, source in case.modules:
        path = root.joinpath(*module_name.split('.')).with_suffix('.vlnc')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    (root / 'main.vlnc').write_text(case.consumer, encoding='utf-8')


def execute_case(case: ProjectCase, *, source_free: bool = False,
                 both_optimizers: bool = True, persisted: bool = True) -> int:
    """Assert a source project against its independent value oracle in each mode.

    Returns the number of actual executions, not the number of source cases.
    """
    with TemporaryDirectory(prefix="valiance-multimodule-") as folder:
        root = Path(folder)
        write_project(root, case)
        if source_free:
            coordinator = CompilationCoordinator(root)
            for module in case.build_order:
                path = root.joinpath(*module.split('.')).with_suffix('.vlnc')
                coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=module)
            for module in case.build_order:
                root.joinpath(*module.split('.')).with_suffix('.vlnc').unlink()
        analyser = Analyser(source_file=root / "main.vlnc")
        typed = analyser.analyse(parse(case.consumer))
        if analyser.diagnostics:
            raise AssertionError(f"{case.identity}: {analyser.diagnostics}")
        count = 0
        for optimize in ((False, True) if both_optimizers else (True,)):
            program = compile_program(typed, optimize=optimize)
            for serialized, candidate in ((False, program), (True, loads(dumps(program)))) if persisted else ((False, program),):
                actual = run(candidate)
                count += 1
                if tuple(actual) != case.expected:
                    raise AssertionError(
                        f"{case.identity} source_free={source_free} optimize={optimize} "
                        f"serialized={serialized}: expected={case.expected!r}, actual={actual!r}"
                    )
        return count


def campaign(*, seed: int = 94721, iterations: int = 120,
             start: int = 0, source_free: bool = False, families: tuple[str, ...] = FAMILIES,
             graphs: tuple[str, ...] = GRAPHS, fail_fast: bool = False) -> dict[str, Any]:
    """Reproducibly sample the full feature-by-import-graph product."""
    if start < 0 or iterations < 0:
        raise ValueError("start and iterations must be non-negative")
    rng = random.Random(seed)
    for _ in range(start):
        rng.randrange(2**31)
    failures = []
    executions = 0
    successful = 0
    observed = set()
    for iteration in range(start, start + iterations):
        # Cover the Cartesian product before repeating any combination.
        pairs = [(f, g) for f in families for g in graphs]
        cycle, offset = divmod(iteration, len(pairs))
        permutation = list(pairs)
        random.Random(seed + cycle).shuffle(permutation)
        family, graph = permutation[offset]
        case_seed = rng.randrange(2**31)
        case = project_case(family, graph, case_seed)
        try:
            executions += execute_case(case, source_free=source_free)
        except Exception as exc:
            failures.append({"case": case.identity, "error": repr(exc),
                             "modules": dict(case.modules), "consumer": case.consumer,
                             "expected": list(case.expected),
                             "build_order": list(case.build_order)})
            if fail_fast:
                break
        else:
            successful += 1
            observed.add(f"{family}/{graph}")
    return {"seed": seed, "start": start, "iterations_requested": iterations,
            "source_free": source_free, "successful_cases": successful,
            "failed_cases": len(failures), "executions": executions,
            "covered_feature_graph_pairs": len(observed),
            "possible_feature_graph_pairs": len(families) * len(graphs),
            "covered_pairs": sorted(observed),
            "failures": failures}


def batched_campaign(*, seed: int, start: int, iterations: int,
                     batch_size: int, batch_timeout: int, source_free: bool,
                     families: tuple[str, ...], graphs: tuple[str, ...],
                     fail_fast: bool) -> dict[str, Any]:
    """Run finite independent worker processes, retaining errors on timeout.

    A compiler failure or a slow graph must not destroy the report for all
    preceding projects. Each worker retains the original global seed/offset.
    """
    if batch_size <= 0 or batch_timeout <= 0:
        raise ValueError("batch size and timeout must be positive")
    completed = 0
    successful = 0
    executions = 0
    covered = set()
    failures: list[dict[str, Any]] = []
    for offset in range(start, start + iterations, batch_size):
        size = min(batch_size, start + iterations - offset)
        argv = [sys.executable, "-m", "tools.multimodule_fuzzing",
                "--seed", str(seed), "--start", str(offset),
                "--iterations", str(size), "--batch-size", "0"]
        if source_free:
            argv.append("--source-free")
        for family in families:
            argv.extend(("--family", family))
        for graph in graphs:
            argv.extend(("--graph", graph))
        try:
            worker = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=batch_timeout, check=False,
            )
            batch = json.loads(worker.stdout)
        except (subprocess.TimeoutExpired, ValueError) as exc:
            failures.append({"batch_start": offset, "batch_size": size,
                             "error": f"worker timed out or produced an invalid report: {exc}"})
        else:
            successful += batch["successful_cases"]
            executions += batch["executions"]
            covered.update(batch["covered_pairs"])
            failures.extend(batch["failures"])
            if worker.returncode and not batch["failures"]:
                failures.append({"batch_start": offset, "batch_size": size,
                                 "error": f"worker exited {worker.returncode}: {worker.stderr[-1000:]}"})
        completed += size
        if fail_fast and failures:
            break
    return {"seed": seed, "start": start, "iterations_requested": iterations,
            "iterations_attempted": completed, "source_free": source_free,
            "successful_cases": successful, "failed_cases": len(failures),
            "executions": executions, "covered_feature_graph_pairs": len(covered),
            "possible_feature_graph_pairs": len(families) * len(graphs),
            "covered_pairs": sorted(covered), "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=94721)
    parser.add_argument("--iterations", type=int, default=120)
    parser.add_argument("--start", type=int, default=0,
                        help="absolute generated-case index (replay a campaign slice)")
    parser.add_argument("--batch-size", type=int, default=30,
                        help="run long campaigns in isolated subprocess chunks (0 disables)")
    parser.add_argument("--batch-timeout", type=int, default=45,
                        help="seconds per worker chunk, never silently ignored")
    parser.add_argument("--source-free", action="store_true")
    parser.add_argument("--family", choices=FAMILIES, action="append")
    parser.add_argument("--graph", choices=GRAPHS, action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--replay-case", metavar="FAMILY/GRAPH/SEED",
                        help="run a specific failing generated case in all four modes")
    args = parser.parse_args(argv)
    if args.replay_case:
        family, graph, seed_string = args.replay_case.split("/")
        case = project_case(family, graph, int(seed_string))
        try:
            count = execute_case(case, source_free=args.source_free)
        except Exception as exc:
            print(f"FAIL {case.identity}: {exc}")
            return 1
        print(f"PASS {case.identity}: {count} execution modes")
        return 0
    parameters = dict(seed=args.seed, start=args.start, iterations=args.iterations,
                      source_free=args.source_free,
                      families=tuple(args.family or FAMILIES),
                      graphs=tuple(args.graph or GRAPHS), fail_fast=args.fail_fast)
    result = (
        batched_campaign(**parameters, batch_size=args.batch_size,
                         batch_timeout=args.batch_timeout)
        if args.batch_size and args.iterations > args.batch_size
        else campaign(**parameters)
    )
    output = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return int(bool(result["failed_cases"]))


if __name__ == "__main__":
    sys.exit(main())
