from copy import deepcopy
from datetime import date
import unittest

from engine import compose, demo_catalog, markdown, clean_report_text, product_usage
from generator import generate_output
from test_engine import case
from test_evaluator import response
from test_generator import FakeClient


class ReportLayoutTests(unittest.TestCase):
    def test_chapters_keep_raw_input_separate_from_main_problems(self):
        c = case()
        c['text'] += '\r\n\r\n\r\n补充原文：保留　全角空格。\r\n\r\n\r\n'
        client = FakeClient()
        output = generate_output(c,demo_catalog(),client)
        r, text = output['report'],output['markdown']
        titles = ['## 1 本次有什么主要问题','## 2 选取了什么产品，为什么选取这些产品',
                  '## 3 产品的剂量、使用方式及价格总和','## 4 附属内容']
        self.assertEqual(sorted(text.index(s) for s in titles),[text.index(s) for s in titles])
        self.assertIn(client.generated['analysis_summary'],text.split(titles[1])[0])
        self.assertNotIn('补充原文',text.split(titles[3])[0])
        self.assertIn('补充原文：保留　全角空格。',text.split(titles[3])[1])
        self.assertNotIn('\n\n\n',clean_report_text(c['text']))
        self.assertEqual(r['input_text'],c['text'])
        review = client.calls[-1][1]['draft']
        self.assertEqual(review['input_text'],c['text'])
        self.assertEqual(review['analysis']['summary'],client.generated['analysis_summary'])

    def test_product_sources_are_only_selected_and_preserve_original_dose(self):
        c, products = case(),demo_catalog()
        c['adjustments'] = [{'catalog_id':'DEMO-A','daily_units':0.5,'profile_id':c['profile']['id'],
                            'reason':'虚构调整','source':'虚构依据','reviewer':'虚构审核','reviewed_at':c['as_of']}]
        report = compose(c,products)
        sources = {p['id']:p for p in report['product_sources']}
        self.assertEqual(set(sources),{p['id'] for p in report['selected_products']})
        self.assertNotIn('DEMO-D',sources)
        self.assertEqual(sources['DEMO-A']['daily_units'],1)
        self.assertEqual(report['selected_products'][0]['daily_units'],0.5)
        products[0]['daily_units'] = 99
        self.assertEqual(sources['DEMO-A']['daily_units'],1)

    def test_usage_and_unknown_price_are_not_invented(self):
        c, products = case(),demo_catalog()
        c['budget'] = None
        products[0]['raw'] = {'使用方式':'仅演示：加水搅拌。','资料日期':date(2026,11,9)}
        products[1]['price']['amount'] = None
        report = compose(c,products)
        text = markdown(report)
        self.assertEqual(product_usage(report['selected_products'][0]),'仅演示：加水搅拌。')
        self.assertIn('未提供，请核对现售标签后确认',text)
        self.assertIn('总购买金额：待报价',text)
        self.assertIn('"资料日期": "2026-11-09"',text)
        self.assertIsNone(report['pricing']['total'])
        self.assertEqual(report['product_sources'][0]['raw']['使用方式'],'仅演示：加水搅拌。')

    def test_edit_and_reprice_keep_original_material(self):
        from generator import retry_review
        from report_editor import edit_report
        client, saved = FakeClient(), []
        c, products = case(),demo_catalog()
        original = generate_output(c,products,client,checkpoint=saved.append)['report']
        changes = {'summary':'人工修改：本次明确蛋白质与B6需求。','lifestyle':[],'follow_up':[],
                   'rationales':{p['id']:p['rationale'] for p in original['selected_products']}}
        edited = edit_report(c,products,original,changes,reviewer=lambda data:(response('unknown'),'test-model'))
        self.assertEqual(edited['report']['product_sources'],original['product_sources'])
        self.assertEqual(edited['report']['input_text'],c['text'])
        self.assertIn(changes['summary'],edited['markdown'])
        priced = retry_review(dict(c,shipping=5),products,deepcopy(saved[-1]),client,reprice=True)
        self.assertEqual(priced['report']['pricing']['total'],222)
        self.assertEqual(priced['report']['product_sources'],original['product_sources'])
        self.assertEqual(priced['report']['input_text'],c['text'])


if __name__ == '__main__': unittest.main()
