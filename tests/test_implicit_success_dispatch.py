"""Optional/Result branches use implicit Some/OK value representation.

Only None is absence, and only Err witnesses are error results.  Ordinary
values are not wrapped at runtime when widened into these sum types.
"""

import unittest

from trait_plan_support import SourceCase, TraitPlanTestCase
from valiance.runtime.runtime_values import ObjectValue, RuntimeNumber
from valiance.runtime.vm import _parse_runtime_type_pattern, _runtime_pattern_matches


class ImplicitSuccessDispatchTests(TraitPlanTestCase):
    def test_optional_union_dispatch_accepts_implicit_and_explicit_some(self):
        self.check_case(SourceCase(
            "UC-008",
            'define[T] label(value: Some[T]) -> String => "present" end\n'
            'define label(value: None) -> String => "missing" end\n'
            '[1 as[Int?], Some(2), \\None] map: label',
            [["present", "present", "missing"]],
        ))

    def test_nested_some_none_remains_present(self):
        self.check_case(SourceCase(
            "UC-008",
            'define label(value: Some[None]) -> String => "nested" end\n'
            'define label(value: None) -> String => "missing" end\n'
            '[Some(\\None), \\None] map: label',
            [["nested", "missing"]],
        ))

    def test_result_success_ascriptions_need_no_ok_wrapper(self):
        self.check_case(SourceCase(
            "EC-006",
            'define label(value: OK[Int]) -> String => "success" end\n'
            'label(1 as[OK[Int]])\nlabel(OK(2))',
            ["success", "success"],
        ))

    def test_runtime_predicates_distinguish_absence_error_and_wrappers(self):
        number = RuntimeNumber("3")
        none = ObjectValue("None", {})
        err = ObjectValue("ValueError", {})
        some = ObjectValue("Some", {"value": number}, ("Int",))
        wrapped_none = ObjectValue("Some", {"value": none}, ("None",))
        ok = ObjectValue("OK", {"value": number}, ("Int",))

        cases = (
            ("Some[Int]", number, True),
            ("Some[Int]", some, True),
            ("Some[Int]", none, False),
            ("Some[Int]", "text", False),
            ("Some[None]", wrapped_none, True),
            ("Some[None]", none, False),
            ("OK[Int]", number, True),
            ("OK[Int]", ok, True),
            ("OK[Int]", err, False),
            ("OK[Int]", "text", False),
            ("OK[None]", none, True),
            ("Some[OK[Int]]", number, True),
        )
        for signature, value, expected in cases:
            with self.subTest(signature=signature, value=repr(value)):
                self.assertIs(
                    _runtime_pattern_matches(value, _parse_runtime_type_pattern(signature)),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
