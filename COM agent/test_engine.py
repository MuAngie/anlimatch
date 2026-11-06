import unittest
from pathlib import Path
from engine import DEMO_TEXT, TODAY, compose, demo_catalog, extract, markdown, read_excel


def case():
    return {"text": DEMO_TEXT, "as_of": TODAY(), "days": 30, "budget": 300,
            "reviewer": "虚拟示例", "reviewed_at": TODAY(), "analysis_approved": True, "risk": "cleared",
            "profile": {"id": "demo", "age": 42, "pregnancy": "none", "allergies": [], "conditions": [], "medications": [], "current_supplements": []}}


class ComposerTests(unittest.TestCase):
    def test_demo_selection_and_pack_quote(self):
        r = compose(case(), demo_catalog())
        self.assertEqual([p["id"] for p in r["selected_products"]], ["DEMO-A", "DEMO-C"])
        self.assertEqual(r["pricing"]["total"], 217)
        self.assertEqual(r["selected_products"][1]["quote"]["remaining_units"], 30)
        self.assertEqual(r["selected_products"][1]["quote"]["consumption_cost"], 44.5)
        self.assertEqual(r["status"], "needs_review")

    def test_packages_round_up_at_boundary(self):
        c = case(); c["days"] = 61
        r = compose(c, demo_catalog())
        self.assertEqual([p["quote"]["packs"] for p in r["selected_products"]], [3, 2])
        self.assertEqual(r["pricing"]["total"], 562)

    def test_negative_goals_do_not_trigger(self):
        for text in ["营养目标：无需补钙；不建议补充维生素B6。", "营养目标：蛋白质摄入不足待确认。", "指标：钙偏低"]:
            self.assertEqual(extract(text)["goals"], [])

    def test_medical_first_suppresses_plan_and_quote(self):
        c = case(); c["risk"] = "medical_first"
        r = compose(c, demo_catalog())
        self.assertEqual(r["status"], "blocked")
        self.assertEqual(r["selected_products"], [])
        self.assertIsNone(r["pricing"]["total"])
        self.assertNotIn("1.2", markdown(r))

    def test_allergen_and_dose_conflicts_excluded(self):
        catalog = demo_catalog(); catalog[0]["allergens"] = ["大豆"]; catalog[1]["daily_units"] = 2
        c = case(); c["profile"]["allergies"] = ["大豆"]
        r = compose(c, catalog)
        self.assertEqual(r["selected_products"], [])
        self.assertTrue(any("过敏" in e["reason"] for e in r["excluded_products"]))
        self.assertTrue(any("超过标签" in e["reason"] for e in r["excluded_products"]))

    def test_unknown_and_zero_prices(self):
        catalog = demo_catalog(); catalog[0]["price"]["amount"] = None
        r = compose(case(), catalog)
        self.assertIsNone(r["pricing"]["total"])
        self.assertEqual(r["pricing"]["known_subtotal"], 89)
        catalog[0]["price"]["amount"] = 0
        self.assertEqual(compose(case(), catalog)["pricing"]["total"], 89)

    def test_expired_price_not_in_total(self):
        catalog = demo_catalog(); catalog[0]["price"]["valid_until"] = "2000-01-01"
        r = compose(case(), catalog)
        self.assertIsNone(r["pricing"]["total"])
        self.assertIn("过期", r["selected_products"][0]["quote"]["price_reason"])

    def test_nutrient_mass_conversion_and_upper_limit(self):
        c = case(); c["exposures"] = [{"name": "维生素B6", "unit": "µg", "diet_amount": 1000, "supplement_amount": 3000,
                                      "upper_limit": 4000, "limit_scope": "all", "limit_source": "虚拟测试上限，非临床阈值"}]
        r = compose(c, demo_catalog())
        self.assertEqual(r["status"], "blocked")
        self.assertTrue(any("5200" in reason for reason in r["blocked"]))
        self.assertIsNone(r["pricing"]["total"])

    def test_unknown_background_exposes_missing_fields(self):
        c = case(); c["profile"]["medications"] = None
        r = compose(c, demo_catalog())
        self.assertTrue(any("正在用药" in s for s in r["missing"]))
        self.assertTrue(all(n["total"] is None for n in r["nutrients"]))

    def test_known_contribution_can_block_even_when_diet_unknown(self):
        c = case(); c["exposures"] = [{"name":"维生素B6", "unit":"mg", "upper_limit":1,
                                      "limit_scope":"all", "limit_source":"虚拟测试阈值"}]
        self.assertEqual(compose(c, demo_catalog())["status"], "blocked")

    def test_explicit_medical_first_in_text_wins(self):
        c = case(); c["text"] += "\n风险结论：就医优先，暂停推荐。"
        self.assertEqual(compose(c, demo_catalog())["status"], "blocked")

    def test_missing_labels_stay_candidate_only(self):
        catalog = demo_catalog(); catalog[0]["label_source"] = None
        r = compose(case(), catalog)
        self.assertEqual([p["id"] for p in r["selected_products"]], ["DEMO-C"])
        self.assertIn("蛋白质补充", r["uncovered_goals"])

    def test_patient_adjustment_changes_consumption_not_catalog(self):
        catalog = demo_catalog(); c = case()
        c["adjustments"] = [{"catalog_id":"DEMO-A", "daily_units":0.5, "profile_id":"demo",
                             "reason":"虚拟用量调整测试", "source":"虚拟专业记录", "reviewer":"虚拟审核人", "reviewed_at":TODAY()}]
        r = compose(c, catalog)
        self.assertEqual(r["selected_products"][0]["daily_units"], 0.5)
        self.assertEqual(r["selected_products"][0]["quote"]["consumption_cost"], 64)
        self.assertEqual(catalog[0]["daily_units"], 1)
        self.assertIn("调整", r["selected_products"][0]["rationale"])

    def test_adjustment_without_patient_evidence_not_applied(self):
        c = case(); c["adjustments"] = [{"catalog_id":"DEMO-A", "daily_units":2}]
        r = compose(c, demo_catalog())
        self.assertEqual(r["selected_products"][0]["daily_units"], 1)
        self.assertTrue(any("未应用调整" in s for s in r["missing"]))

    def test_bad_financial_inputs_rejected(self):
        for changes in [{"days": 0}, {"days": 1.5}, {"discount": 999}, {"shipping": -1}, {"budget": "NaN"}]:
            c = case(); c.update(changes)
            with self.assertRaises(ValueError):
                compose(c, demo_catalog())

    def test_archive_read_only_and_complete(self):
        files = list((Path(__file__).resolve().parent.parent / "archive").glob("*.xlsx"))
        before = {p: p.read_bytes() for p in files}
        catalog = [p for f in files for p in read_excel(f)]
        self.assertEqual(len(catalog), 40)
        self.assertEqual(len({p["id"] for p in catalog}), 40)
        self.assertEqual(before, {p: p.read_bytes() for p in files})
        self.assertEqual(compose(case(), catalog)["selected_products"], [])

    def test_archive_fields_preserve_unknowns_and_historical_values(self):
        files = (Path(__file__).resolve().parent.parent / "archive").glob("*.xlsx")
        products = [p for file in files for p in read_excel(file)]
        self.assertTrue(all(p["goals"] and p["evidence"] and p["archive_info"] for p in products))
        self.assertTrue(all(not p["label_reviewed"] and not p["ingredients_complete"] for p in products))
        self.assertTrue(all(p["label_daily_max"] is None and not p["price"]["valid_until"] for p in products))
        protein = next(p for p in products if p["sku"] == "P002")
        self.assertEqual((protein["units_per_pack"], protein["unit"]), (400, "克"))
        self.assertIsNone(protein["daily_units"])
        self.assertEqual(protein["ingredients"], [])
        self.assertIn("历史资料", protein["archive_info"]["建议食用量原文"])
        multi = next(p for p in products if p["sku"] == "P001")
        self.assertIsNone(multi["units_per_pack"])
        self.assertIsNone(multi["contraindications"])

    def test_official_archive_amounts_keep_basis_and_units(self):
        files = (Path(__file__).resolve().parent.parent / "archive").glob("*.xlsx")
        products = [p for file in files for p in read_excel(file)]
        b = next(p for p in products if p["sku"] == "P003")
        self.assertEqual((b["daily_units"], b["unit"], b["units_per_pack"]), (1, "片", 300))
        self.assertEqual(len(b["ingredients"]), 8)
        folate = next(i for i in b["ingredients"] if i["name"] == "叶酸")
        self.assertEqual((folate["amount_per_unit"], folate["unit"]), (150, "µg"))
        calcium = next(p for p in products if p["sku"] == "P004")
        self.assertIsNone(calcium["daily_units"])
        self.assertEqual(calcium["ingredients"][0]["amount_per_unit"], 200)
        self.assertEqual(calcium["ingredients"][0]["source_basis"], "每3片")
        fish = next(p for p in products if p["sku"] == "AMW-CV-001")
        self.assertEqual(fish["ingredients"][0]["name"], "EPA+DHA")
        self.assertEqual(fish["ingredients"][0]["amount_per_unit"], 345)
        self.assertTrue(fish["allergens"] == ["鱼类"])


if __name__ == "__main__":
    unittest.main()
