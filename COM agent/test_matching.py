import copy
import unittest

from engine import demo_catalog
from generator import generate_output
from test_engine import case
from test_generator import FakeClient


def semantic_catalog():
    products = demo_catalog()[:2]
    descriptions = ["虚构资料：用于日常蛋白质补充。","虚构资料：用于补充维生素B6。"]
    for product,description in zip(products,descriptions):
        product["goals"] = []
        product["raw"] = {"product_description":description}
    return products


def matching(products):
    return {"summary":"根据所给功能描述关联，不按目标名称筛选", "products":[
        {"catalog_id":p["id"],"status":"related","goal_indices":[i],"reason":"虚构产品资料支持本次目标",
         "evidence":[{"field":"raw.product_description","quote":p["raw"]["product_description"]}]}
        for i,p in enumerate(products)]}


class MatchingTests(unittest.TestCase):
    def test_products_without_matching_tags_reach_compose_and_review_with_original_sources(self):
        products = semantic_catalog(); client = FakeClient(matched=matching(products))
        result = generate_output(case(),products,client)
        self.assertEqual(result["evaluation"]["verdict"],"pass")
        self.assertEqual(result["report"]["pricing"]["total"],217)
        self.assertEqual([c[2] for c in client.calls],["matching_schema.json","compose_schema.json","eval_schema.json"])
        self.assertEqual(client.calls[2][1]["original_labels"][0]["goals"],[])
        self.assertEqual(client.calls[2][1]['original_labels'][0]['sources']['raw.product_description'], products[0]['raw']['product_description'])
        self.assertNotIn('original_sources',client.calls[2][1]['draft']['matching'])
        self.assertEqual(products[0]["goals"],[])
        self.assertEqual(result["report"]["selected_products"][0]["matched_goals"],["蛋白质补充"])

    def test_related_but_unverified_products_are_listed_without_a_plan(self):
        products = semantic_catalog()
        for p in products: p["label_reviewed"] = False
        client = FakeClient(matched=matching(products))
        result = generate_output(case(),products,client)
        self.assertIsNone(result["report"])
        self.assertEqual(len(client.calls),1)
        self.assertEqual(result["matching"]["products"][0]["status"],"related")
        self.assertTrue(result["matching"]["products"][0]["missing"])
        self.assertIn("有关联产品",result["evaluation"]["summary"])

    def test_uncertain_relevance_does_not_become_eligible(self):
        products = semantic_catalog(); value = matching(products)
        for p in value["products"]: p["status"] = "uncertain"
        client = FakeClient(matched=value)
        result = generate_output(case(),products,client)
        self.assertIsNone(result["report"])
        self.assertEqual(len(client.calls),1)
        self.assertEqual(result["evaluation"]["verdict"],"needs_review")

    def test_fabricated_quotes_ids_goal_indices_and_excessive_candidates_are_rejected(self):
        products = semantic_catalog()
        for kind in ("quote","id","index","duplicate","too_many"):
            with self.subTest(kind=kind):
                value = matching(products)
                if kind=="quote": value["products"][0]["evidence"][0]["quote"] = "原资料不存在"
                elif kind=="id": value["products"][0]["catalog_id"] = "INVENTED"
                elif kind=="index": value["products"][0]["goal_indices"] = [True]
                elif kind=="duplicate": value["products"].append(copy.deepcopy(value["products"][0]))
                else: value["products"] *= 6
                client = FakeClient(matched=value)
                result = generate_output(case(),products,client)
                self.assertIsNone(result["report"])
                self.assertEqual(result["evaluation"]["verdict"],"error")
                self.assertEqual(len(client.calls),1)

    def test_forbidden_products_are_removed_before_semantic_assessment(self):
        products = semantic_catalog(); c = case()
        c["profile"]["forbidden_products"] = ["DEMO-A"]
        value = matching(products); value["products"] = value["products"][1:]
        client = FakeClient(matched=value)
        result = generate_output(c,products,client)
        self.assertNotIn("DEMO-A",[p["catalog_id"] for p in client.calls[0][1]["catalog"]])
        self.assertIsNone(result["report"])
        self.assertEqual(result["evaluation"]["verdict"],"block")

    def test_forty_products_use_one_batch_and_repeat_reuses_only_matching(self):
        products = semantic_catalog()
        for i in range(38):
            extra = copy.deepcopy(products[0]); extra["id"] = extra["sku"] = "EXTRA-"+str(i)
            products.append(extra)
        client = FakeClient(matched=matching(products[:2]))
        first = generate_output(case(),products,client)
        self.assertEqual(len(client.calls[0][1]["catalog"]),40)
        self.assertEqual([c[2] for c in client.calls],["matching_schema.json","compose_schema.json","eval_schema.json"])
        self.assertEqual(sum(p["status"]=="related" for p in first["matching"]["products"]),2)
        self.assertEqual(sum(p["status"]=="not_retrieved" for p in first["matching"]["products"]),38)
        second = generate_output(case(),products,client)
        self.assertTrue(second["matching"]["cached"])
        self.assertEqual([c[2] for c in client.calls[3:]],["compose_schema.json","eval_schema.json"])
        products[0]["raw"]["product_description"] += " 资料更新"
        third = generate_output(case(),products,client)
        self.assertFalse(third["matching"]["cached"])
        self.assertEqual(client.calls[5][2],"matching_schema.json")


if __name__=="__main__":
    unittest.main()
