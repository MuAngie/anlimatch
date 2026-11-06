from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from engine import demo_catalog
from generator import generate_output, select_products
from learning import validate_case, validate_rule, distill, rule_context, compact_rules
from store import Store
from test_engine import case
from test_generator import FakeClient


def manual_case():
    c=case()
    return {'title':'匿名人工演示001','analysis':c['text'],'profile':{'age':42,'goals':['蛋白质补充','补充维生素B6'],'conditions':[],'medications':[],'allergies':[],'pregnancy':['none']},
            'products':[{'catalog_id':'DEMO-A','daily_units':1,'rationale':'蛋白质补充'}, {'catalog_id':'DEMO-C','daily_units':1,'rationale':'补充维生素B6'}],
            'exclusions':[{'catalog_id':'DEMO-D','reason':'本次无明确补钙目标'}], 'basis':'按明确补充目标及标签默认量组合。','outcome':'虚构流程案例，没有临床疗效证明。'}


def rule(kind='combine'):
    return {'kind':kind,'title':'成人蛋白质和B6示例组合','conditions':{'goals':['蛋白质补充','补充维生素B6'],'conditions':[],'medications':[],'allergies':[],'pregnancy':[],'age_min':18,'age_max':None},
            'product_ids':['DEMO-D'] if kind=='exclude' else ['DEMO-A','DEMO-C'], 'reason':'来源案例人工组合记录','limitations':'仅供演示，需按当前标签和个体情况独立复核。'}


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.directory=Path(__file__).parent / ('.learning-test-'+str(uuid.uuid4()))
        self.store=Store(self.directory/'test.sqlite3')
        self.catalog=demo_catalog()
        self.source_id=self.store.create_case(validate_case(manual_case(),self.catalog),'test-admin')
        self.source=self.store.learning_case(self.source_id)

    def tearDown(self):
        for p in self.directory.iterdir(): p.unlink()
        self.directory.rmdir()

    def enabled(self,value=None):
        r=self.store.save_rule(self.source_id,value or rule(),'test-admin')
        return self.store.rule_state(r['id'],r['revision'],'active','review-admin')

    def test_draft_edit_disable_review_and_source_versions(self):
        r=self.store.save_rule(self.source_id,rule(),'test-admin')
        self.assertEqual(rule_context(case(),self.catalog,self.store.learned_rules(True))['rules'],[])
        active=self.store.rule_state(r['id'],1,'active','review-admin')
        self.assertEqual(active['reviewed_by'],'review-admin')
        self.assertEqual(len(rule_context(case(),self.catalog,self.store.learned_rules(True))['rules']),1)
        updated=self.store.save_rule(self.source_id,dict(rule(),reason='修订依据'),'test-admin',active['id'],active['revision'])
        self.assertEqual(updated['status'],'draft')
        self.assertNotIn('reviewed_by',updated)
        self.assertEqual(self.store.learned_rules(True),[])
        with self.assertRaises(ValueError): self.store.rule_state(updated['id'],1,'active','test-admin')
        active=self.store.rule_state(updated['id'],updated['revision'],'active','review-admin')
        self.store.rule_state(active['id'],active['revision'],'inactive','test-admin')
        self.assertEqual(self.store.learned_rules(True),[])
        versions=self.store.rule_versions(active['id'])
        self.assertEqual(len(versions),5)
        self.assertTrue(all(v['data']['case_id']==self.source_id for v in versions))
        self.assertEqual(Store(self.store.path).learning_case(self.source_id)['data']['analysis'],manual_case()['analysis'])

    def test_full_rule_coverage_saves_matching_call_without_reading_case(self):
        self.enabled()
        with patch.object(self.store,'learning_case',side_effect=AssertionError('生成时不读完整案例')):
            context=rule_context(case(),self.catalog,self.store.learned_rules(True))
        client=FakeClient()
        selected=select_products(case(),self.catalog,client,learned=context)
        self.assertEqual(client.calls,[])
        self.assertTrue(selected['matching']['rule_based'])
        self.assertEqual([p['id'] for p in selected['products'] if p['eligible']],['DEMO-A','DEMO-C'])
        output=generate_output(case(),self.catalog,client,selection=selected,confirmed_products=['DEMO-A','DEMO-C'],learned=context)
        self.assertEqual([c[2] for c in client.calls],['compose_schema.json','eval_schema.json'])
        self.assertEqual(output['report']['pricing']['total'],217)
        self.assertIn(self.source_id,output['markdown'])
        sent=json.dumps([c[1] for c in client.calls],ensure_ascii=False)
        self.assertNotIn('匿名人工演示001',sent)
        self.assertNotIn(manual_case()['outcome'],sent)
        self.assertTrue(all('learned_rules' in (c[1]['draft'] if c[2]=='eval_schema.json' else c[1]) for c in client.calls))

    def test_partial_rules_keep_batch_matching_and_case_text_is_never_sent(self):
        r=rule('select');r['product_ids']=['DEMO-A'];r['conditions']['goals']=['蛋白质补充'];self.enabled(r)
        context=rule_context(case(),self.catalog,self.store.learned_rules(True))
        client=FakeClient();select_products(case(),self.catalog,client,learned=context)
        self.assertEqual([c[2] for c in client.calls],['matching_schema.json'])
        self.assertEqual(len(client.calls[0][1]['learned_rules']),1)
        self.assertNotIn('labels',client.calls[0][1]['learned_rules'][0])

    def test_local_conditions_unknown_background_and_changed_labels(self):
        value=rule();value['conditions'].update(conditions=['虚构状况'],pregnancy=['none'],age_min=40,age_max=50);self.enabled(value)
        for age,conditions,pregnancy in ((None,['虚构状况'],'none'),(42,[],'none'),(42,['虚构状况'],None),(51,['虚构状况'],'none')):
            c=case();c['profile'].update(age=age,conditions=conditions,pregnancy=pregnancy)
            self.assertEqual(rule_context(c,self.catalog,self.store.learned_rules(True))['rules'],[])
        c=case();c['profile']['conditions']=['虚构状况']
        self.assertEqual(len(rule_context(c,self.catalog,self.store.learned_rules(True))['rules']),1)
        self.catalog[0]['ingredients'][0]['amount_per_unit']=4
        self.assertEqual(rule_context(c,self.catalog,self.store.learned_rules(True))['rules'],[])

    def test_combination_requires_all_its_targets_not_only_one(self):
        self.enabled()
        c=case();c['goal_context']={'goals':[{'name':'蛋白质补充','catalog_goal':'蛋白质补充'}]}
        self.assertEqual(rule_context(c,self.catalog,self.store.learned_rules(True))['rules'],[])

    def test_rules_cannot_override_forbidden_allergy_dose_or_budget(self):
        self.enabled()
        for failure in ('forbidden','allergy','dose','budget'):
            c, catalog=case(),deepcopy(self.catalog)
            if failure=='forbidden':c['profile']['forbidden_products']=['DEMO-A']
            if failure=='allergy':catalog[0]['allergens']=['大豆'];c['profile']['allergies']=['大豆']
            if failure=='dose':catalog[0]['daily_units']=3
            if failure=='budget':c['budget']=100
            ctx=rule_context(c,catalog,self.store.learned_rules(True))
            output=generate_output(c,catalog,FakeClient(),learned=ctx)
            self.assertIsNone(output['report'],failure)
            self.assertEqual(output['evaluation']['verdict'],'block',failure)
        with self.assertRaises(ValueError):validate_rule(dict(rule(),daily_units=3),self.source)
        with self.assertRaises(ValueError):validate_rule(dict(rule(),product_ids=['DEMO-D']),self.source)

    def test_exclusion_rules_are_enforced_before_model_and_audited(self):
        value=rule('exclude');value['product_ids']=['DEMO-D'];self.enabled(value)
        ctx=rule_context(case(),self.catalog,self.store.learned_rules(True));client=FakeClient()
        result=select_products(case(),self.catalog,client,learned=ctx)
        self.assertNotIn('DEMO-D',[p['catalog_id'] for p in client.calls[0][1]['catalog']])
        self.assertTrue(any(p['id']=='DEMO-D' and '人工排除' in p['reason'] for p in result['matching']['excluded_products']))

    def test_distillation_is_one_call_and_does_not_enable_rules(self):
        class Client:
            model='test-model'
            def call(inner,prompt,data,schema):
                self.assertEqual(schema,'learning_schema.json');self.assertNotIn('raw',data['labels']['DEMO-A'])
                return {'summary':'提炼演示规则','rules':[dict(rule(), source_evidence={'field':'basis','quote':data['basis']})]},inner.model
        result=distill(self.source,Client())
        self.assertEqual(len(result['rules']),1)
        self.assertEqual(self.store.learned_rules(True),[])
        with self.assertRaises(ValueError):validate_rule(dict(rule(),conditions={k:(None if k.startswith('age_') else []) for k in rule()['conditions']}),self.source)

    def test_rule_context_is_bounded(self):
        for _ in range(10):self.enabled()
        context=rule_context(case(),self.catalog,self.store.learned_rules(True))
        self.assertEqual(len(compact_rules(context)),8)

if __name__=='__main__':unittest.main()
