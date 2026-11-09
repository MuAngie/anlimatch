import copy
import io
import json
import unittest
from unittest.mock import patch

from engine import demo_catalog
from generator import generate_output
from model_client import ModelClient
from test_engine import case
from test_evaluator import response


def proposal():
    return {"status":"proposed", "summary":"虚构组合测试", "analysis_summary":"专业分析已明确蛋白质及维生素B6补充需求。", "products":[
        {"catalog_id":"DEMO-A", "rationale":"虚拟蛋白质补充依据"},
        {"catalog_id":"DEMO-C", "rationale":"虚拟B6补充依据"}]}


class FakeClient:
    model = "test-model"
    source = "测试模型"

    def __init__(self, generated=None, evaluated=None, matched=None):
        from matching import CACHE
        CACHE.clear()
        self.generated = generated if generated is not None else proposal()
        self.evaluated = evaluated if evaluated is not None else response()
        self.calls = []
        self.matched = matched

    def call(self, prompt, data, schema):
        self.calls.append((prompt, data, schema))
        if schema == "matching_schema.json":
            if self.matched is not None: return copy.deepcopy(self.matched),self.model
            products = []
            for p in data["catalog"]:
                indices = [i for i,g in enumerate(data["analysis"]["goals"]) if g in p["sources"]["goals"].splitlines()]
                if not indices: continue
                products.append({"catalog_id":p["catalog_id"],"status":"related" if indices else "unrelated",
                    "goal_indices":indices,"reason":"虚构关联判断", "evidence":[{"field":"goals","quote":data["analysis"]["goals"][indices[0]]}] if indices else []})
            return {"summary":"虚构产品关联结果","products":products},self.model
        return (self.generated if schema == "compose_schema.json" else self.evaluated), self.model


class GenerationTests(unittest.TestCase):
    def test_two_independent_prompts_use_the_same_client_and_model(self):
        client = FakeClient()
        result = generate_output(case(), demo_catalog(), client)
        self.assertEqual([c[2] for c in client.calls], ["matching_schema.json","compose_schema.json", "eval_schema.json"])
        self.assertNotEqual(client.calls[0][0], client.calls[1][0])
        self.assertEqual(result["generation"]["model"], result["evaluation"]["model"])
        self.assertEqual(result["report"]["pricing"]["total"], 217)
        self.assertIn("虚拟蛋白质补充依据", result["markdown"])

    def test_model_actually_selects_products_instead_of_rule_first_choice(self):
        catalog = demo_catalog()
        alternative = copy.deepcopy(catalog[0])
        alternative.update(id="DEMO-Z", sku="DEMO-Z", name="虚拟替代蛋白粉")
        alternative["price"].update(sku="DEMO-Z", amount=100)
        catalog.append(alternative)
        generated = proposal(); generated["products"][0]["catalog_id"] = "DEMO-Z"
        result = generate_output(case(), catalog, FakeClient(generated))
        self.assertEqual([p["id"] for p in result["report"]["selected_products"]], ["DEMO-C", "DEMO-Z"])
        self.assertEqual(result["report"]["pricing"]["total"], 189)
        self.assertFalse(any(p["id"] == "DEMO-Z" for p in result["report"]["excluded_products"]))

    def test_candidate_checks_do_not_block_a_valid_alternative_combination(self):
        c, catalog = case(), demo_catalog()
        alternative = copy.deepcopy(catalog[0])
        alternative.update(id="DEMO-Z", sku="DEMO-Z", name="虚拟低含量替代产品")
        alternative["ingredients"][0]["amount_per_unit"] = 2
        alternative["price"]["sku"] = "DEMO-Z"
        catalog.append(alternative)
        c["exposures"] = [{"name":"蛋白质", "unit":"g", "diet_amount":0, "supplement_amount":0,
                           "upper_limit":3, "limit_scope":"all", "limit_source":"仅用于虚构计算测试"}]
        c["adjustments"] = [{"catalog_id":"DEMO-A", "daily_units":0.5, "profile_id":c["profile"]["id"],
            "reason":"虚构测试", "source":"虚构记录", "reviewer":"虚构审核人", "reviewed_at":c["as_of"]}]
        generated = proposal(); generated["products"][0]["catalog_id"] = "DEMO-Z"
        result = generate_output(c, catalog, FakeClient(generated))
        self.assertIsNotNone(result["report"])
        self.assertEqual(next(n for n in result["report"]["nutrients"] if n["name"] == "蛋白质")["total"], 2)

    def test_hallucinated_forbidden_and_unapproved_dose_are_blocked_locally(self):
        for kind in ("unknown", "forbidden", "dose", "max"):
            c, generated, catalog = case(), proposal(), demo_catalog()
            if kind == "unknown": generated["products"][0]["catalog_id"] = "INVENTED"
            if kind == "forbidden": c["profile"]["forbidden_products"] = ["DEMO-A"]
            if kind == "dose": generated["products"][0]["daily_units"] = 0.5
            if kind == "max": generated["products"][0]["daily_units"] = 3
            client = FakeClient(generated)
            result = generate_output(c, catalog, client)
            self.assertIsNone(result["report"])
            self.assertEqual(result["evaluation"]["verdict"], "error" if kind in ('dose','max') else "block")
            self.assertEqual(len(client.calls), 2)

    def test_individual_adjustment_requires_original_user_record(self):
        c, generated = case(), proposal()
        c["adjustments"] = [{"catalog_id":"DEMO-A", "daily_units":0.5, "profile_id":c["profile"]["id"],
            "reason":"虚构测试", "source":"虚构记录", "reviewer":"虚构审核人", "reviewed_at":c["as_of"]}]
        result = generate_output(c, demo_catalog(), FakeClient(generated))
        self.assertEqual(result["report"]["selected_products"][0]["daily_units"], 0.5)
        c["adjustments"][0]["profile_id"] = "OTHER-USER"
        other = generate_output(c, demo_catalog(), FakeClient(generated))
        self.assertEqual(other['report']['selected_products'][0]['daily_units'],1)
        self.assertTrue(any('未应用调整' in s for s in other['report']['missing']))

    def test_uncovered_goals_keep_report_while_budget_and_failed_eval_block(self):
        generated = proposal(); generated["products"].pop()
        result = generate_output(case(), demo_catalog(), FakeClient(generated))
        self.assertIsNotNone(result["report"])
        self.assertEqual(result["report"]["uncovered_goals"], ["补充维生素B6"])
        self.assertTrue(any("未覆盖目标" in s for s in result["report"]["warnings"]))
        c = case(); c["budget"] = 100
        self.assertEqual(generate_output(c, demo_catalog(), FakeClient())["evaluation"]["verdict"], "block")
        result = generate_output(case(), demo_catalog(), FakeClient(evaluated=response("fail")))
        self.assertIsNone(result["report"])
        self.assertIsNone(result["markdown"])
        result = generate_output(case(), demo_catalog(), FakeClient(evaluated=response("unknown")))
        self.assertEqual(result["report"]["pricing"]["total"], 217)
        self.assertEqual(result["evaluation"]["verdict"], "needs_review")

    def test_generation_refusal_or_malformed_output_does_not_trigger_eval(self):
        for generated in ({"status":"needs_review", "summary":"需补充用药资料", "analysis_summary":"需核对背景", "products":[]},
                          {"status":"proposed", "summary":"错误格式", "analysis_summary":"测试", "products":[]},
                          {"status":"proposed", "summary":"产品重复", "analysis_summary":"测试", "products":proposal()["products"] * 2}):
            client = FakeClient(generated)
            result = generate_output(case(), demo_catalog(), client)
            self.assertIsNone(result["report"])
            self.assertEqual(len(client.calls), 2)

    def test_shared_api_config_snapshot_and_strict_schema(self):
        env = {"OPENAI_BASE_URL":"https://example.invalid/v1", "OPENAI_API_KEY":"test-only-secret", "OPENAI_MODEL":"test-api-model"}
        requests = []
        def transport(request, timeout):
            requests.append(request)
            schema = json.loads(request.data)["response_format"]["json_schema"]
            if schema["name"] == "matching_schema":
                value = FakeClient().call("",json.loads(json.loads(request.data)["messages"][1]["content"]),"matching_schema.json")[0]
            else:
                value = proposal() if schema["name"] == "compose_schema" else response()
            return io.BytesIO(json.dumps({"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(value)}}]}).encode())
        with patch.dict("os.environ", env), patch("model_client.urlopen", side_effect=transport):
            client = ModelClient()
            # Later environment edits cannot cause generation and evaluation to use different settings.
            with patch.dict("os.environ", {"OPENAI_MODEL":"changed-after-client-created"}):
                result = generate_output(case(), demo_catalog(), client)
        self.assertEqual(result["evaluation"]["verdict"], "pass")
        self.assertEqual(len(requests), 3)
        for request in requests:
            body = json.loads(request.data)
            self.assertEqual(body["model"], "test-api-model")
            self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
            self.assertTrue(body["response_format"]["json_schema"]["strict"])
            self.assertEqual(request.get_header("Authorization"), "Bearer test-only-secret")

    def test_partial_api_configuration_does_not_fallback_to_codex(self):
        with patch.dict("os.environ", {"OPENAI_BASE_URL":"", "OPENAI_API_KEY":"test-only", "OPENAI_MODEL":""}):
            with self.assertRaisesRegex(RuntimeError, "不完整"):
                ModelClient()


if __name__ == "__main__":
    unittest.main()
