from copy import deepcopy
import unittest

from engine import compose, demo_catalog
from report_editor import edit_report
from test_engine import case
from test_evaluator import response


class ReportEditorTests(unittest.TestCase):
    def setUp(self):
        self.case = case()
        self.catalog = demo_catalog()
        self.original = compose(self.case, self.catalog)
        self.changes = {"summary":"虚拟示例：明确蛋白质和B6补充目标。", "lifestyle":["保持规律饮食。"],
                        "follow_up":["按原专业计划复评。"],
                        "rationales":{p["id"]:p["rationale"] for p in self.original["selected_products"]}}

    def test_narrative_edit_preserves_calculation_and_original_analysis(self):
        inputs = deepcopy(self.original)
        seen = []
        output = edit_report(self.case, self.catalog, self.original, self.changes,
                             reviewer=lambda data:(seen.append(data) or response(), "test-model"))
        report = output["report"]
        self.assertEqual(report["pricing"], inputs["pricing"])
        self.assertEqual(report["nutrients"], inputs["nutrients"])
        self.assertEqual(report["days"], inputs["days"])
        self.assertEqual([(p["id"],p["daily_units"],p["quote"]) for p in report["selected_products"]],
                         [(p["id"],p["daily_units"],p["quote"]) for p in inputs["selected_products"]])
        self.assertEqual(self.original, inputs)
        self.assertEqual(seen[0]["draft"]["analysis"]["original_summary"], self.case["text"])
        self.assertEqual(report["analysis"]["summary"], self.changes["summary"])
        self.assertIn(self.changes["summary"], output["markdown"])
        self.assertIn(self.case["text"], output["markdown"])
        self.assertIn("人工修改", output["markdown"])

    def test_numeric_or_product_changes_cannot_be_smuggled_in(self):
        for key in ("days", "products", "pricing", "evaluation"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                edit_report(self.case, self.catalog, self.original, dict(self.changes, **{key:0}))
        with self.assertRaises(ValueError):
            edit_report(self.case, self.catalog, self.original, dict(self.changes, rationales={"unknown":"依据"}))

    def test_warning_edit_keeps_calculations_and_replaces_old_review_reminders(self):
        self.original['warnings'] = ['旧复核提醒']
        output = edit_report(self.case, self.catalog, self.original, self.changes,
                             reviewer=lambda data:(response('unknown'), 'test-model'))
        self.assertEqual(output['evaluation']['verdict'], 'needs_review')
        self.assertEqual(output['report']['pricing'], self.original['pricing'])
        self.assertNotIn('旧复核提醒', output['report']['warnings'])
        self.assertIn('结果：有提醒，待确认', output['markdown'])

    def test_failed_review_does_not_output_or_mutate_original(self):
        original = deepcopy(self.original)
        output = edit_report(self.case, self.catalog, self.original, self.changes,
                             reviewer=lambda data:(response("fail"), "test-model"))
        self.assertIsNone(output["report"])
        self.assertIsNone(output["markdown"])
        self.assertEqual(self.original, original)


if __name__ == "__main__":
    unittest.main()
