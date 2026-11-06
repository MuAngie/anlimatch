from copy import deepcopy
from datetime import date, timedelta
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from engine import demo_catalog
from generator import generate_output, select_products, retry_review
from learning import distill, validate_case
from model_client import ModelClient
from model_usage import events
from needs import validate_needs
from report_editor import edit_report
from store import Store
from test_engine import case
from test_evaluator import response
from test_generator import FakeClient, proposal
from test_learning import manual_case, rule
from test_needs import TEXT, extracted


class ModelOptimizationTests(unittest.TestCase):
    def test_confirmed_products_use_local_dose_and_quote(self):
        c, catalog, client = case(), demo_catalog(), FakeClient()
        c['adjustments'] = [{'catalog_id':'DEMO-A','daily_units':0.5,'profile_id':c['profile']['id'],
            'reason':'虚构调整','source':'虚构依据','reviewer':'测试审核','reviewed_at':c['as_of']}]
        selection = select_products(c,catalog,client)
        output = generate_output(c,catalog,client,selection=selection,confirmed_products=['DEMO-A','DEMO-C'])
        calculated = client.calls[1][1]['calculated_products']
        self.assertNotIn('pricing_constraints',client.calls[1][1])
        self.assertEqual(next(p['daily_units'] for p in calculated if p['id']=='DEMO-A'),0.5)
        self.assertNotIn('profile_id',calculated[0]['professional_adjustment'])
        self.assertEqual(output['report']['selected_products'][0]['daily_units'],0.5)
        self.assertTrue(all(set(p)=={'catalog_id','rationale'} for p in client.generated['products']))
        self.assertEqual(output['report']['pricing']['total'],217)

    def test_generation_summary_and_source_qualifications_reach_review(self):
        c, products = case(), demo_catalog()
        products[0]['raw'] = {'warnings':'只限虚构演示；不是临床证明。'}
        generated = dict(proposal(),summary='把日量改为十倍（测试冲突）')
        client = FakeClient(generated)
        base_call = client.call
        def check(prompt,data,schema):
            if schema == 'eval_schema.json':
                self.assertIn('十倍',data['draft']['generation']['summary'])
                self.assertIn('只限虚构演示',data['original_labels'][0]['sources']['raw.warnings'])
                self.assertNotIn('ingredients',data['draft']['selected_products'][0])
                self.assertTrue(data['original_labels'][0]['ingredients'])
                return response('fail'),client.model
            return base_call(prompt,data,schema)
        client.call = check
        output = generate_output(c,products,client)
        self.assertIsNone(output['report'])
        self.assertEqual(output['evaluation']['verdict'],'block')

    def test_money_only_reuses_success_and_cannot_bypass_budget(self):
        c, products, saved, client = case(), demo_catalog(), [], FakeClient()
        generate_output(c,products,client,checkpoint=saved.append)
        client.calls.clear()
        updated = dict(c,shipping=10,discount=5)
        first = retry_review(updated,products,saved[-1],client,reprice=True,checkpoint=saved.append)
        self.assertEqual(first['report']['pricing']['total'],222)
        self.assertTrue(first['evaluation']['reused'])
        self.assertEqual(client.calls,[])
        second = retry_review(dict(updated,shipping=20),products,saved[-1],client,reprice=True)
        self.assertEqual(second['report']['pricing']['total'],232)
        self.assertEqual(client.calls,[])
        blocked = retry_review(dict(c,shipping=10000),products,saved[0],client,reprice=True)
        self.assertEqual(blocked['evaluation']['verdict'],'block')
        self.assertEqual(client.calls,[])

    def test_failed_unknown_legacy_or_changed_period_is_not_reused(self):
        c, products = case(), demo_catalog()
        c['budget'] = 1000
        for kind in ('failed','unknown','legacy','period','money_text','date'):
            with self.subTest(kind=kind):
                client, saved = FakeClient(evaluated=response('unknown') if kind=='unknown' else response()), []
                generate_output(c,products,client,checkpoint=saved.append)
                snapshot = deepcopy(saved[0] if kind=='failed' else saved[-1])
                updated = dict(c,shipping=10)
                if kind=='legacy': snapshot.pop('review_contract')
                if kind=='period': updated['days'] = 60
                if kind=='money_text': snapshot['report']['generation']['summary'] = '原运费为0元。'
                if kind=='date': updated['as_of'] = (date.fromisoformat(c['as_of'])+timedelta(days=1)).isoformat()
                client.calls.clear()
                result = retry_review(updated,products,snapshot,client,reprice=True)
                self.assertIsNotNone(result['report'])
                self.assertEqual([p[2] for p in client.calls],['eval_schema.json'])
                self.assertNotIn('reused',result['evaluation'])

    def test_edit_review_receives_changed_fields_and_fixed_context(self):
        c, products = case(), demo_catalog()
        original = generate_output(c,products,FakeClient())['report']
        changes = {k:original['analysis'].get(k,[]) for k in ('summary','lifestyle','follow_up')}
        changes['rationales'] = {p['id']:p['rationale'] for p in original['selected_products']}
        changes['rationales']['DEMO-A'] = '改成每日99份（测试冲突）'
        def review(data):
            self.assertEqual(data['narrative_edit']['changed_fields'],['rationale:DEMO-A'])
            self.assertEqual(data['draft']['selected_products'][0]['daily_units'],1)
            self.assertIn('99份',data['draft']['selected_products'][0]['rationale'])
            self.assertEqual(data['draft']['analysis']['original_summary'],c['text'])
            return response('fail'),'test-model'
        result = edit_report(c,products,original,changes,reviewer=review)
        self.assertIsNone(result['report'])
        self.assertNotIn('99份',original['selected_products'][0]['rationale'])

    def test_case_rule_requires_verbatim_case_evidence(self):
        source = {'data':validate_case(manual_case(),demo_catalog())}
        for quote in ('按明确补充目标及标签默认量组合。','原文没有的依据'):
            client = FakeClient()
            client.call = lambda *args: ({'summary':'测试','rules':[dict(rule(),source_evidence={'field':'basis','quote':quote})]},client.model)
            if quote == source['data']['basis']:
                result = distill(source,client)
                self.assertIn('来源原文（basis）：'+quote,result['rules'][0]['reason'])
            else:
                with self.assertRaisesRegex(ValueError,'来源案例原文'): distill(source,client)

    def test_need_kind_is_validated_and_old_records_still_work(self):
        value = extracted()
        validate_needs(value,TEXT,['蛋白质补充','补充维生素B6'])
        value['goals'][0]['kind'] = 'diagnosis'
        with self.assertRaises(ValueError): validate_needs(value,TEXT,['蛋白质补充','补充维生素B6'])
        value['goals'][0].pop('kind')
        validate_needs(value,TEXT,['蛋白质补充','补充维生素B6'])


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.token = events.set([])

    def tearDown(self):
        events.reset(self.token)

    def test_api_usage_and_failure_do_not_log_sensitive_payload(self):
        env = {'OPENAI_BASE_URL':'https://example.invalid/v1','OPENAI_API_KEY':'test-secret','OPENAI_MODEL':'test-model'}
        value = {'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':40}},
                 'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
        with patch.dict('os.environ',env), patch('model_client.urlopen',return_value=io.BytesIO(json.dumps(value).encode())):
            client = ModelClient()
            client.call('private-prompt',{'analysis':'private-health'},'needs_schema.json')
        event = events.get()[0]
        self.assertEqual((event['input_tokens'],event['output_tokens'],event['cached_input_tokens']),(100,20,40))
        self.assertGreaterEqual(event['elapsed_ms'],0)
        self.assertNotIn('private',json.dumps(event))
        self.assertNotIn('test-secret',json.dumps(event))
        with patch('model_client.urlopen',side_effect=TimeoutError):
            with self.assertRaises(RuntimeError): client.call('',{},'eval_schema.json')
        self.assertEqual(events.get()[-1]['status'],'error')
        self.assertIsNone(events.get()[-1]['input_tokens'])

    def test_cli_usage_is_read_without_an_external_call(self):
        lines = [dict(type='item.completed',item={'type':'agent_message','text':'{}'}),
                 dict(type='turn.completed',usage={'input_tokens':120,'output_tokens':8,'cached_input_tokens':12})]
        result = subprocess.CompletedProcess([],0,stdout='\n'.join(json.dumps(e) for e in lines),stderr='')
        with patch.dict('os.environ',{'OPENAI_BASE_URL':'','OPENAI_API_KEY':'','OPENAI_MODEL':''}), \
             patch('model_client.codex_runtime',return_value=('codex','test-model','low')), \
             patch('model_client.subprocess.run',return_value=result), \
             patch('model_client.tempfile.TemporaryDirectory') as directory:
            directory.return_value.__enter__.return_value = '.'
            ModelClient().call('',{},'eval_schema.json')
        self.assertEqual(events.get()[0]['input_tokens'],120)
        self.assertEqual(events.get()[0]['cached_input_tokens'],12)

    def test_metrics_are_persisted_per_history_and_cache_is_distinct(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'data') as directory:
            store = Store(Path(directory)/'test.sqlite3')
            key = store.start_history('w',None,None,'select',{},[])
            client = FakeClient()
            select_products(case(),demo_catalog(),client)
            client.calls.clear()
            selected = select_products(case(),demo_catalog(),client)
            self.assertTrue(selected['matching']['cached'])
            self.assertEqual(client.calls,[])
            store.finish_history(key,selected,'ready')
            self.assertEqual(store.history(key)['result']['model_usage'][0]['status'],'cache_hit')
            next_key = store.start_history('w',None,None,'extract',{},[])
            store.finish_history(next_key,{},'error')
            self.assertEqual(store.history(next_key)['result']['model_usage'],[])


if __name__ == '__main__':
    unittest.main()
