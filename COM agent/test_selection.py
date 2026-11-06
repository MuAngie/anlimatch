import json
import subprocess
import unittest
from unittest.mock import patch
from generator import generate_output, select_products
from engine import demo_catalog
from server import archived_products
from matching import source_fields
from model_client import ModelClient
from test_engine import case
from test_generator import FakeClient, proposal
from test_evaluator import response


class SelectionTests(unittest.TestCase):
    def test_confirmed_three_product_warning_draft_keeps_uncovered_goal_and_quote(self):
        c, catalog = case(), demo_catalog()
        c['budget'] = None
        catalog[2]['goals'] = ['蛋白质补充']
        c['goal_context'] = {'goals':[{'name':g,'catalog_goal':g,'evidence':c['text']}
            for g in ('蛋白质补充','补充维生素B6','胰岛素抵抗管理')], 'cautions':[], 'missing':[]}
        for empty in (False, True):
            generated = proposal()
            generated.update(status='needs_review',summary='缺少膳食评估，三款合用必要性和安全性尚待确认。')
            generated['products'] = [] if empty else generated['products'] + [
                {'catalog_id':'DEMO-D','rationale':'仅演示同目标组合，合用依据待确认。'}]
            client = FakeClient(generated,evaluated=response('unknown'))
            selected = select_products(c,catalog,client)
            result = generate_output(c,catalog,client,selection=selected,confirmed_products=['DEMO-A','DEMO-C','DEMO-D'])
            self.assertEqual(result['evaluation']['verdict'],'needs_review')
            self.assertEqual({p['id'] for p in result['report']['selected_products']},{'DEMO-A','DEMO-C','DEMO-D'})
            self.assertEqual(result['report']['pricing']['total'],313)
            self.assertEqual(result['report']['uncovered_goals'],['胰岛素抵抗管理'])
            self.assertIn('缺少膳食评估',result['markdown'])
            self.assertIn('未覆盖目标：胰岛素抵抗管理',result['markdown'])
            self.assertEqual([call[2] for call in client.calls],['matching_schema.json','compose_schema.json','eval_schema.json'])

    def test_selection_stops_before_composition_and_confirmation_reuses_it(self):
        client = FakeClient()
        selected = select_products(case(),demo_catalog(),client)
        self.assertEqual(selected['status'],'ready')
        self.assertEqual([c[2] for c in client.calls],['matching_schema.json'])
        result = generate_output(case(),demo_catalog(),client,selection=selected,confirmed_products=['DEMO-A','DEMO-C'])
        self.assertEqual(result['evaluation']['verdict'],'pass')
        self.assertEqual([c[2] for c in client.calls],['matching_schema.json','compose_schema.json','eval_schema.json'])
        self.assertEqual(result['report']['pricing']['total'],217)

    def test_unverified_or_uncertain_products_cannot_be_confirmed(self):
        products = demo_catalog(); products[0]['label_reviewed'] = False
        client = FakeClient(); selected = select_products(case(),products,client)
        self.assertFalse(selected['products'][0]['eligible'])
        result = generate_output(case(),products,client,selection=selected,confirmed_products=['DEMO-A'])
        self.assertEqual(result['evaluation']['verdict'],'block')
        self.assertEqual(len(client.calls),1)

    def test_confirmed_product_set_cannot_be_silently_changed(self):
        client = FakeClient(); selected = select_products(case(),demo_catalog(),client)
        result = generate_output(case(),demo_catalog(),client,selection=selected,confirmed_products=['DEMO-A'])
        self.assertIsNone(result['report'])
        self.assertNotIn('eval_schema.json',[c[2] for c in client.calls])

    def test_timeout_keeps_the_selection_stage_separate(self):
        client = FakeClient()
        with patch.object(client,'call',side_effect=subprocess.TimeoutExpired('test',300)):
            result = select_products(case(),demo_catalog(),client)
        self.assertEqual(result['status'],'error')
        self.assertIn('选品调用超时',result['summary'])
        self.assertEqual(result['products'],[])

    def test_archive_compaction_keeps_warnings_and_historical_qualifications(self):
        products = archived_products()
        chars = sum(len(json.dumps(source_fields(p),ensure_ascii=False)) for p in products)
        self.assertLess(chars,40000)
        protein = next(p for p in products if p['sku']=='P002')
        fields = source_fields(protein)
        self.assertIn('历史',fields['ingredients_raw'])
        self.assertIn('少年儿童',fields['raw.不适宜人群'])
        fish = next(p for p in products if p['sku']=='AMW-CV-001')
        self.assertIn('抗凝',source_fields(fish)['raw.warnings'])
        fish['evidence'].append('新增人工依据，必须保留')
        self.assertIn('新增人工依据，必须保留',source_fields(fish)['evidence'])

    def test_matching_has_a_scoped_budget_without_changing_final_review(self):
        event = json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'{}'}})
        process = subprocess.CompletedProcess([],0,stdout=event,stderr='')
        with patch.dict('os.environ',{'OPENAI_BASE_URL':'','OPENAI_API_KEY':'','OPENAI_MODEL':''}), patch('model_client.codex_runtime',return_value=('codex','test-model','xhigh')), patch('model_client.subprocess.run',return_value=process) as run:
            client = ModelClient(); client.call('test',{},'matching_schema.json')
            args,kwargs = run.call_args
            self.assertIn('model_reasoning_effort="medium"',args[0])
            self.assertEqual(kwargs['timeout'],300)
            client.call('test',{},'eval_schema.json')
            args,kwargs = run.call_args
            self.assertIn('model_reasoning_effort="xhigh"',args[0])
            self.assertEqual(kwargs['timeout'],180)


if __name__ == '__main__':
    unittest.main()
