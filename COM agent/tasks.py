"""Small durable task runner for the local application's model stages."""
from copy import deepcopy
import json
import threading
import uuid

from store import encode, now
from task_runtime import observer, task_id, TaskCancelled


class Tasks:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, workspace TEXT NOT NULL, user_key TEXT NOT NULL,
                action TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT, result TEXT, error TEXT,
                checkpoint TEXT, history_id TEXT)''')
            db.execute("UPDATE history SET status='interrupted',result=NULL WHERE id IN (SELECT history_id FROM tasks WHERE status='running')")
            db.execute("UPDATE tasks SET status='interrupted',error='服务已重启，可重试未完成步骤',finished_at=? WHERE status='running'", (now(),))

    def get(self, workspace, key, private=False):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=? AND workspace=?',(key,workspace)).fetchone()
        if not row: raise PermissionError('任务不属于当前工作空间')
        data = dict(row)
        for name in ('payload','result','checkpoint'):
            data[name] = json.loads(data[name]) if data[name] else None
        data['user_revision'] = data['payload'].get('user_revision')
        data['can_retry'] = data['status'] in ('error','interrupted','cancelled')
        data['retry_review'] = bool(data['checkpoint']) and data['can_retry']
        if not private:
            for name in ('workspace','payload','checkpoint'): data.pop(name)
        return data

    def latest(self, workspace):
        with self.store.connect() as db:
            rows = db.execute('SELECT id,user_key FROM tasks WHERE workspace=? ORDER BY started_at DESC',(workspace,)).fetchall()
        seen, result = set(), []
        for row in rows:
            if row['user_key'] not in seen:
                result.append(self.get(workspace,row['id'])); seen.add(row['user_key'])
        return result

    def start(self, workspace, action, payload, execute, checkpoint=None):
        key, user_key = str(uuid.uuid4()), payload.get('user_key')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT * FROM users WHERE key=? AND workspace=?',(user_key,workspace)).fetchone()
            if not user: raise PermissionError('用户不属于当前工作空间')
            if user['revision'] != payload.get('user_revision'): raise ValueError('用户资料已更新，请重新载入')
            active = db.execute("SELECT id FROM tasks WHERE user_key=? AND status='running'",(user_key,)).fetchone()
            if active: raise ValueError('当前用户已有任务运行，请先等待或取消')
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (key,workspace,user_key,action,encode(payload),'running','starting',now(),None,None,None,
                        encode(checkpoint) if checkpoint else None,None))
        threading.Thread(target=self.run,args=(workspace,key,execute),daemon=True).start()
        return self.get(workspace,key)

    def cancel(self, workspace, key):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE id=? AND workspace=?',(key,workspace)).fetchone()
            if not row: raise PermissionError('任务不属于当前工作空间')
            if row['status']=='running':
                db.execute("UPDATE tasks SET status='cancelled',error='已取消；已发出的 API 请求可能仍在结束',finished_at=? WHERE id=?",(now(),key))
                db.execute("UPDATE history SET status='cancelled',result=NULL WHERE id=?",(row['history_id'],))
        return self.get(workspace,key)

    def run(self, workspace, key, execute):
        def update(stage):
            with self.store.connect() as db:
                row = db.execute('SELECT status FROM tasks WHERE id=?',(key,)).fetchone()
                if row['status'] != 'running': raise TaskCancelled('任务已取消')
                if stage: db.execute('UPDATE tasks SET stage=? WHERE id=?',(stage,key))
        def checkpoint(value):
            update(None)
            with self.store.connect() as db:
                db.execute("UPDATE tasks SET checkpoint=? WHERE id=? AND status='running'",(encode(value),key))
        context = observer.set(update); owner = task_id.set(key)
        try:
            item = self.get(workspace,key,private=True)
            result = execute(item['action'],deepcopy(item['payload']),item['checkpoint'],checkpoint)
            update(None)
            failed = result.get('status')=='error' or result.get('evaluation',{}).get('verdict')=='error'
            with self.store.connect() as db:
                db.execute("UPDATE tasks SET status=?,result=?,finished_at=? WHERE id=? AND status='running'",
                           ('error' if failed else 'done',encode(result),now(),key))
        except TaskCancelled:
            pass
        except Exception as exc:
            with self.store.connect() as db:
                db.execute("UPDATE tasks SET status='error',error=?,finished_at=? WHERE id=? AND status='running'",(str(exc),now(),key))
        finally:
            observer.reset(context); task_id.reset(owner)
