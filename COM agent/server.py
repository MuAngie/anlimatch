"""Local workbench, persisted records and a separate authenticated admin console."""
import argparse
import base64
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

from engine import DEMO_TEXT, TODAY, demo_catalog, read_excel
from generator import generate_output, select_products, held, retry_review
from generator import PROMPT as COMPOSE_PROMPT
from evaluator import PROMPT as EVAL_PROMPT
from store import Store
from tasks import Tasks
from task_runtime import clinical_case, TaskCancelled, progress
from report_editor import edit_report
from needs import PROMPT as NEEDS_PROMPT, extract_needs
from matching import PROMPT as MATCHING_PROMPT
from archive_demo import complete_demo_catalog
from learning import PROMPT as LEARNING_PROMPT, validate_case, distill, rule_context

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT.parent / "archive"


def prompts():
    return [{"key":key, "name":name, "description":description, "text":text,
             "version":hashlib.sha256(text.encode()).hexdigest()[:12],
             "schema":json.loads((ROOT / schema).read_text(encoding="utf-8"))}
            for key, name, description, text, schema in (
                ("needs", "需求提取", "从专业分析整理有原文依据的需求，使用者确认后进入选品", NEEDS_PROMPT, "needs_schema.json"),
                ("matching", "产品关联判断", "结合成分、功能描述和依据比较所有允许评估的商品，保留原文引用及待核验原因", MATCHING_PROMPT, "matching_schema.json"),
                ("compose", "组合说明", "按四章报告整理主要问题及选品理由；用量、使用方式、报价和原始资料由程序呈现", COMPOSE_PROMPT, "compose_schema.json"),
                ("eval", "独立复核", "核对四章报告与原始依据，检查选品、用量、禁用商品、重复成分与禁忌；提醒保留，明确冲突拦截", EVAL_PROMPT, "eval_schema.json"),
                ("learning", "人工案例规则提炼", "管理员手动提炼单份案例，草案审核后启用；生成时不读取完整案例", LEARNING_PROMPT, "learning_schema.json"))]


def archived_products():
    products = []
    for path in sorted(ARCHIVE.glob("*.xlsx")):
        products.extend(read_excel(path))
    return complete_demo_catalog(products)


class Handler(BaseHTTPRequestHandler):
    store = None
    login_attempts = {}
    login_lock = threading.Lock()

    task_lock = threading.Lock()
    task_manager = None

    def tasks(self):
        with Handler.task_lock:
            if Handler.task_manager is None or Handler.task_manager.store is not self.store:
                Handler.task_manager = Tasks(self.store)
            return Handler.task_manager

    def validate_task_request(self, workspace, payload):
        progress()
        if '_save_version' not in payload: return
        user = next((u for u in self.store.workspace_users(workspace) if u['key']==payload.get('user_key')),None)
        if not user or user['revision'] != payload.get('user_revision') or any(user['fields'].get(k,'') != v for k,v in payload.get('_input_fields',{}).items()):
            raise ValueError('用户输入已更新，旧任务不会覆盖新资料，请按当前输入继续')
        if payload.get('_prompt_versions') != [p['version'] for p in prompts()]:
            raise ValueError('处理规则已更新，请按当前输入重新开始')
        kind = payload.get('catalog_kind')
        saved = self.store.catalogs(workspace)
        current = saved.get(kind)
        if kind == 'archive': current = complete_demo_catalog(current) if current is not None else archived_products()
        elif kind == 'demo' and current is None: current = demo_catalog()
        elif kind == 'imported' and current is None: current = []
        if kind and current != payload.get('catalog'):
            raise ValueError('产品库已更新，请刷新后重新选品')

    def cookie(self, name):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        return cookies[name].value if name in cookies else ""

    def set_cookie(self, name, value, max_age):
        self.response_cookies.append(f"{name}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}")

    def workspace(self):
        token = self.cookie("hc_workspace")
        if not token or len(token) != 43:
            token = secrets.token_urlsafe(32)
            self.set_cookie("hc_workspace", token, 365*24*3600)
        return hashlib.sha256(token.encode()).hexdigest()

    def prepare(self):
        self.response_cookies = []
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.reject_request("只接受本机站点请求")
            return False
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.reject_request("只接受本应用页面提交")
            return False
        return True

    def reject_request(self, message):
        # Consume ordinary rejected POST bodies so Windows does not reset the response socket.
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 15_000_000:
                    self.connection.settimeout(10)
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
        self.send({"error":message}, 403)

    def require_admin(self):
        if not self.store.admin(self.cookie("hc_admin")):
            raise PermissionError("需要管理员登录")

    def learning_catalog(self):
        products = {p['id']:p for p in demo_catalog()+archived_products()}
        for rows in self.store.catalogs(self.workspace()).values():
            for p in rows: products[p['id']] = p
        return list(products.values())
    def log_message(self, *args):
        pass  # Do not write patient content or requests to access logs.

    def send(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        for cookie in self.response_cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.prepare():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/bootstrap":
                saved = self.store.catalogs(self.workspace())
                if 'archive' in saved: saved['archive'] = complete_demo_catalog(saved['archive'])
                return self.send({"text": DEMO_TEXT, "demo_catalog": demo_catalog(), "archive_catalog": archived_products(), "today": TODAY(),
                                  "saved_catalogs":saved})
            if path == '/api/tasks':
                query = parse_qs(parsed.query)
                return self.send(self.tasks().get(self.workspace(),query['id'][0]) if query.get('id') else {'tasks':self.tasks().latest(self.workspace())})
            if path == "/api/users":
                return self.send({"users":self.store.workspace_users(self.workspace())})
            if path == "/api/admin/status":
                return self.send({"initialized":self.store.initialized(), "account":self.store.admin(self.cookie("hc_admin"))})
            if path.startswith("/api/admin/"):
                self.require_admin()
                query = parse_qs(parsed.query)
                if path == "/api/admin/users":
                    return self.send({"users":self.store.users()})
                if path == "/api/admin/user":
                    record = self.store.user(query.get("key", [""])[0])
                elif path == "/api/admin/history":
                    offset = max(0, int(query.get("offset", ["0"])[0]))
                    return self.send(self.store.histories(offset, query.get("user_key", [""])[0]))
                elif path == "/api/admin/history/detail":
                    record = self.store.history(query.get("id", [""])[0])
                elif path == "/api/admin/prompts":
                    return self.send({"prompts":prompts(), "rules":["用户资料保存", "Excel / 产品库导入", "禁用商品与标签用量校验", "成分合计与报价计算"]})
                elif path == '/api/admin/learning':
                    return self.send({'cases':self.store.learning_cases(),'rules':self.store.learned_rules(),
                        'products':[{k:p.get(k) for k in ('id','name','unit','daily_units')} for p in self.learning_catalog()]})
                elif path == '/api/admin/learning/case':
                    record = self.store.learning_case(query.get('id',[''])[0])
                elif path == '/api/admin/learning/versions':
                    return self.send({'versions':self.store.rule_versions(query.get('id',[''])[0])})
                else:
                    return self.send({"error":"接口不存在"}, 404)
                return self.send(record if record else {"error":"记录不存在"}, 200 if record else 404)
            if path not in ("/", "/app.js", "/style.css", "/admin", "/admin/", "/admin.js", "/admin.css", "/learning.js"):
                return self.send({"error": "页面不存在"}, 404)
            file = ROOT / "web" / ("index.html" if path == "/" else "admin.html" if path in ("/admin", "/admin/") else path[1:])
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)
        except PermissionError as exc:
            self.send({"error":str(exc)}, 401)
        except (ValueError, OSError, ImportError) as exc:
            self.send({"error": str(exc)}, 400)

    def model_action(self, path, payload, workspace, resume=None, checkpoint=None):
        history_id = None
        try:
            self.validate_task_request(workspace,payload)
            if path == "/api/extract":
                user_key, revision = payload.get("user_key"), payload.get("user_revision")
                owner = next((u for u in self.store.workspace_users(workspace) if u["key"] == user_key), None)
                if not owner:
                    raise PermissionError("用户不属于当前工作空间")
                if owner["revision"] != revision:
                    raise ValueError("用户资料已更新，请重新提取")
                history_id = self.store.start_history(workspace,user_key,revision,"extract",payload,
                    [p for p in prompts() if p["key"] == "needs"])
                result = extract_needs(payload["case"],payload["catalog"])
                result["history_id"] = history_id
                self.store.finish_history(history_id,result,result["status"])
                return result
            if path == "/api/reports/edit":
                user_key, revision, source_id = payload.get("user_key"), payload.get("user_revision"), payload.get("history_id")
                context = self.store.report_context(workspace, user_key, revision, source_id)
                changes = payload.get("changes")
                original = context["request"]
                request = {"case":original["case"],
                           "catalog":original["catalog"], "source_history_id":source_id, "changes":changes}
                history_id = self.store.start_history(workspace, user_key, revision, "report_edit", request,
                                                     [p for p in prompts() if p["key"] == "eval"])
                result = edit_report(original["case"], original["catalog"], context["result"]["report"], changes)
                result["history_id"] = history_id
                self.store.finish_edit(workspace, user_key, revision, source_id, history_id, result)
                return result
            if path in ("/api/select", "/api/compose"):
                user_key, revision = payload.get("user_key"), payload.get("user_revision")
                if payload.get("extraction_id"):
                    reuse = payload.get("reuse_extraction") is True
                    context = self.store.extraction_context(workspace,user_key,revision,payload["extraction_id"],allow_reuse=reuse)
                    if not reuse and (clinical_case(payload["case"]) != clinical_case(context["request"]["case"])):
                        raise ValueError("资料或产品库与提取时不一致，请重新提取")
                    indices = payload.get("confirmed_goals")
                    if (not isinstance(indices,list) or not indices
                            or any(type(i) is not int or i < 0 or i >= len(context["result"]["goals"]) for i in indices)
                            or len(set(indices)) != len(indices)):
                        raise ValueError("请确认至少一个已提取目标")
                    payload["case"] = dict(payload["case"], goal_context={
                        "goals":[context["result"]["goals"][i] for i in indices],
                        "summary":context["result"]["summary"], "cautions":context["result"]["cautions"],
                        "missing":context["result"]["missing"], "model":context["result"]["model"]})
                    if reuse:
                        payload["case"]["goal_context"].update(reuse_confirmed=True,source_analysis=context["request"]["case"]["text"])
                elif payload["case"].get("goal_context"):
                    raise ValueError("模型提取目标需关联本用户的提取记录并确认")
                if not user_key:
                    # Existing local API clients can still submit a case; bind it to their own workspace.
                    profile = payload["case"].get("profile") or {}
                    fields = {"user-id":str(profile.get("id") or ""), "user-name":str(profile.get("name") or ""), "analysis-text":str(payload["case"].get("text") or "")}
                    existing = next((u for u in self.store.workspace_users(workspace) if u["fields"].get("user-id") == fields["user-id"]), None)
                    user_key = existing["key"] if existing else str(uuid.uuid4())
                    revision = existing["revision"] + 1 if existing else 0
                    self.store.save_user(workspace, {"key":user_key, "revision":revision, "fields":fields, "demo":False})
                if path == '/api/select':
                    learned = rule_context(payload['case'],payload['catalog'],self.store.learned_rules(active=True))
                    history_id = self.store.start_history(workspace,user_key,revision,'select',payload,[p for p in prompts() if p['key']=='matching'])
                    result = select_products(payload['case'],payload['catalog'],**({'learned':learned} if learned['rules'] or learned['excluded_ids'] else {}))
                    if learned != rule_context(payload['case'],payload['catalog'],self.store.learned_rules(active=True)):
                        result = {'status':'error','summary':'选品期间适用的人工规则已变更，请重新选品。','products':[],'matching':None}
                    result['learning'] = learned
                    result['confirmed_goals'] = payload.get('confirmed_goals',[])
                    result['history_id'] = history_id
                    self.validate_task_request(workspace,payload)
                    self.store.finish_history(history_id,result,result['status'])
                    return result
                selection = None
                learned = rule_context(payload['case'],payload['catalog'],self.store.learned_rules(active=True))
                if payload.get('selection_id'):
                    context = self.store.selection_context(workspace,user_key,revision,payload['selection_id'])
                    if clinical_case(payload['case']) != clinical_case(context['request']['case']) or payload['catalog'] != context['request']['catalog']:
                        raise ValueError('用户需求或产品库已变化，请重新选品并确认')
                    selection = context['result']
                    if selection.get('learning',{'rules':[],'excluded_ids':[]}) != learned:
                        raise ValueError('适用的人工规则已更新或停用，请重新选品并确认')
                elif payload.get('extraction_id'):
                    raise ValueError('请先选品并确认选品结果，再生成完整报告')
                history_id = self.store.start_history(workspace, user_key, revision, "compose", payload,
                    [p for p in prompts() if p["key"] in (("compose","eval") if selection else ("matching","compose","eval"))])
                if resume is not None:
                    if resume['report'].get('learning',{'rules':[],'excluded_ids':[]}) != learned:
                        raise ValueError('人工规则已变更，请重新选品')
                    if set(payload.get('confirmed_products',[])) != {p['id'] for p in resume['report']['selected_products']}:
                        raise ValueError('商品勾选已改变，请重新生成')
                    result = retry_review(payload['case'],payload['catalog'],resume,reprice=bool(payload.get('reprice_task_id')),checkpoint=checkpoint)
                    if learned != rule_context(payload['case'],payload['catalog'],self.store.learned_rules(active=True)):
                        raise ValueError('复核期间适用的人工规则已修改，请重新选品')
                    result['history_id'] = history_id
                    self.validate_task_request(workspace,payload)
                    self.store.finish_history(history_id,result,result['evaluation']['verdict'])
                    return result
                learning_args = {'learned':learned} if learned['rules'] or learned['excluded_ids'] else {}
                if checkpoint: learning_args['checkpoint'] = checkpoint
                result = (generate_output(payload["case"], payload["catalog"],selection=selection,confirmed_products=payload.get('confirmed_products'),**learning_args)
                          if selection else generate_output(payload["case"], payload["catalog"],**learning_args))
                if learned != rule_context(payload['case'],payload['catalog'],self.store.learned_rules(active=True)):
                    result = dict(held('needs_review','生成期间适用的人工规则已修改或停用，请重新选品后生成报告。',stage='人工规则检查'),learning=learned)
                result["history_id"] = history_id
                self.validate_task_request(workspace,payload)
                self.store.finish_history(history_id, result, result["evaluation"]["verdict"])
                return result
        except TaskCancelled:
            raise
        except Exception as exc:
            if history_id: self.store.finish_history(history_id, {'error':str(exc)}, 'error')
            raise

    def do_POST(self):
        if not self.prepare():
            return
        history_id = None
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请求必须为JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 15_000_000:
                raise ValueError("请求为空或超过15MB")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求应为JSON对象")
            if self.path == "/api/admin/setup":
                if self.store.initialized():
                    return self.send({"error":"管理员已设置，请登录"}, 409)
                self.store.setup(payload.get("username"), payload.get("password"))
                return self.send({"ok":True})
            if self.path == "/api/admin/login":
                with self.login_lock:
                    attempts = [t for t in self.login_attempts.get(self.client_address[0], []) if t > time.time()-300]
                    self.login_attempts[self.client_address[0]] = attempts
                    if len(attempts) >= 5:
                        return self.send({"error":"登录失败次数过多，请5分钟后重试"}, 429)
                    token = self.store.login(payload.get("username"), payload.get("password"))
                    if not token:
                        attempts.append(time.time())
                        return self.send({"error":"账号或密码不正确"}, 401)
                    self.login_attempts.pop(self.client_address[0], None)
                self.set_cookie("hc_admin", token, 8*3600)
                return self.send({"account":self.store.admin(token)})
            if self.path.startswith("/api/admin/"):
                self.require_admin()
                if self.path == "/api/admin/logout":
                    self.store.logout(self.cookie("hc_admin"))
                    self.set_cookie("hc_admin", "", 0)
                    return self.send({"ok":True})
                actor = self.store.admin(self.cookie('hc_admin'))['username']
                if self.path == '/api/admin/learning/case':
                    key = self.store.create_case(validate_case(payload,self.learning_catalog()),actor)
                    return self.send({'id':key})
                if self.path == '/api/admin/learning/distill':
                    source = self.store.learning_case(payload.get('case_id'))
                    history_id = self.store.start_history(self.workspace(),None,None,'learning',{'case_id':source['id']},[p for p in prompts() if p['key']=='learning'])
                    result = distill(source)
                    result['rules'] = [self.store.save_rule(source['id'],rule,actor) for rule in result['rules']]
                    self.store.finish_history(history_id,result,'completed')
                    return self.send(result)
                if self.path == '/api/admin/learning/rule':
                    return self.send(self.store.save_rule(payload.get('case_id'),payload.get('rule'),actor,payload.get('id'),payload.get('revision')))
                if self.path == '/api/admin/learning/state':
                    if payload.get('status')=='active' and payload.get('reviewed') is not True: raise ValueError('请先审核适用条件、依据和局限，再确认启用')
                    return self.send(self.store.rule_state(payload.get('id'),payload.get('revision'),payload.get('status'),actor))
                return self.send({"error":"接口不存在"}, 404)
            workspace = self.workspace()
            if self.path == "/api/users/save":
                saved = self.store.save_user(workspace, payload)
                if not saved: return self.send({'error':'其他窗口已更新此用户。当前输入已保留，请下载草稿或重新载入已保存资料。','conflict':True},409)
                user = next(u for u in self.store.workspace_users(workspace) if u['key']==payload['key'])
                return self.send({'saved':True,'save_version':user['save_version']})
            if self.path == "/api/catalogs/save":
                self.store.save_catalog(workspace, payload.get("kind"), payload.get("products"))
                return self.send({"saved":True})
            if self.path in ('/api/extract','/api/select','/api/compose','/api/reports/edit'):
                return self.send(self.model_action(self.path,payload,workspace))
            if self.path in ('/api/tasks/start','/api/tasks/retry','/api/tasks/cancel'):
                tasks = self.tasks()
                if self.path == '/api/tasks/cancel': return self.send(tasks.cancel(workspace,payload.get('id')))
                resume = None
                if self.path == '/api/tasks/retry':
                    previous = tasks.get(workspace,payload.get('id'),private=True)
                    if not previous['can_retry']: raise ValueError('此任务不能重试')
                    action, request, resume = previous['action'],previous['payload'],previous['checkpoint']
                else:
                    action, request = payload.get('action'),payload.get('request')
                    if action not in ('/api/extract','/api/select','/api/compose'): raise ValueError('任务类型不正确')
                    if not isinstance(request,dict): raise ValueError('任务参数不正确')
                    request = dict(request)
                    user = next((u for u in self.store.workspace_users(workspace) if u['key']==request.get('user_key')),None)
                    if not user: raise PermissionError('用户不属于当前工作空间')
                    request['_save_version'] = user['save_version']
                    names = ('user-name','user-id','age','pregnancy','allergies','conditions','medications','supplements','forbidden-products','analysis-text','reviewer','review-date','analysis-approved','days','budget','shipping','discount','catalog-select','needs-choice-id','needs-choices','selection-choice-id','selection-choices')
                    request['_input_fields'] = {k:user['fields'].get(k,'') for k in names}
                    request['_prompt_versions'] = [p['version'] for p in prompts()]
                    if request.get('reprice_task_id'):
                        previous = tasks.get(workspace,request['reprice_task_id'],private=True)
                        if action != '/api/compose' or previous['user_key'] != user['key'] or not previous['checkpoint']:
                            raise ValueError('没有可重算的当前用户草案')
                        resume = previous['checkpoint']
                self.validate_task_request(workspace,request)
                return self.send(tasks.start(workspace,action,request,
                    lambda a,p,r,c:self.model_action(a,p,workspace,r,c),resume),202)
            if self.path == "/api/import":
                name = Path(payload.get("filename", "products.xlsx")).name
                history_id = self.store.start_history(workspace, None, None, "import", {"filename":name}, [])
                content = base64.b64decode(payload["data"], validate=True)
                if name.endswith(".json"):
                    products = json.loads(content.decode("utf-8-sig"))
                    if not isinstance(products, list) or not products:
                        raise ValueError("备份须为产品JSON数组")
                    for p in products:
                        if not isinstance(p, dict) or not all(k in p for k in ("id", "name", "sku", "goals", "price", "ingredients", "source")):
                            raise ValueError("JSON格式不匹配，请导入本应用下载的产品库备份")
                    if len({p["id"] for p in products}) != len(products):
                        raise ValueError("产品备份有重复ID")
                elif name.endswith(".xlsx"):
                    products = read_excel(content, name)
                else:
                    raise ValueError("支持.xlsx和本应用产品库.json备份；请将.xls另存为.xlsx")
                self.store.finish_history(history_id, {"count":len(products)}, "completed")
                self.send({"products": products})
            else:
                self.send({"error": "接口不存在"}, 404)
        except PermissionError as exc:
            self.send({"error":str(exc)}, 403)
        except Exception as exc:
            # User-facing error, no traceback or health-data echo.
            if history_id:
                self.store.finish_history(history_id, {"error":str(exc)}, "error")
            self.send({"error": str(exc)}, 400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    Handler.store = Store(ROOT / "data" / "health.sqlite3")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Health Composer: http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
