from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request
import uuid

from server import Handler, prompts
from store import Store
from test_engine import case
from engine import demo_catalog
from generator import generate_output, select_products
from report_editor import edit_report
from test_generator import FakeClient
from test_evaluator import response
from test_needs import TEXT, extracted


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(__file__).parent / (".admin-test-" + str(uuid.uuid4()))
        cls.store = Store(cls.directory / "test.sqlite3")
        class TestHandler(Handler):
            store = cls.store
            login_attempts = {}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.store.setup("test-admin", "Only-For-Test-12345")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        shutil.rmtree(cls.directory)

    def client(self):
        jar = CookieJar()
        return build_opener(HTTPCookieProcessor(jar)), jar

    def call(self, client, path, payload=None, headers=None):
        data = json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
        req = Request(self.url+path,data=data,headers={"Content-Type":"application/json",**(headers or {})})
        try:
            with client.open(req, timeout=10) as result:
                return result.status, json.load(result)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def administrator(self):
        client, jar = self.client()
        status, data = self.call(client,"/api/admin/login",{"username":"test-admin","password":"Only-For-Test-12345"})
        self.assertEqual(status,200); self.assertEqual(data["account"]["role"],"admin")
        return client, jar

    def select_confirmed(self, client, payload, ids=None):
        with patch('server.select_products',side_effect=lambda *args:select_products(*args,FakeClient())):
            status,selected = self.call(client,'/api/select',payload)
        self.assertEqual(status,200)
        return dict(payload,selection_id=selected['history_id'],confirmed_products=ids or [p['id'] for p in selected['products'] if p['eligible']])

    def wait_task(self, client, task):
        deadline=time.monotonic()+10
        while task['status']=='running' and time.monotonic()<deadline:
            time.sleep(.02)
            status,task=self.call(client,'/api/tasks?id='+task['id'])
            self.assertEqual(status,200)
        self.assertNotEqual(task['status'],'running')
        return task

    def test_background_review_retry_and_quote_reuse(self):
        from generator import retry_review
        import subprocess
        client,_=self.client(); other,_=self.client(); c=case()
        user=dict(key=str(uuid.uuid4()),revision=0,fields={'user-id':'ASYNC','days':'30','shipping':'0'})
        self.assertEqual(self.call(client,'/api/users/save',user)[0],200)
        request=dict(case=c,catalog=demo_catalog(),catalog_kind='demo',user_key=user['key'],user_revision=0)
        request=self.select_confirmed(client,request)
        fake=FakeClient(); original_call=fake.call
        def fail_eval(prompt,data,schema):
            if schema=='eval_schema.json': raise subprocess.TimeoutExpired('fake',1)
            return original_call(prompt,data,schema)
        fake.call=fail_eval
        with patch('server.generate_output',side_effect=lambda *a,**kw:generate_output(*a,client=fake,**kw)):
            status,task=self.call(client,'/api/tasks/start',{'action':'/api/compose','request':request})
            self.assertEqual(status,202); task=self.wait_task(client,task)
        self.assertEqual(task['status'],'error'); self.assertTrue(task['retry_review'])
        self.assertIsNone(task['result']['report'])
        self.assertEqual(self.call(other,'/api/tasks?id='+task['id'])[0],401)
        fake.call=original_call; fake.calls.clear()
        with patch('server.retry_review',side_effect=lambda *a,**kw:retry_review(*a,client=fake,**kw)), patch('server.generate_output') as generate:
            status,retried=self.call(client,'/api/tasks/retry',{'id':task['id']})
            self.assertEqual(status,202); retried=self.wait_task(client,retried)
            self.assertEqual(retried['status'],'done',retried.get('error'))
            self.assertEqual(retried['result']['evaluation']['verdict'],'pass')
            generate.assert_not_called()
        self.assertEqual([call[2] for call in fake.calls],['eval_schema.json'])
        restored=self.call(client,'/api/users')[1]['users'][0]
        self.assertEqual(restored['result']['task_id'],retried['id'])
        updated=dict(user,expected_version=restored['save_version'],fields=dict(user['fields'],shipping='10'))
        self.assertEqual(self.call(client,'/api/users/save',updated)[0],200)
        fake.calls.clear()
        with patch('server.retry_review',side_effect=lambda *a,**kw:retry_review(*a,client=fake,**kw)), patch('server.generate_output') as generate:
            status,priced=self.call(client,'/api/tasks/start',{'action':'/api/compose','request':dict(request,case=dict(c,shipping=10),reprice_task_id=retried['id'])})
            self.assertEqual(status,202); priced=self.wait_task(client,priced)
            self.assertEqual(priced['status'],'done',priced.get('error'))
            self.assertEqual(priced['result']['report']['pricing']['total'],227)
            generate.assert_not_called()
        self.assertEqual(fake.calls,[])
        self.assertTrue(priced['result']['evaluation']['reused'])

    def test_background_stale_inputs_are_rejected_and_conflicting_save_returns_409(self):
        client,_=self.client(); user=dict(key=str(uuid.uuid4()),revision=0,fields={'user-id':'CAS'})
        status,saved=self.call(client,'/api/users/save',dict(user,expected_version=0))
        self.assertEqual(status,200)
        original=dict(user,expected_version=saved['save_version'])
        changed=dict(original,fields={'user-id':'CAS','analysis-text':'新版分析'},revision=1)
        self.assertEqual(self.call(client,'/api/users/save',changed)[0],200)
        self.assertEqual(self.call(client,'/api/users/save',dict(original,fields={'user-id':'CAS','analysis-text':'旧版'}))[0],409)
        with patch('server.extract_needs') as extract:
            status,result=self.call(client,'/api/tasks/start',{'action':'/api/extract','request':dict(case=case(),catalog=demo_catalog(),user_key=user['key'],user_revision=0)})
            self.assertEqual(status,400)
            extract.assert_not_called()

    def test_admin_endpoints_deny_anonymous_and_forged_role(self):
        client, jar = self.client()
        for path in ("/api/admin/users","/api/admin/history","/api/admin/prompts","/api/admin/history/detail?id=fake"):
            self.assertEqual(self.call(client,path,headers={"X-Role":"admin","Cookie":"hc_admin=forged"})[0],401)
        self.assertEqual(self.call(client,"/api/admin/logout",{"role":"admin"})[0],403)

    def test_login_cookie_password_hash_and_logout_revocation(self):
        client, jar = self.administrator()
        cookie = next(c for c in jar if c.name=="hc_admin")
        self.assertIn("HttpOnly",cookie._rest); self.assertEqual(cookie._rest["SameSite"],"Strict")
        with self.store.connect() as db:
            row = db.execute("SELECT password_hash FROM admins").fetchone()
            self.assertNotEqual(row[0],b"Only-For-Test-12345")
        self.assertEqual(self.call(client,"/api/admin/logout",{})[0],200)
        self.assertEqual(self.call(client,"/api/admin/users",headers={"Cookie":"hc_admin="+cookie.value})[0],401)

    def test_workspaces_are_isolated_but_admin_can_see_all(self):
        first,_=self.client(); second,_=self.client()
        user={"key":str(uuid.uuid4()),"fields":{"user-id":"TEST-USER","user-name":"虚构测试用户","analysis-text":"营养目标：蛋白质补充。"},"revision":1,"demo":True}
        self.assertEqual(self.call(first,"/api/users/save",user)[0],200)
        self.assertEqual(len(self.call(first,"/api/users")[1]["users"]),1)
        self.assertEqual(self.call(second,"/api/users")[1]["users"],[])
        self.assertEqual(self.call(second,"/api/users/save",user)[0],403)
        admin,_=self.administrator()
        rows=self.call(admin,"/api/admin/users")[1]["users"]
        self.assertTrue(any(u["key"]==user["key"] for u in rows))
        self.assertEqual(self.call(admin,"/api/admin/user?key="+user["key"])[1]["fields"],user["fields"])

    def test_history_logs_blocked_and_invalid_requests_and_prompt_snapshot(self):
        client,_=self.client(); c=case(); c["profile"]["forbidden_products"]=["DEMO-A","DEMO-C","DEMO-D"]
        self.assertEqual(self.call(client,"/api/compose",{"case":c,"catalog":demo_catalog()})[1]["evaluation"]["verdict"],"block")
        self.assertEqual(self.call(client,"/api/compose",{"case":{"text":""},"catalog":[]})[0],400)
        self.assertEqual(self.call(client,"/api/import",{"filename":"bad.xlsx","data":"invalid"})[0],400)
        admin,_=self.administrator(); rows=self.call(admin,"/api/admin/history")[1]["items"]
        self.assertTrue(any(h["status"]=="block" for h in rows)); self.assertTrue(any(h["status"]=="error" for h in rows))
        record=next(h for h in rows if h["status"]=="block")
        detail=self.call(admin,"/api/admin/history/detail?id="+record["id"])[1]
        self.assertEqual(detail["prompts"],[p for p in prompts() if p["key"] in ("matching","compose","eval")]); self.assertIsNone(detail["result"]["report"])

    def test_learning_admin_review_lifecycle_and_stale_selection(self):
        from test_learning import manual_case, rule
        anon,_=self.client()
        for endpoint in ('/api/admin/learning','/api/admin/learning/case?id=fake','/api/admin/learning/versions?id=fake'):
            self.assertEqual(self.call(anon,endpoint)[0],401)
        for endpoint in ('case','distill','rule','state'):
            self.assertEqual(self.call(anon,'/api/admin/learning/'+endpoint,{'role':'admin'})[0],403)
        admin,_=self.administrator()
        status,source=self.call(admin,'/api/admin/learning/case',manual_case());self.assertEqual(status,200)
        with patch('server.distill',return_value={'summary':'受控提炼','rules':[rule()],'model':'test-model'}):
            status,result=self.call(admin,'/api/admin/learning/distill',{'case_id':source['id']})
        self.assertEqual(status,200);draft=result['rules'][0];self.assertEqual(draft['status'],'draft')
        state={'id':draft['id'],'revision':draft['revision'],'status':'active'}
        self.assertEqual(self.call(admin,'/api/admin/learning/state',state)[0],400)
        status,active=self.call(admin,'/api/admin/learning/state',dict(state,reviewed=True));self.assertEqual(status,200)
        self.assertEqual(self.call(admin,'/api/admin/learning/state',dict(state,reviewed=True))[0],400)
        client,_=self.client();key=str(uuid.uuid4())
        self.call(client,'/api/users/save',{'key':key,'revision':1,'fields':{'user-id':'RULE-USER','analysis-text':case()['text']}})
        request={'case':case(),'catalog':demo_catalog(),'user_key':key,'user_revision':1}
        model=FakeClient()
        with patch('server.select_products',side_effect=lambda *args,**kwargs:select_products(*args,model,**kwargs)):
            status,selected=self.call(client,'/api/select',request)
        self.assertEqual(status,200);self.assertEqual(model.calls,[])
        self.assertEqual(selected['learning']['rules'][0]['id'],active['id'])
        with patch('server.generate_output',side_effect=lambda *args,**kwargs:generate_output(*args,model,**kwargs)):
            status,report=self.call(client,'/api/compose',dict(request,selection_id=selected['history_id'],confirmed_products=['DEMO-A','DEMO-C']))
        self.assertEqual(status,200);self.assertEqual(report['report']['pricing']['total'],217)
        self.assertEqual([call[2] for call in model.calls],['compose_schema.json','eval_schema.json'])
        self.call(admin,'/api/admin/learning/state',{'id':active['id'],'revision':active['revision'],'status':'inactive'})
        with patch('server.generate_output') as generated:
            status,error=self.call(client,'/api/compose',dict(request,selection_id=selected['history_id'],confirmed_products=['DEMO-A','DEMO-C']))
        self.assertEqual(status,400);self.assertIn('规则已更新或停用',error['error']);generated.assert_not_called()
        versions=self.call(admin,'/api/admin/learning/versions?id='+active['id'])[1]['versions']
        self.assertEqual(len(versions),3)
        self.assertEqual(self.call(admin,'/api/admin/learning/case?id='+source['id'])[1]['data']['basis'],manual_case()['basis'])

    def test_learning_disabled_during_generation_does_not_release_old_rule_report(self):
        from test_learning import manual_case, rule
        from learning import validate_case
        source=self.store.create_case(validate_case(manual_case(),demo_catalog()),'test-admin')
        draft=self.store.save_rule(source,rule(),'test-admin')
        active=self.store.rule_state(draft['id'],draft['revision'],'active','test-admin')
        client,_=self.client();key=str(uuid.uuid4())
        self.call(client,'/api/users/save',{'key':key,'revision':1,'fields':{'user-id':'CONCURRENT-RULE'}})
        model=FakeClient();original=model.call
        def changing_call(prompt,data,schema):
            if schema=='eval_schema.json':self.store.rule_state(active['id'],active['revision'],'inactive','test-admin')
            return original(prompt,data,schema)
        model.call=changing_call
        with patch('server.generate_output',side_effect=lambda *args,**kwargs:generate_output(*args,model,**kwargs)):
            status,result=self.call(client,'/api/compose',{'case':case(),'catalog':demo_catalog(),'user_key':key,'user_revision':1})
        self.assertEqual(status,200);self.assertIsNone(result['report'])
        self.assertIn('生成期间',result['evaluation']['summary'])
        self.assertIn('停用',result['evaluation']['summary'])

    def test_prompts_are_actual_and_administrator_only(self):
        admin,_=self.administrator(); data=self.call(admin,"/api/admin/prompts")[1]
        self.assertEqual([p["key"] for p in data["prompts"]],["needs","matching","compose","eval","learning"])
        self.assertEqual(data["prompts"],prompts()); self.assertTrue(data["rules"])
        self.assertEqual(self.call(admin,"/api/admin/prompts",{"text":"overwrite"})[0],404)

    def test_setup_cannot_replace_existing_admin_and_password_is_required(self):
        client,_=self.client()
        self.assertEqual(self.call(client,"/api/admin/setup",{"username":"attacker","password":"Another-Secret-123"})[0],409)
        empty = Store(self.directory/"empty.sqlite3")
        with self.assertRaises(ValueError):
            empty.setup("admin","1234567")
        empty.setup("admin","12345678")
        self.assertIsNotNone(empty.login("admin","12345678"))

    def test_cross_origin_and_rebound_host_are_rejected(self):
        client,_=self.client()
        self.assertEqual(self.call(client,"/api/admin/login",{},headers={"Origin":"https://example.invalid"})[0],403)
        self.assertEqual(self.call(client,"/api/admin/status",headers={"Host":"example.invalid"})[0],403)

    def test_expired_sessions_and_login_throttling(self):
        token=self.store.login("test-admin","Only-For-Test-12345")
        with self.store.connect() as db: db.execute("UPDATE sessions SET expires=? WHERE expires>?",(time.time()-1,time.time()))
        self.assertIsNone(self.store.admin(token))
        client,_=self.client()
        for _ in range(5): self.assertEqual(self.call(client,"/api/admin/login",{"username":"wrong","password":"wrong"})[0],401)
        self.assertEqual(self.call(client,"/api/admin/login",{"username":"wrong","password":"wrong"})[0],429)
        self.server.RequestHandlerClass.login_attempts.clear()

    def test_persistence_revision_and_history_pagination(self):
        workspace="unit-test-workspace"; key=str(uuid.uuid4())
        user={"key":key,"fields":{"user-id":"PERSIST-TEST","user-name":"虚构持久化测试"},"revision":3}
        self.store.save_user(workspace,user)
        stale=dict(user,revision=2,fields={"user-id":"PERSIST-TEST","user-name":"旧版本"})
        self.assertFalse(self.store.save_user(workspace,stale))
        self.assertEqual(Store(self.store.path).workspace_users(workspace)[0]["fields"],user["fields"])
        for _ in range(32):
            entry=self.store.start_history(workspace,key,3,"compose",{},[])
            self.store.finish_history(entry,{"report":None,"evaluation":{"verdict":"block"}},"block")
        page=self.store.histories(0,key); self.assertEqual(len(page["items"]),30)
        self.assertEqual(len(self.store.histories(30,key)["items"]),2)

    def test_report_edits_are_owned_versioned_reviewed_and_persisted(self):
        client,jar=self.client(); key=str(uuid.uuid4())
        user={"key":key,"fields":{"user-id":"demo","user-name":"虚构编辑测试"},"revision":1,"demo":True}
        self.assertEqual(self.call(client,"/api/users/save",user)[0],200)
        workspace=hashlib.sha256(next(c.value for c in jar if c.name=="hc_workspace").encode()).hexdigest()
        c,catalog=case(),demo_catalog(); original=generate_output(c,catalog,FakeClient(evaluated=response('unknown')))
        source=self.store.start_history(workspace,key,1,"compose",{"case":c,"catalog":catalog},prompts())
        self.store.finish_history(source,original,"needs_review")
        changes={"summary":"虚构编辑摘要", "lifestyle":[], "follow_up":["按原计划复查"],
                 "rationales":{p["id"]:p["rationale"] for p in original["report"]["selected_products"]}}
        payload={"user_key":key,"user_revision":1,"history_id":source,"changes":changes}
        foreign,_=self.client()
        self.assertEqual(self.call(foreign,"/api/reports/edit",payload)[0],403)
        with patch("server.edit_report",side_effect=lambda *args:edit_report(*args,reviewer=lambda data:(response('unknown'),"test-model"))):
            self.assertEqual(self.call(client,"/api/reports/edit",dict(payload,changes=dict(changes,pricing={"total":0})))[0],400)
            status,result=self.call(client,"/api/reports/edit",payload)
        self.assertEqual(status,200)
        self.assertEqual(result['evaluation']['verdict'],'needs_review')
        self.assertEqual(result["report"]["pricing"],original["report"]["pricing"])
        self.assertEqual(self.call(client,"/api/users")[1]["users"][0]["result"]["history_id"],result["history_id"])
        record=self.store.history(result["history_id"])
        self.assertEqual(record["action"],"report_edit")
        self.assertEqual(record["request"]["source_history_id"],source)
        self.assertEqual([p["key"] for p in record["prompts"]],["eval"])
        self.assertEqual(self.call(client,"/api/reports/edit",payload)[0],400)
        payload["history_id"]=result["history_id"]
        with patch("server.edit_report",side_effect=lambda *args:edit_report(*args,reviewer=lambda data:(response("fail"),"test-model"))):
            self.assertIsNone(self.call(client,"/api/reports/edit",payload)[1]["report"])
        self.assertEqual(self.call(client,"/api/users")[1]["users"][0]["result"]["history_id"],result["history_id"])
        self.call(client,"/api/users/save",dict(user,revision=2))
        self.assertEqual(self.call(client,"/api/reports/edit",payload)[0],400)

    def test_needs_confirmation_connects_free_format_text_and_preserves_trace(self):
        client,_ = self.client(); key = str(uuid.uuid4())
        user = {"key":key,"revision":1,"fields":{"user-id":"NEEDS-TEST","analysis-text":TEXT}}
        self.call(client,"/api/users/save",user)
        c = case(); c["text"] = TEXT
        payload = {"case":c,"catalog":demo_catalog(),"user_key":key,"user_revision":1}
        with patch("server.extract_needs",return_value=dict(extracted(),model="test-model",source="测试模型")):
            status,needs = self.call(client,"/api/extract",payload)
        self.assertEqual(status,200)
        source = self.store.history(needs["history_id"])
        self.assertEqual(source["action"],"extract")
        self.assertEqual([p["key"] for p in source["prompts"]],["needs"])
        restored = self.call(client,"/api/users")[1]["users"][0]
        self.assertIsNone(restored["result"])
        self.assertEqual(restored["extraction"]["history_id"],needs["history_id"])
        confirmed = dict(payload,extraction_id=needs["history_id"],confirmed_goals=[0,1])
        self.assertEqual(self.call(client,'/api/compose',confirmed)[0],400)
        confirmed = self.select_confirmed(client,confirmed)
        self.assertEqual(self.call(client,'/api/users')[1]['users'][0]['selection']['history_id'],confirmed['selection_id'])
        with patch("server.generate_output",side_effect=lambda *args,**kwargs:generate_output(*args,FakeClient(),**kwargs)):
            status,result = self.call(client,"/api/compose",confirmed)
        self.assertEqual(status,200)
        self.assertEqual(result["evaluation"]["verdict"],"pass")
        self.assertEqual(result["report"]["pricing"]["total"],217)
        self.assertEqual(result["report"]["analysis"]["goal_context"]["goals"],needs["goals"])
        record = self.store.history(result["history_id"])
        self.assertEqual(record["request"]["extraction_id"],needs["history_id"])
        self.assertEqual(self.call(client,'/api/users')[1]['users'][0]['result']['history_id'],result['history_id'])
        changed_user = dict(user,expected_version=self.call(client,'/api/users')[1]['users'][0]['save_version'],fields=dict(user['fields'],**{'selection-choice-id':confirmed['selection_id'],'selection-choices':'["DEMO-C"]'}))
        self.call(client,'/api/users/save',changed_user)
        self.assertIsNone(self.call(client,'/api/users')[1]['users'][0]['result'])
        partial = FakeClient(generated={"status":"proposed","summary":"仅组合本次已勾选的蛋白质目标","analysis_summary":"本次确认蛋白质补充需求。",
            "products":[{"catalog_id":"DEMO-A","rationale":"已选蛋白质目标"}]})
        only_protein = self.select_confirmed(client,dict(confirmed,confirmed_goals=[0]))
        self.assertIsNone(self.call(client,'/api/users')[1]['users'][0]['result'])
        with patch('server.generate_output') as generated:
            self.assertEqual(self.call(client,'/api/compose',confirmed)[0],400)
            other,_ = self.client()
            self.assertEqual(self.call(other,'/api/compose',only_protein)[0],403)
            changed = dict(only_protein,catalog=demo_catalog()+[dict(demo_catalog()[0],id='NEW-PRODUCT')])
            self.assertEqual(self.call(client,'/api/compose',changed)[0],400)
            generated.assert_not_called()
        with patch("server.generate_output",side_effect=lambda *args,**kwargs:generate_output(*args,partial,**kwargs)):
            status,result = self.call(client,"/api/compose",only_protein)
        self.assertEqual(status,200)
        self.assertEqual(result["report"]["analysis"]["goals"],["蛋白质补充"])
        self.assertEqual([p["id"] for p in result["report"]["selected_products"]],["DEMO-A"])

    def test_needs_cannot_be_forged_reused_across_users_or_after_input_changes(self):
        client,_ = self.client(); key = str(uuid.uuid4())
        user = {"key":key,"revision":1,"fields":{"user-id":"NEEDS-GUARD","analysis-text":TEXT}}
        self.call(client,"/api/users/save",user)
        c = case(); c["text"] = TEXT
        payload = {"case":c,"catalog":demo_catalog(),"user_key":key,"user_revision":1}
        with patch("server.extract_needs",return_value=dict(extracted(),model="test-model",source="测试模型")):
            needs = self.call(client,"/api/extract",payload)[1]
        confirmed = dict(payload,extraction_id=needs["history_id"],confirmed_goals=[0,1])
        other,_ = self.client()
        self.assertEqual(self.call(other,"/api/compose",confirmed)[0],403)
        for indices in ([],[-1],[0,0],[True],[[0]]):
            self.assertEqual(self.call(client,"/api/compose",dict(confirmed,confirmed_goals=indices))[0],400)
        self.assertEqual(self.call(client,"/api/compose",dict(confirmed,case=dict(c,text=TEXT+"修改")))[0],400)
        self.assertEqual(self.call(client,"/api/compose",dict(confirmed,catalog=[]))[0],400)
        self.assertEqual(self.call(client,"/api/compose",dict(payload,case=dict(c,goal_context=extracted())))[0],400)
        self.call(client,"/api/users/save",dict(user,revision=2))
        self.assertEqual(self.call(client,"/api/compose",confirmed)[0],400)
        restored = self.call(client,"/api/users")[1]["users"][0]["extraction"]
        self.assertEqual(restored["history_id"],needs["history_id"])
        self.assertEqual(restored["goals"],needs["goals"])
        self.assertTrue(restored["stale"])
        reused = dict(confirmed,user_revision=2,reuse_extraction=True,case=dict(c,profile=dict(c["profile"],age=46)))
        self.assertEqual(self.call(other,"/api/compose",reused)[0],403)
        self.assertEqual(self.call(client,"/api/compose",dict(reused,reuse_extraction="true"))[0],400)
        reused = self.select_confirmed(client,reused)
        with patch("server.generate_output",side_effect=lambda *args,**kwargs:generate_output(*args,FakeClient(),**kwargs)) as generated:
            status,result = self.call(client,"/api/compose",reused)
        self.assertEqual(status,200)
        self.assertEqual(result["evaluation"]["verdict"],"pass")
        self.assertEqual(generated.call_args.args[0]["profile"]["age"],46)
        self.assertTrue(result["report"]["analysis"]["goal_context"]["reuse_confirmed"])
        self.assertEqual(result["report"]["analysis"]["goal_context"]["source_analysis"],TEXT)
        blocked = dict(reused,case=dict(c,profile=dict(c["profile"],forbidden_products=["DEMO-A","DEMO-C","DEMO-D"])))
        self.assertEqual(self.call(client,"/api/compose",blocked)[0],400)
        result = self.call(client,"/api/select",blocked)[1]
        self.assertEqual(result["products"],[])
        with patch("server.extract_needs",return_value=dict(extracted(),model="test-model",source="测试模型")):
            fresh = self.call(client,"/api/extract",dict(payload,user_revision=2))[1]
        restored = self.call(client,"/api/users")[1]["users"][0]["extraction"]
        self.assertEqual(restored["history_id"],fresh["history_id"])
        self.assertFalse(restored.get("stale",False))


if __name__=="__main__":
    unittest.main()
