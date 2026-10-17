"""Owner-only, opt-in facts outbox. No solver, device IO or sends on construction."""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA = 'assistant-notifications-v1'
TERMINAL = {'sent', 'unknown', 'failed'}


@contextmanager
def transaction(db):
    if db.in_transaction:
        yield
    else:
        with db:
            db.execute('BEGIN IMMEDIATE')
            yield


def scope(who):
    return tuple(who[k] for k in ('app', 'sender', 'household'))


def tables(db):
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('notification_settings','notification_outbox')")}
    required={'notification_settings':{'enabled','enabled_at','scan_at','daily_cutoff','daily_date','pending_fingerprint','app','sender','household'},
              'notification_outbox':{'id','source_key','kind','state','title','text','created_at','updated_at','due_at','episode_id','source_refs','origin_message','outbound_uuid','provider_id','delivery_via','consumed_by','feed_published','app','sender','household'}}
    for name in names:
        if not required[name]<={row[1] for row in db.execute('PRAGMA table_info('+name+')')}:
            raise ValueError('notification_schema_mismatch')
    return names==set(required)


def owner(db, who):
    rows = db.execute('SELECT * FROM bindings WHERE app=? AND household=?', (who['app'], 'site_1')).fetchall()
    if who['household'] != 'site_1' or len(rows) != 1 or rows[0]['sender'] != who['sender']:
        raise ValueError('notification_owner_mismatch')
    return dict(rows[0])


def zone(binding):
    value = binding.get('timezone')
    try:
        return ZoneInfo(value) if isinstance(value, str) and value else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def read_notifications(db, who):
    binding = owner(db, who)
    settings = {'enabled': False, 'needsTimezone': zone(binding) is None, 'timezone': binding.get('timezone'),
                'dailyAt': '20:00', 'quietHours': {'start': '22:00', 'end': '08:00'},
                'triggers': ['important_events', 'daily_summary'], 'enabledAt': None}
    items = []
    if tables(db):
        row = db.execute('SELECT * FROM notification_settings WHERE app=? AND sender=? AND household=?', scope(who)).fetchone()
        if row:
            settings.update(enabled=bool(row['enabled']), enabledAt=row['enabled_at'])
        for row in db.execute('SELECT * FROM notification_outbox WHERE app=? AND sender=? AND household=? ORDER BY updated_at DESC,id ASC LIMIT 20', scope(who)):
            items.append({'id': row['id'], 'kind': row['kind'], 'state': row['state'], 'title': row['title'], 'text': row['text'],
                          'createdAt': row['created_at'], 'updatedAt': row['updated_at'], 'dueAt': row['due_at'],
                          'episodeId': row['episode_id'], 'environment': 'local_debug1_sim', 'deliveryVia': row['delivery_via']})
    return {'schemaVersion': SCHEMA, 'settings': settings, 'items': items}


class NeedsTimezone(ValueError):
    pass


class Notifications:
    def __init__(self, store, clock=None):
        self.store, self.db = store, store.db
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.backup_path = None
        self.recovered = set()

    def ensure_schema(self):
        present = tables(self.db)
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(notification_settings)')}
        if present and 'event_baseline' in columns:
            return
        if 'enabled' in columns and self.db.execute('SELECT 1 FROM notification_settings WHERE enabled=1').fetchone():
            raise ValueError('notification_migration_requires_disabled')
        if self.db.in_transaction:
            raise ValueError('notification_migration_requires_idle')
        self.backup_path = self.store.path.with_name(self.store.path.stem + '.before-notifications-' + str(time.time_ns()) + '.sqlite3')
        target = sqlite3.connect(self.backup_path)
        try:
            self.db.backup(target)
        finally:
            target.close()
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS notification_settings(app TEXT,sender TEXT,household TEXT,enabled INTEGER,
          enabled_at REAL,scan_at REAL,daily_cutoff REAL,daily_date TEXT,pending_fingerprint TEXT,event_baseline TEXT NOT NULL DEFAULT '[]',
          PRIMARY KEY(app,sender,household));
        CREATE TABLE IF NOT EXISTS notification_outbox(id TEXT PRIMARY KEY,app TEXT,sender TEXT,household TEXT,
          source_key TEXT,kind TEXT,state TEXT,title TEXT,text TEXT,created_at REAL,updated_at REAL,due_at REAL,
          episode_id TEXT,source_refs TEXT,origin_message TEXT,outbound_uuid TEXT,provider_id TEXT,
          delivery_via TEXT,consumed_by TEXT,feed_published INTEGER NOT NULL DEFAULT 0,
          UNIQUE(app,sender,household,source_key));
        ''')
        if 'event_baseline' not in {r[1] for r in self.db.execute('PRAGMA table_info(notification_settings)')}:
            with transaction(self.db):
                self.db.execute("ALTER TABLE notification_settings ADD COLUMN event_baseline TEXT NOT NULL DEFAULT '[]'")

    def setting(self, who):
        return self.db.execute('SELECT * FROM notification_settings WHERE app=? AND sender=? AND household=?', scope(who)).fetchone() if tables(self.db) else None

    def pending(self, who, now):
        row = self.db.execute('SELECT * FROM pending WHERE app=? AND sender=? AND household=? AND expires>?', (*scope(who), now)).fetchone()
        if not row:
            return None
        data = json.loads(row['data'])
        if data.get('action') not in {'memory', 'runtime_plan'} or data.get('expiresAt', row['expires']) <= now:
            return None
        if data['action'] == 'runtime_plan':
            ep = self.db.execute('SELECT state FROM episodes WHERE id=? AND app=? AND sender=? AND household=?', (data.get('episodeId'), *scope(who))).fetchone()
            if not ep or ep['state'] != 'proposed':
                return None
        if data['action']=='memory' and data.get('id') and not self.db.execute('SELECT 1 FROM memories WHERE id=? AND app=? AND sender=? AND household=?',(data['id'],*scope(who))).fetchone():
            return None
        fp = hashlib.sha256((row['token'] + ':' + data['action'] + ':' + str(data.get('proposalId',''))).encode()).hexdigest()
        return {'fingerprint': fp, 'episodeId': data.get('episodeId'), 'expiresAt': min(row['expires'], data.get('expiresAt', row['expires']))}

    def set_enabled(self, who, enabled):
        if type(enabled) is not bool:
            raise ValueError('invalid_notification_settings')
        binding = owner(self.db, who)
        if enabled and zone(binding) is None:
            raise NeedsTimezone('needs_timezone')
        if not enabled:
            if tables(self.db):
                with self.lock, self.db:
                    self.db.execute('UPDATE notification_settings SET enabled=0 WHERE app=? AND sender=? AND household=?', scope(who))
                    self.db.execute("UPDATE notification_outbox SET state='cancelled',updated_at=? WHERE app=? AND sender=? AND household=? AND state IN ('queued','deferred')", (self.clock(), *scope(who)))
            return read_notifications(self.db, who)
        self.ensure_schema()
        now = self.clock()
        with self.lock, transaction(self.db):
            old = self.setting(who)
            if enabled and (not old or not old['enabled']):
                current = self.pending(who, now)
                local = datetime.fromtimestamp(now, zone(binding))
                self.db.execute('INSERT OR REPLACE INTO notification_settings VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (*scope(who), 1, now, now, now, local.date().isoformat() if local.hour >= 20 else None,
                     current['fingerprint'] if current else None, json.dumps([self.event_key(row) for row in self.events(who, now)])))
        return read_notifications(self.db, who)

    def events(self, who, after):
        return self.db.execute("SELECT e.id,e.state,v.sequence,v.phase,v.created_at,v.evidence_json FROM episodes e JOIN episode_events v ON v.episode_id=e.id WHERE e.app=? AND e.sender=? AND e.household=? AND v.created_at>=? AND v.phase IN ('unknown','outcome') ORDER BY v.created_at,e.id,v.sequence", (*scope(who), after)).fetchall()

    @staticmethod
    def event_key(row):
        return 'event:' + row['id'] + ':' + str(row['sequence'])

    def capture_before(self, who):
        setting = self.setting(who)
        if not setting or not setting['enabled']:
            return None
        return {self.event_key(row) for row in self.events(who, setting['enabled_at'])}

    def enqueue(self, who, key, kind, title, text, refs, episode=None, origin=None, due=None, state='queued'):
        now = self.clock()
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, '\x1f'.join(scope(who)) + ':' + key))
        self.db.execute('INSERT OR IGNORE INTO notification_outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
            (identifier, *scope(who), key, kind, state, title[:80], text[:1200], now, now, due, episode,
             json.dumps(refs), origin, str(uuid.uuid5(uuid.NAMESPACE_URL, 'notify:' + identifier)), None, None, None))
        return identifier

    def enqueue_event(self, who, row, origin=None):
        key = self.event_key(row)
        setting = self.setting(who)
        if 'event_baseline' not in setting.keys() or key in json.loads(setting['event_baseline']):
            return
        if row['phase'] == 'unknown':
            kind, title = 'execution_unknown', '本机演练结果待核对'
            text = '这次本机演练没有完整执行回读；未知请求不会自动重发。请查看原事件，或回复“仿真进度”。'
        else:
            proof = json.loads(row['evidence_json'])
            if proof.get('settlementAvailable') is not True or proof.get('success') is not True:
                return
            was_unknown = self.db.execute("SELECT 1 FROM episode_events WHERE episode_id=? AND phase='unknown' AND sequence<?", (row['id'], row['sequence'])).fetchone()
            kind = 'execution_resolved' if was_unknown else 'execution_outcome'
            title = '本机演练已核对完成' if was_unknown else '本机演练结果已核验'
            settled = self.db.execute("SELECT evidence_json FROM episode_events WHERE episode_id=? AND phase='settled' AND sequence<? ORDER BY sequence DESC LIMIT 1", (row['id'], row['sequence'])).fetchone()
            data = json.loads(settled[0]) if settled else {}
            cost, slot = data.get('costEur'), data.get('sourceSlot')
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or isinstance(slot, bool) or not isinstance(slot, int):
                return
            text = f'本机演练事件 {row["id"][:8]}：模拟槽 {slot-1}→{slot} 的15分钟已回读核验，净电费 €{cost:.4f}。这不是今日账单或节省金额；后续时段未执行。'
        self.enqueue(who, key, kind, title, text, [key], row['id'], origin)

    def record_changes(self, who, before, message):
        if before is None:
            return
        setting = self.setting(who)
        if not setting or not setting['enabled']:
            return
        with transaction(self.db):
            for row in self.events(who, setting['enabled_at']):
                if self.event_key(row) not in before:
                    self.enqueue_event(who, row, message)
            self.collect_pending(who, setting, message)

    def collect_pending(self, who, setting, origin=None):
        current = self.pending(who, self.clock())
        fp = current['fingerprint'] if current else None
        if current and fp != setting['pending_fingerprint']:
            self.enqueue(who, 'pending:' + fp, 'pending_attention', '有一项内容等你确认',
                         '请打开网页查看当前待确认内容。保存资料与执行方案分别确认；这条提醒不会保存资料或控制设备。',
                         ['pending:' + fp], current['episodeId'], origin)
        self.db.execute('UPDATE notification_settings SET pending_fingerprint=? WHERE app=? AND sender=? AND household=?', (fp, *scope(who)))

    def collect(self, who, setting, now):
        with self.db:
            for row in self.events(who, setting['scan_at']):
                self.enqueue_event(who, row)
            self.collect_pending(who, setting)
            self.db.execute('UPDATE notification_settings SET scan_at=? WHERE app=? AND sender=? AND household=?', (now, *scope(who)))

    def valid(self, who, row, now):
        if row['kind'] in {'quiet_digest','daily_summary'}:
            return bool(self.aggregate_rows(who,row,now))
        refs = json.loads(row['source_refs'])
        for ref in refs:
            if ref.startswith('pending:'):
                current = self.pending(who, now)
                if not current or ref != 'pending:' + current['fingerprint']:
                    return False
        if row['episode_id'] and row['kind'] == 'execution_unknown':
            ep = self.db.execute('SELECT state FROM episodes WHERE id=? AND app=? AND sender=? AND household=?', (row['episode_id'], *scope(who))).fetchone()
            if not ep or ep['state'] != 'unknown':
                return False
        return True

    def aggregate_rows(self,who,row,now):
        latest={}
        for ref in json.loads(row['source_refs']):
            child=self.db.execute('SELECT * FROM notification_outbox WHERE app=? AND sender=? AND household=? AND source_key=?',(*scope(who),ref)).fetchone()
            if child and child['kind'] not in {'quiet_digest','daily_summary'} and self.valid(who,child,now):
                latest[child['episode_id'] or child['source_key']]=child
        return list(latest.values())

    def publish(self, row):
        if row['state'] not in TERMINAL or row['feed_published']:
            return
        text = row['text'] + ('\n飞书消息发送结果未知，未自动重发。' if row['state'] == 'unknown' else '\n飞书消息发送失败。' if row['state'] == 'failed' else '')
        self.db.execute('INSERT OR IGNORE INTO interaction_feed(id,app,sender,household,message,request_id,source_channel,user_text,agent_text,created_at,episode_id,preview_id) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,NULL)',
                        (row['id'], row['app'], row['sender'], row['household'], 'notification:' + row['id'], 'agent', '', text[:6000], self.clock(), row['episode_id']))
        self.db.execute('UPDATE notification_outbox SET feed_published=1 WHERE id=?', (row['id'],))

    def merge_quiet(self, who, now):
        rows = self.db.execute("SELECT * FROM notification_outbox WHERE app=? AND sender=? AND household=? AND state='deferred' ORDER BY created_at,id", scope(who)).fetchall()
        if not rows:
            return
        latest = {}
        with self.db:
            for row in rows:
                if self.valid(who, row, now):
                    latest[row['episode_id'] or row['source_key']] = row
                else:
                    self.db.execute("UPDATE notification_outbox SET state='cancelled',updated_at=? WHERE id=?", (now, row['id']))
            selected = list(latest.values())
            if not selected:
                return
            refs = [ref for row in selected for ref in json.loads(row['source_refs'])]
            key = 'quiet:' + hashlib.sha256('|'.join(sorted(refs)).encode()).hexdigest()
            merged = self.enqueue(who, key, 'quiet_digest', '静默期间的新进展', '\n'.join(row['text'] for row in selected), refs)
            for row in rows:
                self.db.execute("UPDATE notification_outbox SET state='cancelled',consumed_by=?,updated_at=? WHERE id=? AND state='deferred'", (merged, now, row['id']))

    def daily(self, who, setting, local, now):
        day = local.date().isoformat()
        if local.hour != 20 or setting['daily_date'] == day:
            return
        rows = self.db.execute("SELECT * FROM notification_outbox WHERE app=? AND sender=? AND household=? AND created_at>=? AND kind IN ('pending_attention','execution_outcome','execution_unknown','execution_resolved') ORDER BY created_at,id", (*scope(who), setting['daily_cutoff'])).fetchall()
        consumed={ref for r in self.db.execute("SELECT source_refs FROM notification_outbox WHERE app=? AND sender=? AND household=? AND kind='daily_summary' AND created_at>=?",(*scope(who),setting['daily_cutoff'])) for ref in json.loads(r['source_refs'])}
        latest = {}
        for row in rows:
            if row['source_key'] not in consumed and self.valid(who, row, now):
                latest[row['episode_id'] or row['source_key']] = row
        with self.db:
            if latest:
                refs = [ref for row in latest.values() for ref in json.loads(row['source_refs'])]
                start = datetime.fromtimestamp(setting['daily_cutoff'], local.tzinfo).strftime('%m-%d %H:%M')
                text = f'自上次摘要以来（家庭现实时间 {start} 至 {local:%m-%d %H:%M}）：\n' + '\n'.join(row['text'] for row in latest.values())
                self.enqueue(who, 'daily:' + day, 'daily_summary', '家庭用电助手每日摘要', text, refs)
                self.db.execute('UPDATE notification_settings SET daily_cutoff=? WHERE app=? AND sender=? AND household=?', (now, *scope(who)))
            self.db.execute('UPDATE notification_settings SET daily_date=? WHERE app=? AND sender=? AND household=?', (day, *scope(who)))

    def tick(self, send_owner, app_id=None):
        # shortcut: one existing channel process/executor owns the pump; scale only if multi-worker is introduced.
        with self.lock:
            if not tables(self.db) or 'event_baseline' not in {r[1] for r in self.db.execute('PRAGMA table_info(notification_settings)')}:
                return
            now = self.clock()
            app_filter=' AND app=?' if app_id is not None else ''
            app_args=(app_id,) if app_id is not None else ()
            if app_id not in self.recovered:
                with self.db:
                    self.db.execute("UPDATE notification_outbox SET state='unknown',updated_at=? WHERE state='sending'"+app_filter, (now,*app_args))
                self.recovered.add(app_id)
            with self.db:
                for row in self.db.execute("SELECT * FROM notification_outbox WHERE state IN ('sent','unknown','failed') AND feed_published=0"+app_filter,app_args).fetchall():
                    self.publish(row)
            for setting in self.db.execute('SELECT * FROM notification_settings WHERE enabled=1'+app_filter,app_args).fetchall():
                who = {k: setting[k] for k in ('app', 'sender', 'household')}
                try:
                    binding = owner(self.db, who)
                except ValueError:
                    continue
                tz = zone(binding)
                if tz is None:
                    continue
                local = datetime.fromtimestamp(now, tz)
                self.collect(who, setting, now)
                if local.hour >= 22 or local.hour < 8:
                    end = local.replace(hour=8, minute=0, second=0, microsecond=0) + (timedelta(days=1) if local.hour >= 22 else timedelta())
                    with self.db:
                        self.db.execute("UPDATE notification_outbox SET state='deferred',due_at=?,updated_at=? WHERE app=? AND sender=? AND household=? AND state='queued'", (end.timestamp(), now, *scope(who)))
                    continue
                self.merge_quiet(who, now)
                self.daily(who, self.setting(who), local, now)
                rows = self.db.execute("SELECT * FROM notification_outbox WHERE app=? AND sender=? AND household=? AND state='queued' ORDER BY created_at,id", scope(who)).fetchall()
                for row in rows:
                    if not self.valid(who, row, now):
                        with self.db:
                            self.db.execute("UPDATE notification_outbox SET state='cancelled',updated_at=? WHERE id=?", (now, row['id']))
                        continue
                    if row['kind'] in {'quiet_digest','daily_summary'}:
                        prefix=row['text'].split('\n',1)[0]+'\n' if row['kind']=='daily_summary' else ''
                        text=prefix+'\n'.join(child['text'] for child in self.aggregate_rows(who,row,now))
                        with self.db:self.db.execute('UPDATE notification_outbox SET text=? WHERE id=?',(text[:1200],row['id']))
                        row=self.db.execute('SELECT * FROM notification_outbox WHERE id=?',(row['id'],)).fetchone()
                    binding = owner(self.db, who)
                    if zone(binding) is None or not self.setting(who)['enabled'] or not binding['sender'].startswith('ou_'):
                        return
                    with self.db:
                        claimed = self.db.execute("UPDATE notification_outbox SET state='sending',outbound_uuid=?,delivery_via='proactive',updated_at=? WHERE id=? AND state='queued'", (row['outbound_uuid'], now, row['id'])).rowcount
                    if not claimed:
                        continue
                    try:
                        result = send_owner(who, row['text'], row['outbound_uuid'])
                        state = result.get('state') if isinstance(result, dict) else 'unknown'
                        if state not in TERMINAL:
                            state = 'unknown'
                        provider = result.get('messageId') if isinstance(result, dict) and state == 'sent' else None
                    except Exception:
                        state, provider = 'unknown', None
                    with self.db:
                        self.db.execute('UPDATE notification_outbox SET state=?,provider_id=?,updated_at=? WHERE id=?', (state, provider, self.clock(), row['id']))
                        self.publish(self.db.execute('SELECT * FROM notification_outbox WHERE id=?', (row['id'],)).fetchone())
                    return
