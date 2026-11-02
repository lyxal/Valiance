"""Seeded, cross-phase generics invariants beyond the numbered test inventory.

Every positive source fixture is compiled with and without optimisation, and
executed before and after bytecode serialization. Inputs, expected values and
random seeds are kept here so any failure can be reduced to a single source.
The original 351-case coverage registry intentionally does not count these
additional tests as new inventory IDs.
"""

from itertools import permutations
import random

from trait_plan_support import SourceCase, TraitPlanTestCase


SEED = 20261102


def nested(value: str, rank: int) -> str:
    """A single-element regular list with exactly ``rank`` dimensions."""
    return "[" * rank + value + "]" * rank


def nested_value(value, rank: int):
    for _ in range(rank):
        value = [value]
    return value


class GenericsAdversarialTests(TraitPlanTestCase):
    def assert_source(self, name, source, stack=None, *, typ=None, reject=()):
        # Include source in subtest output: a failure is directly reproducible.
        with self.subTest(case=name, source=source):
            self.check_case(SourceCase(name, source, stack=stack, typ=typ,
                                       reject=reject))

    def test_seeded_where_rank_and_identity_matrix(self):
        """Reification and computed rank survive specialization/serialization."""
        rng = random.Random(SEED)
        choices = [(rank, leaf) for rank in range(1, 7)
                   for leaf in ("1", '"s"')]
        rng.shuffle(choices)
        for rank, leaf in choices:
            value = 1 if leaf == "1" else "s"
            literal = nested(leaf, rank)
            self.assert_source(
                f"rank-{rank}-{leaf}",
                "define[T] depth(xs:T+$n)->Number where ($result=$n)=>$result end\n"
                f"{literal} depth", [rank], typ="Number",
            )
            self.assert_source(
                f"identity-{rank}-{leaf}",
                "define[T] whole(xs:T+ novec)->T+=>$xs end\n"
                f"{literal} whole", [nested_value(value, rank)],
            )

    def test_where_overload_backtracking_is_order_independent(self):
        """A rejected `where` candidate must not poison fallback inference."""
        specialized = ('define[T] choose(xs:T+$n)->String '
                       'where ($n 2 == ?) => "two" end')
        fallback = ('define[T] choose(xs:T novec)->String '
                    '=> "fallback" end')
        for order in permutations((specialized, fallback)):
            for rank in range(1, 6):
                self.assert_source(
                    f"where-overload-{rank}",
                    "\n".join(order) + "\n" + nested("1", rank) + " choose",
                    ["two" if rank == 2 else "fallback"],
                )

    def test_where_function_type_tuple_equality(self):
        """Compare real type tuples, including unequal output type evidence."""
        definition = ('define[T,U] sameSignature(f:Function[T->U])->Number '
                      'where ($result=$f.inputs $f.outputs ==)=>$result end\n')
        for arg, result, expression, expected in (
            ("Int", "Int", "$x", 1),
            ("String", "String", "$x", 1),
            ("Int", "String", '"new"', 0),
        ):
            self.assert_source(
                f"type-tuples-{arg}-{result}",
                definition + (f'sameSignature(fn(x:{arg})->{result} '
                              f'=>{expression} end)'),
                [expected],
            )

    def test_implicit_some_and_none_union_dispatch_with_static_values(self):
        """Neither inferred Some nor explicit Some may lose hidden `where` values."""
        rng = random.Random(SEED + 1)
        definitions = ('define[T] kind(x:Some[T])->Number '
                       'where ($n=11)=>$n end\n'
                       'define kind(x:None)->Number '
                       'where ($n=0)=>$n end\n')
        samples = (("1 as[Int?]", 11), ("Some(2)", 11),
                   ("\\None", 0), ("3 as[Int?]", 11),
                   ("Some(4)", 11))
        for iteration in range(14):
            selection = list(samples)
            rng.shuffle(selection)
            # Deliberately include empty/present transitions and repeated cases.
            selection = selection[:2 + iteration % 4]
            elements = ", ".join(expression for expression, _ in selection)
            expected = [value for _, value in selection]
            self.assert_source(
                f"implicit-some-permutation-{iteration}",
                definitions + f"[{elements}] map: kind", [expected],
            )

    def test_numeric_promotion_and_union_inference_are_commutative(self):
        """Evidence order changes the returned value, never the solved type."""
        declaration = 'define[T] first(a:T,b:T)->T=>$a end\n'
        pairs = (("10", "2.5", 10, "Real"),
                 ("2.5", "10", 2.5, "Real"),
                 ('"hello"', "10", "hello", "Int | String"),
                 ("10", '"hello"', 10, "Int | String"))
        for repeat in range(3):
            for left, right, value, typ in pairs:
                self.assert_source(
                    f"unification-{repeat}-{left}-{right}",
                    declaration + f"first({left},{right})",
                    [value], typ=typ,
                )
        # Alternating instantiations must not reuse the previous solution.
        expressions = ("first(10,2.5)", 'first("a",3)',
                       "first(2.5,10)", 'first(4,"z")')
        for order in permutations(expressions):
            results = {expressions[0]: 10, expressions[1]: "a",
                       expressions[2]: 2.5, expressions[3]: 4}
            self.assert_source(
                "cross-instantiation-" + str(order),
                declaration + "\n".join(order),
                [results[expression] for expression in order],
            )

    def test_rank_subtyping_never_treats_minimum_or_rugged_as_uniform(self):
        """Rank annotations cannot be stripped to satisfy a T+ generic."""
        fn = 'define[T] strictlyUniform(xs:T+ novec)->T+=>$xs end\n'
        for rank in range(1, 5):
            literal = nested("1", rank)
            self.assert_source(
                f"regular-{rank}", fn + f"strictlyUniform({literal})",
                [nested_value(1, rank)],
            )
            for qualifier in ("*", "~"):
                self.assert_source(
                    f"nonuniform-{rank}-{qualifier}",
                    fn + f'$x:Number{qualifier}{rank}={literal}\nstrictlyUniform($x)',
                    reject=("no overloads",),
                )

    def test_hidden_where_values_remain_scalar_under_vectorisation(self):
        """Vector call metadata covers user arguments, not appended static inputs."""
        definition = ('define inc(x:Int)->Number '
                      'where ($offset=3)=>$x+$offset end\n')
        self.assert_source("empty-vector", definition + '[] as[Int+] inc', [[]])
        for rank in range(1, 5):
            self.assert_source(
                f"static-where-vector-rank-{rank}",
                definition + nested("1", rank) + " inc",
                [nested_value(4, rank)],
            )
        for length in range(1, 6):
            left = [n + 1 for n in range(length)]
            right = [n + 2 for n in range(length)]
            source = ('define combine(x:Int,y:Int)->Number '
                      'where ($offset=1)=>$x+$y+$offset end\n'
                      + "[" + ",".join(map(str, left)) + "] "
                      + "[" + ",".join(map(str, right)) + "] combine")
            self.assert_source(
                f"static-where-paired-lists-{length}", source,
                [[a + b + 1 for a, b in zip(left, right, strict=True)]],
            )
        for left, right in (("[1,2]", "[3]"), ("[1]", "[2,3]")):
            with self.subTest(left=left, right=right, oracle="runtime-fault"):
                self.check_case(SourceCase(
                    "static-where-length-mismatch",
                    'define combine(x:Int,y:Int)->Number '
                    'where ($offset=1)=>$x+$y+$offset end\n'
                    f'{left} {right} combine',
                    fault="VectorisationFault",
                ))

    def test_hidden_where_values_survive_import_and_overload_selection(self):
        """The same call policy must work for imported and overloaded elements."""
        source = 'import {arithmetic.inc}\n[1,2] inc'
        with self.subTest(imported=True):
            self.check_case(SourceCase(
                "where-import",
                source,
                stack=[[4, 5]],
                modules={"arithmetic.vlnc":
                         'public define inc(x:Int)->Number '
                         'where ($offset=3)=>$x+$offset end'},
            ))
        self.assert_source(
            "where-overloaded",
            'define inc(x:Int)->Number '
            'where ($offset=3)=>$x+$offset end\n'
            'define inc(x:String)->String=>"s" end\n'
            '[1,2,3] inc', [[4, 5, 6]],
        )

    def test_equivalent_generic_overloads_replace_in_source_order(self):
        """Alpha-renamed equivalent definitions use last-definition-wins."""
        unrelated = 'define side(x:String)->String=>$x end\n'
        for first, second in (("T", "U"), ("Alpha", "Beta")):
            for swapped in (False, True):
                a, b = ("original", "replacement")
                if swapped:
                    a, b = b, a
                source = (
                    f'define[{first}] label(x:{first})->String=>"{a}" end\n'
                    + unrelated
                    + f'define[{second}] label(x:{second})->String=>"{b}" end\n'
                    + 'label(10)\nlabel("word")\nside("hello")'
                )
                self.assert_source(
                    f"later-definition-{first}-{second}-{swapped}",
                    source, [b, b, "hello"],
                )
