from pathlib import Path
import unittest

from archive_demo import complete_demo_catalog
from engine import read_excel, product_checks, quote, require_date, TODAY
from generator import select_products, generate_output
from matching import source_fields
from server import archived_products
from test_engine import case
from test_generator import FakeClient


class ArchiveDemoTests(unittest.TestCase):
    def test_all_forty_records_are_complete_for_demo_without_claiming_real_review(self):
        files = list((Path(__file__).parent.parent/'archive').glob('*.xlsx'))
        before = {p:p.read_bytes() for p in files}
        raw = [p for file in files for p in read_excel(file)]
        products = complete_demo_catalog(raw)
        self.assertEqual(len(products),40)
        self.assertEqual({p['id'] for p in products},{p['id'] for p in raw})
        for original,p in zip(raw,products):
            self.assertEqual(p['raw'],original['raw'])
            self.assertEqual(p['name'],original['name'])
            self.assertTrue(p['demo'] and p['demo_ready'])
            self.assertFalse(p['label_reviewed'] or p['ingredients_complete'])
            self.assertEqual(product_checks(p,case()['profile'],require_date(TODAY(),'date')),( [], [] ))
            self.assertIsNotNone(quote(p,30,require_date(TODAY(),'date'))['purchase_cost'])
        self.assertTrue(all(not p['demo'] for p in raw))
        self.assertEqual(before,{p:p.read_bytes() for p in files})
        b = next(p for p in products if p['sku']=='P003')
        original_b = next(p for p in raw if p['sku']=='P003')
        self.assertEqual(b['ingredients'],original_b['ingredients'])
        self.assertEqual(complete_demo_catalog(products),products)

    def test_demo_still_checks_dose_allergy_and_forbidden_products(self):
        products = archived_products()
        protein = next(p for p in products if p['sku']=='P002')
        self.assertTrue(product_checks(protein,dict(case()['profile'],allergies=['大豆']),require_date(TODAY(),'date'))[1])
        protein['daily_units'] = protein['label_daily_max']+1
        self.assertTrue(product_checks(protein,case()['profile'],require_date(TODAY(),'date'))[1])
        c = case(); c['profile']['forbidden_products'] = [p['id'] for p in products]
        client = FakeClient()
        self.assertEqual(select_products(c,products,client)['products'],[])
        self.assertEqual(client.calls,[])

    def test_demo_selection_confirmation_and_full_report_use_completed_values(self):
        products = archived_products()
        chosen = [next(p for p in products if p['sku']==sku) for sku in ('P002','P003')]
        matched = {'summary':'虚构目标关联','products':[
            {'catalog_id':p['id'],'status':'related','goal_indices':[i],'reason':'演示成分支持对应需求',
             'evidence':[{'field':'ingredients','quote':source_fields(p)['ingredients']}]} for i,p in enumerate(chosen)]}
        proposed = {'status':'proposed','summary':'Demo组合','analysis_summary':'虚构蛋白质及B6补充需求。','products':[
            {'catalog_id':p['id'],'rationale':'仅按Demo演示参数计算'} for p in chosen]}
        client = FakeClient(matched=matched,generated=proposed)
        c = case(); c['budget'] = None
        selected = select_products(c,products,client)
        self.assertEqual(selected['status'],'ready')
        result = generate_output(c,products,client,selection=selected,confirmed_products=[p['id'] for p in chosen])
        self.assertEqual(result['evaluation']['verdict'],'pass')
        self.assertTrue(result['report']['demo'])
        self.assertEqual(result['report']['pricing']['total'],670)
        nutrients = {n['name']:n['product_amount'] for n in result['report']['nutrients']}
        self.assertEqual(nutrients['蛋白质'],8)
        self.assertEqual(nutrients['维生素B6'],1.2)
        self.assertEqual([call[2] for call in client.calls],['matching_schema.json','compose_schema.json','eval_schema.json'])
        self.assertIn('demo_assumptions',source_fields(chosen[0]))
        self.assertTrue(all(p['demo_ready'] for p in client.calls[-1][1]['original_labels']))


if __name__ == '__main__':
    unittest.main()
