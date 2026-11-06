"""Persistence and stage reuse regressions, all data and models are synthetic."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import uuid
import threading

from store import Store
from engine import demo_catalog
from generator import generate_output, select_products
from test_generator import FakeClient
from test_evaluator import case
from tasks import Tasks
from task_runtime import progress


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent / 'data')
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.key = str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def test_equal_revision_cannot_overwrite_another_window(self):
        original = dict(key=self.key, revision=0, fields={'user-id':'TEST','user-name':'first'}, expected_version=0)
        self.assertTrue(self.store.save_user('w', original))
        version = self.store.workspace_users('w')[0]['save_version']
        first = dict(original, revision=1, fields={'user-id':'TEST','user-name':'window A'}, expected_version=version)
        second = dict(first, fields={'user-id':'TEST','user-name':'window B'})
        self.assertTrue(self.store.save_user('w', first))
        self.assertFalse(self.store.save_user('w', second))
        self.assertTrue(self.store.save_user('w', first))  # Lost response: safe idempotent retry.
        self.assertEqual(self.store.workspace_users('w')[0]['fields']['user-name'], 'window A')

    def test_quote_only_save_keeps_selection_but_hides_old_report(self):
        fields = {'user-id':'TEST','days':'30','budget':'','shipping':'','discount':''}
        self.store.save_user('w', dict(key=self.key, revision=1, fields=fields))
        request = dict(case=case(), catalog=demo_catalog())
        selected = self.store.start_history('w',self.key,1,'select',request,[])
        self.store.finish_history(selected,{'status':'ready','products':[]},'ready')
        h = self.store.start_history('w',self.key,1,'compose',request,[])
        self.store.finish_history(h,{'report':{'days':30,'pricing':{}},'evaluation':{'verdict':'pass'}},'pass')
        version = self.store.workspace_users('w')[0]['save_version']
        self.store.save_user('w',dict(key=self.key,revision=1,expected_version=version,fields=dict(fields,shipping='10')))
        restored = self.store.workspace_users('w')[0]
        self.assertIsNotNone(restored['selection'])
        self.assertIsNone(restored['result'])

    def test_review_retry_does_not_call_composition(self):
        from generator import retry_review
        client = FakeClient()
        c, products = case(), demo_catalog()
        selection = select_products(c, products, client)
        saved = []
        output = generate_output(c,products,client,selection=selection,confirmed_products=['DEMO-A','DEMO-C'],checkpoint=saved.append)
        self.assertEqual(output['evaluation']['verdict'],'pass')
        self.assertEqual(len(saved),2)
        client.calls.clear()
        result = retry_review(c,products,deepcopy(saved[0]),client)
        self.assertEqual(result['evaluation']['verdict'],'pass')
        self.assertEqual([call[2] for call in client.calls],['eval_schema.json'])
        changed = deepcopy(products); changed[0]['daily_units'] = 99
        with self.assertRaises(ValueError):
            retry_review(c,changed,deepcopy(saved[0]),client)

    def test_reprice_reuses_selection_but_recalculates_quote_and_safety(self):
        from generator import retry_review
        client = FakeClient()
        c, products, saved = case(), demo_catalog(), []
        generate_output(c,products,client,checkpoint=saved.append)
        client.calls.clear()
        priced = retry_review(dict(c,shipping=10,discount=5,days=60,budget=1000),products,saved[0],client,reprice=True)
        self.assertEqual([call[2] for call in client.calls],['eval_schema.json'])
        self.assertEqual(priced['report']['pricing']['total'],350)
        self.assertEqual(priced['report']['days'],60)
        client.calls.clear()
        blocked = retry_review(dict(c,budget=1),products,saved[0],client,reprice=True)
        self.assertEqual(blocked['evaluation']['verdict'],'block')
        self.assertEqual(client.calls,[])
        changed = deepcopy(c); changed['profile']['forbidden_products']=['DEMO-A']
        with self.assertRaises(ValueError): retry_review(changed,products,saved[0],client,reprice=True)

    def test_task_cancel_discards_late_result_and_history(self):
        manager=Tasks(self.store)
        self.store.save_user('w',dict(key=self.key,revision=0,fields={'user-id':'TASK'}))
        entered, release, finished = threading.Event(),threading.Event(),threading.Event()
        def execute(action,payload,saved,checkpoint):
            h=self.store.start_history('w',self.key,0,'compose',{},[])
            checkpoint({'draft':'server only'}); progress('review'); entered.set()
            try:
                release.wait(5)
                self.store.finish_history(h,{'report':'must not be published'},'pass')
                return {'status':'ready'}
            finally: finished.set()
        task=manager.start('w','/api/compose',dict(user_key=self.key,user_revision=0),execute)
        self.assertTrue(entered.wait(5))
        self.assertEqual(manager.get('w',task['id'])['stage'],'review')
        self.assertNotIn('checkpoint',manager.get('w',task['id']))
        with self.assertRaises(PermissionError): manager.get('another',task['id'])
        with self.assertRaises(ValueError): manager.start('w','/api/select',dict(user_key=self.key,user_revision=0),execute)
        cancelled=manager.cancel('w',task['id'])
        self.assertTrue(cancelled['retry_review'])
        release.set(); self.assertTrue(finished.wait(5))
        self.assertEqual(manager.get('w',task['id'])['status'],'cancelled')
        with self.store.connect() as db:
            row=db.execute('SELECT result,status FROM history').fetchone()
            self.assertIsNone(row['result']); self.assertEqual(row['status'],'cancelled')

    def test_restart_preserves_checkpoint_and_marks_interrupted(self):
        from store import encode, now
        manager=Tasks(self.store)
        with self.store.connect() as db:
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('interrupted','w',self.key,'/api/compose',encode({}),'running','review',now(),None,None,None,encode({'draft':'saved'}),None))
        restored=Tasks(self.store).get('w','interrupted',private=True)
        self.assertEqual(restored['status'],'interrupted')
        self.assertTrue(restored['retry_review'])
        self.assertEqual(restored['checkpoint'],{'draft':'saved'})


if __name__ == '__main__':
    unittest.main()
