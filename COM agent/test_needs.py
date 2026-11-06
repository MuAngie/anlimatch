import copy
import subprocess
import unittest

from engine import GOALS, demo_catalog
from evaluator import SLOT
from generator import generate_output
from needs import extract_needs, validate_needs
from test_engine import case
from test_generator import FakeClient


TEXT = "专业评估确认日常蛋白质和维生素B6摄入不足，建议补充蛋白质及维生素B6。"


def extracted():
    return {"status":"ready", "summary":"原专业评估确认两项补充需求", "goals":[
        {"name":"蛋白质补充", "evidence":"建议补充蛋白质及维生素B6", "catalog_goal":"蛋白质补充", "kind":"supplement_need"},
        {"name":"补充维生素B6", "evidence":"建议补充蛋白质及维生素B6", "catalog_goal":"补充维生素B6", "kind":"supplement_need"}],
        "cautions":[], "missing":[]}


class NeedsClient:
    model = "test-model"
    source = "测试模型"

    def call(self, prompt, data, schema):
        self.data, self.schema = data, schema
        return extracted(), self.model


class NeedsTests(unittest.TestCase):
    def test_free_format_analysis_connects_to_selection_with_original_evidence(self):
        c = case(); c["text"] = TEXT
        client = NeedsClient()
        needs = extract_needs(c, demo_catalog(), client)
        self.assertEqual(client.schema,"needs_schema.json")
        self.assertNotIn("id",client.data["profile"])
        self.assertEqual(client.data["analysis"],TEXT)
        self.assertEqual(client.data['standard_goals'],sorted(GOALS))
        self.assertEqual(set(client.data['available_goals']),set(client.data['standard_goals']) | set(client.data['catalog_goals']))
        c["goal_context"] = needs
        result = generate_output(c,demo_catalog(),FakeClient())
        self.assertEqual(result["evaluation"]["verdict"],"pass")
        self.assertEqual(result["report"]["pricing"]["total"],217)
        self.assertEqual(result["report"]["analysis"]["summary"],TEXT)
        self.assertIn("需求提取与确认依据",result["markdown"])
        self.assertIn("建议补充蛋白质及维生素B6",result["markdown"])

    def test_free_catalog_tag_stays_distinct_from_standard_goals(self):
        products = demo_catalog()
        products[0]['goals'].append('自定义蛋白支持方向')
        client, c = NeedsClient(), case()
        c['text'] = TEXT
        extract_needs(c,products,client)
        self.assertIn('自定义蛋白支持方向',client.data['catalog_goals'])
        self.assertNotIn('自定义蛋白支持方向',client.data['standard_goals'])
        self.assertIn('自定义蛋白支持方向',client.data['available_goals'])

    def test_unmatched_goal_is_not_changed_to_sell_a_product(self):
        c = case(); c["text"] = "专业评估已确认血糖管理需求。"
        needs = dict(extracted(),goals=[{"name":"血糖管理", "catalog_goal":"", "evidence":"已确认血糖管理需求"}])
        validate_needs(needs,c["text"],[])
        c["goal_context"] = needs
        c["profile"]["forbidden_products"] = ["DEMO-A"]
        client = FakeClient(); result = generate_output(c,demo_catalog(),client)
        self.assertEqual([c[2] for c in client.calls],["matching_schema.json"])
        self.assertIsNone(result["report"])
        self.assertEqual(result["evaluation"]["verdict"],"needs_review")
        self.assertIn("现有资料未支持",result["evaluation"]["summary"])

    def test_invalid_evidence_mapping_and_duplicate_goals_are_rejected(self):
        for change in ("evidence", "mapping", "duplicate", "empty"):
            with self.subTest(change=change):
                value = copy.deepcopy(extracted())
                if change == "evidence": value["goals"][0]["evidence"] = "原文没有这句话"
                elif change == "mapping": value["goals"][0]["catalog_goal"] = "不存在的产品目标"
                elif change == "duplicate": value["goals"].append(value["goals"][0])
                else: value["goals"] = []
                with self.assertRaises(ValueError):
                    validate_needs(value,TEXT,["蛋白质补充","补充维生素B6"])

    def test_timeout_releases_shared_model_slot(self):
        class TimeoutClient(NeedsClient):
            def call(self,*args): raise subprocess.TimeoutExpired("test",1)
        c = case(); c["text"] = TEXT
        with self.assertRaisesRegex(RuntimeError,"需求提取超时"):
            extract_needs(c,demo_catalog(),TimeoutClient())
        self.assertTrue(SLOT.acquire(blocking=False)); SLOT.release()


if __name__ == "__main__":
    unittest.main()
