import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path=Path(path)
        self.memory_v2=None
        self.memory_backup_path=None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS bindings(app TEXT, sender TEXT, household TEXT,
          timezone TEXT, PRIMARY KEY(app,sender));
        CREATE TABLE IF NOT EXISTS pairing(app TEXT PRIMARY KEY, digest TEXT,
          household TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, app TEXT, sender TEXT,
          household TEXT, kind TEXT, key TEXT, value TEXT, starts_at TEXT, expires_at TEXT,
          source_message_id TEXT, created_at REAL, updated_at REAL);
        CREATE TABLE IF NOT EXISTS pending(app TEXT, sender TEXT, household TEXT,
          token TEXT, data TEXT, expires REAL, PRIMARY KEY(app,sender,household));
        CREATE TABLE IF NOT EXISTS messages(app TEXT, message TEXT, state TEXT,
          outbound_uuid TEXT, created REAL, PRIMARY KEY(app,message));
        CREATE TABLE IF NOT EXISTS work(app TEXT,message TEXT,sender TEXT,text TEXT,
          reply TEXT,PRIMARY KEY(app,message));
        CREATE TABLE IF NOT EXISTS receipts(app TEXT,message TEXT,result TEXT,PRIMARY KEY(app,message));
        CREATE TABLE IF NOT EXISTS decision_selection(app TEXT,sender TEXT,household TEXT,
          episode_id TEXT,preview_id TEXT,memory_version INTEGER,candidate_digest TEXT,updated_at REAL,
          PRIMARY KEY(app,sender,household));
        CREATE TABLE IF NOT EXISTS interaction_feed(sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,
          app TEXT,sender TEXT,household TEXT,message TEXT,request_id TEXT,source_channel TEXT,
          user_text TEXT,agent_text TEXT,created_at REAL,episode_id TEXT,preview_id TEXT,UNIQUE(app,message));
        ''')
        self.db.commit()

    def enable_memory_v2(self):
        if self.memory_v2 is None:
            from .memory_v2 import MemoryV2
            self.memory_v2=MemoryV2(self.db)
            if self.db.execute('PRAGMA user_version').fetchone()[0]<2:
                self.memory_backup_path=self.path.with_name(self.path.stem+'.before-ch02-'+str(time.time_ns())+'.sqlite3')
            self.memory_v2.migrate(self.memory_backup_path)
        return self.memory_v2

    def binding(self, app, sender):
        row = self.db.execute('SELECT * FROM bindings WHERE app=? AND sender=?', (app, sender)).fetchone()
        return dict(row) if row else None

    def pair_code(self, app, household='site_1', now=None):
        now = time.time() if now is None else now
        code = secrets.token_hex(4).upper()
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO pairing VALUES(?,?,?,?)',
                            (app, hashlib.sha256(code.encode()).hexdigest(), household, now + 600))
        return code

    def receipt(self, app, message, sender=None):
        row = self.db.execute('SELECT result FROM receipts WHERE app=? AND message=?', (app,message)).fetchone()
        data = json.loads(row['result']) if row else None
        if data and sender is not None and data.get('sender') != sender:
            return None
        return data

    def bind(self, app, sender, code, now=None, message=None):
        now = time.time() if now is None else now
        with self.db:
            cached = self.receipt(app,message,sender) if message else None
            if cached and cached.get('action') == 'binding':
                return cached['success']
            row = self.db.execute('SELECT * FROM pairing WHERE app=?', (app,)).fetchone()
            if not row or row['expires'] < now or not secrets.compare_digest(
                    row['digest'], hashlib.sha256(code.strip().upper().encode()).hexdigest()):
                return False
            if self.binding(app, sender):
                return False
            # One owner per household in this local trial; only a new owner-issued code can add another household.
            if self.db.execute('SELECT 1 FROM bindings WHERE app=? AND household=?',
                               (app, row['household'])).fetchone():
                return False
            self.db.execute('INSERT INTO bindings VALUES(?,?,?,NULL)', (app, sender, row['household']))
            self.db.execute('DELETE FROM pairing WHERE app=?', (app,))
            if message:
                self.db.execute('INSERT INTO receipts VALUES(?,?,?)',
                                (app,message,json.dumps({'action':'binding','success':True,'sender':sender})))
            return True

    def timezone(self, identity, timezone):
        with self.db:
            self.db.execute('UPDATE bindings SET timezone=? WHERE app=? AND sender=?',
                            (timezone, identity['app'], identity['sender']))

    @staticmethod
    def scope(identity):
        return identity['app'], identity['sender'], identity['household']

    def memories(self, identity, active_at=None):
        rows = [dict(row) for row in self.db.execute(
            'SELECT * FROM memories WHERE app=? AND sender=? AND household=? ORDER BY created_at', self.scope(identity))]
        if active_at:
            rows = [r for r in rows if not r['expires_at'] or r['expires_at'] > active_at]
        return rows

    def put_pending(self, identity, data, now=None, token=None, message=None, reply=None):
        token = token or secrets.token_hex(3).upper()
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO pending VALUES(?,?,?,?,?,?)',
                            (*self.scope(identity), token, json.dumps(data, ensure_ascii=False),
                             (time.time() if now is None else now) + 600))
            if message and reply:
                self.db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?)',
                                (identity['app'],message,json.dumps({'action':'reply','sender':identity['sender'],
                                                                  'household':identity['household'],'reply':reply},ensure_ascii=False)))
        return token

    def pending(self, identity, token, now=None):
        row = self.db.execute('SELECT * FROM pending WHERE app=? AND sender=? AND household=?', self.scope(identity)).fetchone()
        if not row or row['expires'] < (time.time() if now is None else now) or not secrets.compare_digest(row['token'], token):
            return None
        return json.loads(row['data'])

    def pending_record(self, identity):
        row=self.db.execute('SELECT * FROM pending WHERE app=? AND sender=? AND household=?',self.scope(identity)).fetchone()
        if not row or row['expires']<time.time():
            return None
        return {'token':row['token'],'expiresAt':row['expires'],'data':json.loads(row['data'])}

    def clear_pending(self, identity):
        with self.db:
            self.db.execute('DELETE FROM pending WHERE app=? AND sender=? AND household=?', self.scope(identity))

    def select_decision(self, identity, episode_id=None, preview_id=None, candidate_digest=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO decision_selection VALUES(?,?,?,?,?,?,?,?)',
                            (*self.scope(identity),episode_id,preview_id,self.memory_v2.version(identity),candidate_digest,time.time()))

    def interaction(self, identity, message, text, reply, request_id=None, metadata=None):
        import uuid
        import re
        metadata=metadata or {}
        normalized=text.strip().strip('`').strip().split(None,1)
        labels={'绑定':'绑定家庭','记下':'确认保存','确认':'确认样例','执行':'确认执行这一时段'}
        if normalized and normalized[0] in labels and len(normalized)>1 and re.fullmatch(r'[A-Fa-f0-9]{6,8}',normalized[1].strip('` \r\n\t')):
            text=labels[normalized[0]]
        def redact(value):
            value=re.sub(r'(记下|执行|确认|绑定|选\d+)[\s`]+[A-Fa-f0-9]{6,8}',lambda m:labels.get(m[1],'确认样例选项'),value)
            return re.sub(r'\b(?:om_|ou_|oc_)[A-Za-z0-9_-]+','[内部引用]',value)
        text,reply=redact(text),redact(reply)
        with self.db:
            self.db.execute('DELETE FROM interaction_feed WHERE created_at<?',(time.time()-600,))
            self.db.execute('INSERT OR IGNORE INTO interaction_feed(id,app,sender,household,message,request_id,source_channel,user_text,agent_text,created_at,episode_id,preview_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                            (uuid.uuid4().hex,*self.scope(identity),message,request_id,'web' if message.startswith('web_') else 'feishu',
                             text[:2000],reply[:6000],time.time(),metadata.get('episodeId'),metadata.get('previewId')))

    def prune_cache(self):
        try:
            connection=sqlite3.connect(self.path,timeout=1)
            with connection:
                connection.execute('DELETE FROM interaction_feed WHERE created_at<?',(time.time()-600,))
                connection.execute('UPDATE web_requests SET result=NULL WHERE created<?',(time.time()-600,))
            connection.close()
        except sqlite3.OperationalError:
            if 'connection' in locals():connection.close()

    def purge_personal_cache(self, identity):
        self.db.execute('DELETE FROM interaction_feed WHERE app=? AND sender=? AND household=?',self.scope(identity))
        owner=hashlib.sha256('\x1f'.join(self.scope(identity)).encode()).hexdigest()
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='web_requests'").fetchone():
            self.db.execute('UPDATE web_requests SET result=NULL WHERE owner=?',(owner,))

    def apply_pending(self, identity, token, source, now=None):
        now = time.time() if now is None else now
        with self.db:
            receipt = self.receipt(identity['app'],source,identity['sender'])
            if receipt and receipt.get('household') == identity['household']:
                return receipt
            row = self.db.execute('SELECT * FROM pending WHERE app=? AND sender=? AND household=?', self.scope(identity)).fetchone()
            if not row or row['expires'] < now or not secrets.compare_digest(row['token'], token):
                return None
            data = json.loads(row['data'])
            if data['action'] == 'forget':
                memory_ids=[record['id'] for record in self.memories(identity)]
                self.db.execute('DELETE FROM memories WHERE app=? AND sender=? AND household=?', self.scope(identity))
                if self.memory_v2:
                    self.memory_v2.record_deletion(identity,memory_ids)
                self.purge_personal_cache(identity)
            else:
                record_id = data.get('id') or secrets.token_hex(4)
                existing = self.db.execute('SELECT created_at FROM memories WHERE id=? AND app=? AND sender=? AND household=?',
                                           (record_id, *self.scope(identity))).fetchone()
                if data.get('id') and not existing:
                    return None
                if data.get('revisionCandidateId'):
                    if not self.memory_v2:
                        raise ValueError('memory_revision_service_unavailable')
                    self.memory_v2.confirmed_revision_candidate(identity,data['revisionCandidateId'])
                self.db.execute('INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                (record_id, *self.scope(identity), data['kind'], data['key'], data['value'],
                                 data.get('starts_at'), data.get('expires_at'), data.get('source_message_id') or source,
                                 existing['created_at'] if existing else now, now))
                data['id'] = record_id
                if self.memory_v2:
                    self.memory_v2.record_confirmation(identity,record_id,data.get('constraint'))
            self.db.execute('DELETE FROM pending WHERE app=? AND sender=? AND household=?', self.scope(identity))
            receipt_data = {'action':data['action'], 'id':data.get('id'),'sender':identity['sender'],'household':identity['household']}
            if data.get('previewId'):
                receipt_data['previewId']=data['previewId']
            self.db.execute('INSERT INTO receipts VALUES(?,?,?)', (identity['app'], source, json.dumps(receipt_data)))
            return data

    def finish_plan(self, identity, message, reply):
        with self.db:
            self.db.execute('DELETE FROM pending WHERE app=? AND sender=? AND household=?', self.scope(identity))
            self.db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?)',
                            (identity['app'],message,json.dumps({'action':'reply','sender':identity['sender'],
                                                              'household':identity['household'],'reply':reply},ensure_ascii=False)))

    def confirm_feedback(self, identity, token, message):
        if not self.memory_v2:
            raise ValueError('memory_revision_service_unavailable')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            cached=self.receipt(identity['app'],message,identity['sender'])
            if cached:
                return cached['feedbackId']
            data=self.pending(identity,token)
            if not data or data.get('action')!='feedback':
                return None
            feedback_id=self.memory_v2.add_feedback(identity,data['episodeId'],data['summary'],data['source_message_id'])
            self.db.execute('DELETE FROM pending WHERE app=? AND sender=? AND household=?',self.scope(identity))
            self.db.execute('INSERT INTO receipts VALUES(?,?,?)',(identity['app'],message,json.dumps(
                {'action':'feedback','feedbackId':feedback_id,'sender':identity['sender'],'household':identity['household']})))
            return feedback_id

    def delete(self, identity, record_id, message=None):
        with self.db:
            result = self.db.execute('DELETE FROM memories WHERE id=? AND app=? AND sender=? AND household=?',
                                     (record_id, *self.scope(identity)))
            if result.rowcount and message:
                self.db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?)',
                                (identity['app'],message,json.dumps({'action':'reply','sender':identity['sender'],
                                                                  'household':identity['household'],'reply':'已删除该条资料。'},ensure_ascii=False)))
            if result.rowcount and self.memory_v2:
                self.memory_v2.record_deletion(identity,[record_id])
                self.purge_personal_cache(identity)
            return result.rowcount > 0

    def claim(self, app, message, outbound_uuid, sender='', text=''):
        with self.db:
            result = self.db.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?, ?,?)',
                                     (app, message, 'received', outbound_uuid, time.time()))
            if result.rowcount:
                self.db.execute('INSERT INTO work VALUES(?,?,?,?,NULL)', (app,message,sender,text))
            return result.rowcount > 0

    def state(self, app, message, state):
        with self.db:
            self.db.execute('UPDATE messages SET state=? WHERE app=? AND message=?', (state, app, message))
            if state in {'sending', 'sent', 'unknown'}:
                self.db.execute('DELETE FROM work WHERE app=? AND message=?', (app,message))

    def save_reply(self, app, message, reply):
        with self.db:
            self.db.execute('UPDATE work SET reply=?,text=NULL WHERE app=? AND message=?', (reply,app,message))
            self.db.execute('UPDATE messages SET state=? WHERE app=? AND message=?', ('computed',app,message))

    def recover(self, app):
        return [dict(r) for r in self.db.execute('SELECT messages.*,work.sender,work.text,work.reply FROM messages JOIN work USING(app,message) WHERE app=? AND state IN (?,?,?)',
                                                (app,'received','processing','computed'))]

    def counts(self):
        return dict(self.db.execute('SELECT state,count(*) FROM messages GROUP BY state').fetchall())
