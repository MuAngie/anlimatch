"""Local records and administrator authentication; no model keys are stored here."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid
from task_runtime import task_id, TaskCancelled
from model_usage import events


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    key TEXT PRIMARY KEY, workspace TEXT NOT NULL, user_id TEXT NOT NULL,
                    name TEXT NOT NULL, demo INTEGER NOT NULL, fields TEXT NOT NULL,
                    revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS users_number ON users(workspace,user_id) WHERE user_id<>'';
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, user_key TEXT,
                    user_revision INTEGER, user_name TEXT, user_id TEXT, action TEXT NOT NULL,
                    status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    request TEXT NOT NULL, result TEXT, prompts TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS history_user ON history(user_key,started_at);
                CREATE TABLE IF NOT EXISTS catalogs (workspace TEXT NOT NULL, kind TEXT NOT NULL,
                    products TEXT NOT NULL, PRIMARY KEY(workspace,kind));
                CREATE TABLE IF NOT EXISTS learning_cases (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    data TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS learned_rules (id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
                    status TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
                    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rule_versions (rule_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    data TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(rule_id,revision));
            """)
            if 'save_version' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
                db.execute('ALTER TABLE users ADD COLUMN save_version INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialized(self):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM admins").fetchone() is not None

    def create_case(self, data, actor):
        key = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('INSERT INTO learning_cases VALUES(?,?,?,?,?)',(key,data['title'],encode(data),actor,now()))
        return key

    def learning_cases(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,title,created_by,created_at FROM learning_cases ORDER BY created_at DESC')]

    def learning_case(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM learning_cases WHERE id=?',(key,)).fetchone()
            if not row: raise ValueError('来源案例不存在')
            return dict(row, data=json.loads(row['data']))

    def learned_rules(self, active=False):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM learned_rules"+(' WHERE status=\'active\'' if active else '')+' ORDER BY updated_at DESC').fetchall()
            return [dict(json.loads(row['data']),id=row['id'],case_id=row['case_id'],status=row['status'],revision=row['revision'],updated_by=row['updated_by'],updated_at=row['updated_at']) for row in rows]

    def save_rule(self, case_id, data, actor, key=None, revision=None):
        from learning import validate_rule, fingerprint
        source = self.learning_case(case_id)
        validated = validate_rule(data,source)
        validated.update(case_title=source['title'],product_fingerprints={p:fingerprint(source['data']['labels'][p]) for p in validated['product_ids']})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if key:
                old = db.execute('SELECT * FROM learned_rules WHERE id=?',(key,)).fetchone()
                if not old or old['revision']!=revision or old['case_id']!=case_id: raise ValueError('规则已更新，请刷新后修改')
                new_revision = revision+1
                db.execute("UPDATE learned_rules SET status='draft',revision=?,data=?,updated_by=?,updated_at=? WHERE id=?",
                           (new_revision,encode(validated),actor,now(),key))
            else:
                key,new_revision = str(uuid.uuid4()),1
                db.execute('INSERT INTO learned_rules VALUES(?,?,?,?,?,?,?)',(key,case_id,'draft',new_revision,encode(validated),actor,now()))
            snapshot = dict(validated,id=key,case_id=case_id,status='draft',revision=new_revision)
            db.execute('INSERT INTO rule_versions VALUES(?,?,?,?,?)',(key,new_revision,encode(snapshot),actor,now()))
        return snapshot

    def rule_state(self, key, revision, status, actor):
        if status not in ('active','inactive'): raise ValueError('规则状态不正确')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM learned_rules WHERE id=?',(key,)).fetchone()
            if not row or row['revision']!=revision: raise ValueError('规则已更新，请刷新后审核')
            data = json.loads(row['data'])
            if status=='active': data.update(reviewed_by=actor,reviewed_at=now())
            new_revision = revision+1
            db.execute('UPDATE learned_rules SET status=?,revision=?,data=?,updated_by=?,updated_at=? WHERE id=?',
                       (status,new_revision,encode(data),actor,now(),key))
            snapshot = dict(data,id=key,case_id=row['case_id'],status=status,revision=new_revision)
            db.execute('INSERT INTO rule_versions VALUES(?,?,?,?,?)',(key,new_revision,encode(snapshot),actor,now()))
        return snapshot

    def rule_versions(self, key):
        with self.connect() as db:
            return [dict(row,data=json.loads(row['data'])) for row in db.execute('SELECT * FROM rule_versions WHERE rule_id=? ORDER BY revision DESC',(key,))]

    def setup(self, username, password):
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
            raise ValueError("管理员账号需为3至40位字母、数字、点、下划线或短横线")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ValueError("管理员密码需为8至128个字符")
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM admins").fetchone():
                raise PermissionError("管理员已设置，请登录")
            db.execute("INSERT INTO admins VALUES(1,?,?,?,?)", (username, salt, digest, now()))

    def login(self, username, password):
        with self.connect() as db:
            account = db.execute("SELECT * FROM admins WHERE id=1").fetchone()
            if not account or not isinstance(password, str) or len(password) > 128:
                return None
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), account["salt"], 600_000)
            if username != account["username"] or not hmac.compare_digest(digest, account["password_hash"]):
                return None
            token = secrets.token_urlsafe(32)
            db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?)", (hashlib.sha256(token.encode()).hexdigest(), time.time() + 8*3600))
            return token

    def admin(self, token):
        if not token:
            return None
        with self.connect() as db:
            session = db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires>?",
                                 (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if session:
                return {"username": db.execute("SELECT username FROM admins WHERE id=1").fetchone()[0], "role": "admin"}

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def save_user(self, workspace, user):
        key, fields, revision = user.get("key"), user.get("fields"), user.get("revision")
        try:
            uuid.UUID(key)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("用户记录编号不正确") from None
        if (not isinstance(fields, dict) or any(not isinstance(v, (str, bool)) for v in fields.values())
                or not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
            raise ValueError("用户资料格式不正确")
        user_id, name = fields.get("user-id", ""), fields.get("user-name", "")
        if not isinstance(user_id, str) or not isinstance(name, str) or len(user_id) > 80 or len(name) > 60:
            raise ValueError("用户编号或称呼格式不正确")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM users WHERE key=?", (key,)).fetchone()
            if existing and existing["workspace"] != workspace:
                raise PermissionError("不能修改其他工作空间的用户")
            if existing:
                identical = (existing['revision'] == revision and json.loads(existing['fields']) == fields
                             and bool(existing['demo']) == bool(user.get('demo')))
                if identical:
                    return True
                expected = user.get('expected_version')
                if (existing['revision'] > revision or
                        ('expected_version' in user and (type(expected) is not int or expected != existing['save_version'])) or
                        ('expected_version' not in user and existing['revision'] == revision)):
                    return False
            elif user.get('expected_version', 0) != 0:
                return False
            try:
                db.execute("""INSERT INTO users(key,workspace,user_id,name,demo,fields,revision,created_at,updated_at,save_version)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(key) DO UPDATE SET user_id=excluded.user_id,name=excluded.name,
                    demo=excluded.demo,fields=excluded.fields,revision=excluded.revision,updated_at=excluded.updated_at,
                    save_version=excluded.save_version""",
                           (key, workspace, user_id.strip(), name.strip(), bool(user.get("demo")), encode(fields), revision, now(), now(),
                            existing['save_version']+1 if existing else 1))
            except sqlite3.IntegrityError:
                raise ValueError("用户编号已存在，请使用不同编号") from None
        return True

    def workspace_users(self, workspace):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM users WHERE workspace=? ORDER BY created_at,key", (workspace,)).fetchall()
            users = []
            for row in rows:
                last = db.execute("""SELECT id,result,request,started_at FROM history WHERE user_key=?
                    AND (action='compose' OR (action='report_edit' AND status IN ('pass','needs_review') AND json_extract(result,'$.report') IS NOT NULL))
                    AND user_revision=? ORDER BY started_at DESC LIMIT 1""", (row["key"], row["revision"])).fetchone()
                result = json.loads(last["result"]) if last and last["result"] else None
                if result:
                    result["history_id"] = last["id"]
                extraction = db.execute("""SELECT id,result,user_revision FROM history WHERE user_key=?
                    AND action='extract' AND result IS NOT NULL ORDER BY started_at DESC LIMIT 1""", (row["key"],)).fetchone()
                needs = json.loads(extraction["result"]) if extraction and extraction["result"] else None
                if needs:
                    needs["history_id"] = extraction["id"]
                    if extraction["user_revision"] != row["revision"]:
                        needs["stale"] = True
                selected = db.execute("SELECT id,result,started_at FROM history WHERE user_key=? AND user_revision=? AND action='select' ORDER BY started_at DESC LIMIT 1",
                                      (row["key"],row["revision"])).fetchone()
                selection = json.loads(selected["result"]) if selected and selected["result"] else None
                if selection: selection["history_id"] = selected["id"]
                fields = json.loads(row["fields"])
                if selected and (not last or selected['started_at'] > last['started_at']): result = None
                if result and selected and fields.get('selection-choice-id') == selected['id']:
                    confirmed = (result.get('matching') or {}).get('confirmed_products',[])
                    if fields.get('selection-choices') != json.dumps(confirmed,ensure_ascii=False,separators=(',',':')): result = None
                if result:
                    old_case = json.loads(last['request']).get('case', {})
                    for name in ('days','budget','shipping','discount'):
                        if name not in fields: continue
                        current = fields[name]
                        try: current = float(current) if current not in ('', None) else (0 if name in ('shipping','discount') else None)
                        except (ValueError, TypeError): result = None; break
                        if current != old_case.get(name, 0 if name in ('shipping','discount') else None): result = None
                if fields.get('needs-choice-id') and selected:
                    request = db.execute('SELECT request FROM history WHERE id=?',(selected['id'],)).fetchone()
                    if json.loads(request['request']).get('confirmed_goals',[]) != json.loads(fields.get('needs-choices','[]')):
                        selection = None; result = None
                if fields.get('catalog-invalidated') == 'true': selection = None; result = None
                users.append({"key":row["key"], "fields":fields, "demo":bool(row["demo"]),
                              "revision":row["revision"], "save_version":row['save_version'], "result":result, "extraction":needs,"selection":selection})
            return users

    def extraction_context(self, workspace, user_key, revision, history_id, allow_reuse=False):
        with self.connect() as db:
            user = db.execute("SELECT revision FROM users WHERE key=? AND workspace=?", (user_key,workspace)).fetchone()
            if not user:
                raise PermissionError("用户不属于当前工作空间")
            row = db.execute("""SELECT id,result,request FROM history WHERE user_key=? AND (? OR user_revision=?)
                AND action='extract' ORDER BY started_at DESC LIMIT 1""",(user_key,allow_reuse,revision)).fetchone()
            if user["revision"] != revision or not row or row["id"] != history_id:
                raise ValueError("资料或需求提取结果已更新，请重新提取并确认")
            result = json.loads(row["result"]) if row["result"] else None
            if not result or result.get("status") != "ready":
                raise ValueError("需求尚未完成提取，请先核对目标或补充资料")
            return {"request":json.loads(row["request"]), "result":result}

    def selection_context(self, workspace, user_key, revision, history_id):
        with self.connect() as db:
            user = db.execute('SELECT revision FROM users WHERE key=? AND workspace=?',(user_key,workspace)).fetchone()
            if not user: raise PermissionError('用户不属于当前工作空间')
            row = db.execute("SELECT id,request,result FROM history WHERE user_key=? AND user_revision=? AND action='select' ORDER BY started_at DESC LIMIT 1",(user_key,revision)).fetchone()
            if user["revision"] != revision or not row or row["id"] != history_id:
                raise ValueError('选品结果已更新，请重新选品并确认')
            result = json.loads(row["result"]) if row["result"] else None
            if not result or result.get('status') != 'ready':
                raise ValueError('选品尚未完成或产品资料待补充')
            return {"request":json.loads(row["request"]),"result":result}

    def report_context(self, workspace, user_key, revision, history_id):
        with self.connect() as db:
            user = db.execute("SELECT revision FROM users WHERE key=? AND workspace=?", (user_key, workspace)).fetchone()
            if not user:
                raise PermissionError("用户不属于当前工作空间")
            latest = db.execute("""SELECT id,result,request FROM history WHERE user_key=? AND user_revision=?
                AND (action='compose' OR (action='report_edit' AND status IN ('pass','needs_review') AND json_extract(result,'$.report') IS NOT NULL))
                ORDER BY started_at DESC LIMIT 1""", (user_key, revision)).fetchone()
            if user["revision"] != revision or not latest or latest["id"] != history_id:
                raise ValueError("用户资料或报告已更新，请刷新后编辑最新报告")
            result = json.loads(latest["result"]) if latest["result"] else None
            if not result or not result.get("report") or result.get("evaluation", {}).get("verdict") not in ("pass", "needs_review"):
                raise ValueError("仅能编辑已生成且无明确冲突的组合草案")
            return {"request":json.loads(latest["request"]), "result":result}

    def finish_edit(self, workspace, user_key, revision, source_id, key, result):
        result = dict(result, model_usage=list(events.get() or []))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.report_context(workspace, user_key, revision, source_id)
            db.execute("UPDATE history SET result=?,status=?,finished_at=? WHERE id=?",
                       (encode(result), result["evaluation"]["verdict"], now(), key))

    def save_catalog(self, workspace, kind, products):
        if kind not in ("demo", "archive", "imported") or not isinstance(products, list):
            raise ValueError("产品库格式不正确")
        if any(not isinstance(p, dict) or not all(k in p for k in ("id","name","sku","goals","price","ingredients","source")) for p in products):
            raise ValueError("产品资料格式不正确")
        with self.connect() as db:
            db.execute("INSERT INTO catalogs VALUES(?,?,?) ON CONFLICT(workspace,kind) DO UPDATE SET products=excluded.products",
                       (workspace, kind, encode(products)))

    def catalogs(self, workspace):
        with self.connect() as db:
            return {row["kind"]:json.loads(row["products"]) for row in db.execute("SELECT * FROM catalogs WHERE workspace=?", (workspace,))}

    def start_history(self, workspace, user_key, revision, action, request, prompts):
        events.set([])
        key = str(uuid.uuid4())
        with self.connect() as db:
            user = db.execute("SELECT * FROM users WHERE key=? AND workspace=?", (user_key, workspace)).fetchone() if user_key else None
            if user_key and not user:
                raise PermissionError("用户不属于当前工作空间")
            db.execute("INSERT INTO history VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (key, workspace, user_key, revision, user["name"] if user else "", user["user_id"] if user else "",
                        action, "running", now(), None, encode(request), None, encode(prompts)))
            if task_id.get():
                task = db.execute('SELECT status FROM tasks WHERE id=?',(task_id.get(),)).fetchone()
                if not task or task['status'] != 'running': raise TaskCancelled('任务已取消')
                db.execute('UPDATE tasks SET history_id=? WHERE id=?',(key,task_id.get()))
        return key

    def finish_history(self, key, result, status):
        result = dict(result, model_usage=list(events.get() or []))
        if task_id.get(): result = dict(result, task_id=task_id.get())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if task_id.get():
                task = db.execute('SELECT status FROM tasks WHERE id=?',(task_id.get(),)).fetchone()
                if not task or task['status'] != 'running': raise TaskCancelled('任务已取消')
            db.execute("UPDATE history SET result=?,status=?,finished_at=? WHERE id=?", (encode(result), status, now(), key))

    def users(self):
        with self.connect() as db:
            rows = db.execute("""SELECT u.*,COUNT(h.id) AS usage_count,MAX(h.started_at) AS last_used
                FROM users u LEFT JOIN history h ON h.user_key=u.key GROUP BY u.key ORDER BY u.updated_at DESC""").fetchall()
            return [{k:row[k] for k in ("key","user_id","name","demo","created_at","updated_at","usage_count","last_used")} for row in rows]

    def user(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE key=?", (key,)).fetchone()
            return {"key":row["key"], "fields":json.loads(row["fields"]), "created_at":row["created_at"],
                    "updated_at":row["updated_at"]} if row else None

    def histories(self, offset=0, user_key=""):
        with self.connect() as db:
            where, params = ("WHERE user_key=?", [user_key]) if user_key else ("", [])
            total = db.execute("SELECT COUNT(*) FROM history " + where, params).fetchone()[0]
            rows = db.execute("SELECT id,user_key,user_name,user_id,action,status,started_at,finished_at FROM history "
                              + where + " ORDER BY started_at DESC LIMIT 30 OFFSET ?", params + [offset]).fetchall()
            return {"items":[dict(row) for row in rows], "total":total, "offset":offset, "limit":30}

    def history(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM history WHERE id=?", (key,)).fetchone()
            if not row:
                return None
            result = dict(row)
            for field in ("request", "result", "prompts"):
                result[field] = json.loads(result[field]) if result[field] else None
            result.pop("workspace")
            return result
