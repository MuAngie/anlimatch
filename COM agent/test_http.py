"""Run against server.py on localhost; uses only the virtual case and product files."""
import base64
import json
import os
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_engine import case

URL = os.environ.get("HEALTH_TEST_URL", "http://127.0.0.1:8765")


def call(path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(URL + path, data=data, headers={"Content-Type":"application/json"})
    with urlopen(req, timeout=10) as response:
        return json.load(response)


class HttpTests(unittest.TestCase):
    def test_bootstrap_and_compose(self):
        data = call("/api/bootstrap")
        self.assertEqual(len(data["archive_catalog"]), 40)
        user_case = case()
        user_case["profile"]["name"] = "测试用户（虚构）"
        user_case["profile"]["forbidden_products"] = ["DEMO-A", "DEMO-C", "DEMO-D"]
        # Deterministic denial requires no external call, and cannot be bypassed by client flags.
        result = call("/api/compose", {"case":user_case, "catalog":data["demo_catalog"], "skip_eval":True})
        self.assertIsNone(result["report"])
        self.assertIsNone(result["markdown"])
        self.assertEqual(result["evaluation"]["verdict"], "block")

    def test_real_excel_import(self):
        path = next((Path(__file__).resolve().parent.parent / "archive").glob("*20款.xlsx"))
        data = call("/api/import", {"filename":path.name, "data":base64.b64encode(path.read_bytes()).decode()})
        self.assertEqual(len(data["products"]), 20)

    def test_catalog_backup_roundtrip(self):
        products = call("/api/bootstrap")["demo_catalog"]
        encoded = base64.b64encode(json.dumps(products, ensure_ascii=False).encode()).decode()
        self.assertEqual(call("/api/import", {"filename":"catalog.json", "data":encoded})["products"], products)

    def test_invalid_case_has_actionable_error(self):
        with self.assertRaises(HTTPError) as context:
            call("/api/compose", {"case":{"text":""}, "catalog":[]})
        self.assertEqual(context.exception.code, 400)
        self.assertIn("粘贴", json.load(context.exception)["error"])


if __name__ == "__main__":
    unittest.main()
