"""AN-01 temporary-owner checks. Fake SDK/API only; never contacts a service."""
from datetime import datetime, timezone
import importlib.util
import json
import sqlite3
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from assistant.notifications import Notifications, NeedsTimezone, read_notifications, tables
from assistant.runtime_api import RuntimeOutcomeUnknown
from assistant.runtime_tools import RuntimeTools
from assistant.service import Service
from assistant.store import Store
from assistant.web_gateway import WebGateway, COOKIE
from channels.feishu import Worker,notification_delivery
from test_web_gateway import FakeRuntime, FakeModel, FakeEnergy


class API(FakeRuntime):
    unknown=False
    def confirm(self,*args):
        if self.unknown:raise RuntimeOutcomeUnknown('fixture_unknown')
        return super().confirm(*args)
    def reconcile(self,who,episode):
        if self.unknown:return super().reconcile(who,episode)
        proposal=next(row for row in self.proposals.values() if row['episodeId']==episode)
        return super().confirm(who,episode,proposal['proposalId'],'fixture_reconcile')


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,10,17,10,tzinfo=timezone.utc).timestamp()
        self.clock=patch('time.time',lambda:self.now);self.clock.start()
        self.store=Store(Path(self.temp.name)/'owner.sqlite3')
        self.who={'app':'cli_notification_fixture','sender':'ou_owner_fixture','household':'site_1'}
        self.store.bind(self.who['app'],self.who['sender'],self.store.pair_code(self.who['app']))
        self.api=API()
        self.service=Service(self.store,FakeEnergy(),FakeModel(),runtime=RuntimeTools(self.store,self.api))
        self.notice=self.service.notifications
        self.gateway=WebGateway(self.who['app'],self.store,self.service)
        _,_,token=self.gateway.dispatch('GET','/api/assistant/session',None)
        self.cookie=COOKIE+'='+token
        self.sends=[]
        self.send=lambda who,text,key:self.sends.append((dict(who),text,key)) or {'state':'sent','messageId':'om_fixture_delivery'}

    def tearDown(self):
        self.store.db.close();self.clock.stop();self.temp.cleanup()

    def enable(self):
        self.store.timezone(self.who,'Europe/Berlin')
        return self.notice.set_enabled(self.who,True)

    def chat(self,text,message=None):
        return self.service.handle(self.who['app'],self.who['sender'],message or 'web_fixture_'+str(self.now),text)

    def pending(self,value='fixture'):
        self.now+=1
        return self.store.put_pending(self.who,{'action':'memory','kind':'habit','key':'fixture','value':value})

    def outcome(self,origin='web_execution'):
        self.now+=1
        self.chat('帮我安排这一时段',origin+'_plan')
        record=self.store.pending_record(self.who)
        episode=record['data']['episodeId']
        self.now+=1
        self.chat('执行 '+record['token'],origin)
        return episode

    def test_constructor_get_missing_tables_is_readonly_and_needs_timezone(self):
        before=self.store.db.total_changes
        seen=tuple(self.store.db.execute('SELECT seen_token,seen_proposal FROM web_sessions').fetchone())
        status,result,_=self.gateway.dispatch('GET','/api/assistant/notifications',self.cookie)
        self.assertEqual((status,result['settings']['enabled'],result['settings']['needsTimezone'],result['items']),(200,False,True,[]))
        self.assertEqual(self.store.db.total_changes,before)
        self.assertEqual(tuple(self.store.db.execute('SELECT seen_token,seen_proposal FROM web_sessions').fetchone()),seen)
        self.assertFalse(tables(self.store.db));self.assertIsNone(self.notice.backup_path)
        status,result,_=self.gateway.dispatch('POST','/api/assistant/notification-settings',self.cookie,{'enabled':True})
        self.assertEqual((status,result['error']['code'],result['settings']['enabled']),(409,'needs_timezone',False))
        self.assertFalse(tables(self.store.db))
        self.notice.tick(self.send);self.assertFalse(self.sends)

    def test_routes_owner_query_body_and_no_seen_are_strict(self):
        self.assertEqual(self.gateway.dispatch('GET','/api/assistant/notifications',None)[0],401)
        self.assertEqual(self.gateway.dispatch('GET','/api/assistant/notifications?sender=other',self.cookie)[0],400)
        for body in ({'enabled':1},{'enabled':True,'recipient':'ou_other'},{},None):
            self.assertEqual(self.gateway.dispatch('POST','/api/assistant/notification-settings',self.cookie,body)[0],400)
        self.assertEqual(self.gateway.dispatch('POST','/api/assistant/notifications',self.cookie,{})[0],405)
        self.enable()
        before=self.store.db.total_changes
        self.gateway.dispatch('GET','/api/assistant/notifications',self.cookie)
        self.assertEqual(before,self.store.db.total_changes)

    def test_schema_backup_preserves_memory_version_and_repeat_enable_watermark(self):
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],2)
        result=self.enable();stamp=result['settings']['enabledAt']
        self.assertTrue(self.notice.backup_path.exists())
        backup=sqlite3.connect(self.notice.backup_path)
        self.assertFalse(tables(backup));self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],2);backup.close()
        self.now+=10
        self.assertEqual(self.notice.set_enabled(self.who,True)['settings']['enabledAt'],stamp)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],2)

    def test_first_enable_ignores_old_events_and_existing_pending(self):
        self.outcome()
        self.pending('old')
        self.now+=1;self.enable();self.notice.tick(self.send)
        self.assertFalse(self.sends)
        self.assertFalse(read_notifications(self.store.db,self.who)['items'])
        self.pending('new');self.notice.tick(self.send)
        self.assertEqual(len(self.sends),1)

    def test_duplicate_tick_and_restart_send_once_with_stable_feed_id(self):
        self.enable();self.pending();self.notice.tick(self.send)
        self.notice.tick(self.send)
        restarted=Notifications(self.store);restarted.tick(self.send)
        self.assertEqual(len(self.sends),1)
        rows=self.store.db.execute("SELECT * FROM interaction_feed WHERE source_channel='agent'").fetchall()
        self.assertEqual(len(rows),1)
        item=self.gateway.dispatch('GET','/api/assistant/feed',self.cookie)[1]['items'][0]
        self.assertEqual((item['sourceChannel'],item['userText'],item['notificationId'],item['deliveryState']),('agent','',rows[0]['id'],'sent'))
        encoded=json.dumps(read_notifications(self.store.db,self.who))
        for hidden in ('ou_owner_fixture','om_fixture_delivery','outbound_uuid','origin_message'):
            self.assertNotIn(hidden,encoded)

    def test_timeout_unknown_is_not_resent_and_publishes_once(self):
        self.enable();self.pending()
        calls=[]
        def timeout(*args):calls.append(args);raise TimeoutError('fixture')
        self.notice.tick(timeout);self.notice.tick(timeout)
        Notifications(self.store).tick(timeout)
        self.assertEqual(len(calls),1)
        self.assertEqual(read_notifications(self.store.db,self.who)['items'][0]['state'],'unknown')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM interaction_feed WHERE source_channel='agent'").fetchone()[0],1)

    def test_sending_crash_becomes_unknown_without_send(self):
        self.enable();self.pending()
        with self.store.db:self.store.db.execute("UPDATE notification_outbox SET state='sending'")
        Notifications(self.store).tick(self.send)
        self.assertFalse(self.sends)
        self.assertEqual(read_notifications(self.store.db,self.who)['items'][0]['state'],'unknown')

    def test_master_off_cancel_and_reenable_no_backfill(self):
        self.enable();self.pending()
        self.notice.set_enabled(self.who,False);self.notice.tick(self.send)
        self.assertFalse(self.sends)
        self.assertEqual(read_notifications(self.store.db,self.who)['items'][0]['state'],'cancelled')
        self.now+=1;self.notice.set_enabled(self.who,True);self.notice.tick(self.send)
        self.assertFalse(self.sends)

    def test_quiet_pending_expiry_and_latest_episode_state_merge(self):
        self.now=datetime(2026,10,17,21,tzinfo=timezone.utc).timestamp() # Berlin 23:00
        self.enable();self.pending()
        self.api.unknown=True
        self.outcome('web_unknown')
        self.notice.tick(self.send);self.assertFalse(self.sends)
        self.api.unknown=False
        episode=next(iter(self.api.proposals.values()))['episodeId']
        self.now+=1;self.chat('仿真进度 '+episode,'web_resolved')
        self.notice.tick(self.send);self.assertFalse(self.sends)
        self.now=datetime(2026,10,18,6,tzinfo=timezone.utc).timestamp() # Berlin 08:00
        self.notice.tick(self.send)
        self.assertEqual(len(self.sends),1)
        self.assertIn('净电费',self.sends[0][1]);self.assertNotIn('没有完整执行回读',self.sends[0][1])
        self.assertNotIn('内容等你确认',self.sends[0][1])

    def test_daily_cursor_independent_and_once_per_household_real_day(self):
        self.enable();episode=self.outcome();self.notice.tick(self.send)
        self.assertEqual(len(self.sends),1)
        self.now=datetime(2026,10,17,18,tzinfo=timezone.utc).timestamp() # Berlin 20:00
        self.notice.tick(self.send);self.notice.tick(self.send)
        self.assertEqual(len(self.sends),2)
        self.assertIn('自上次摘要以来',self.sends[-1][1]);self.assertIn(episode[:8],self.sends[-1][1])
        self.assertIn('不是今日账单或节省',self.sends[-1][1])
        self.now+=24*3600;self.notice.tick(self.send)
        self.assertEqual(len(self.sends),2)

    def test_feishu_reply_covers_specific_unknown_and_second_reconcile(self):
        self.enable();replies=[]
        worker=Worker(self.who['app'],self.store,self.service,lambda message,text,key:replies.append(message) or False)
        def receive(message,text):worker.receive({'message':message,'sender':self.who['sender'],'text':text})
        self.now+=1;receive('om_plan','帮我安排这一时段')
        pending=self.store.pending_record(self.who);episode=pending['data']['episodeId']
        self.api.unknown=True
        self.now+=1;receive('om_confirm','执行 '+pending['token'])
        self.api.unknown=False
        self.now+=1;receive('om_reconcile_second','仿真进度 '+episode)
        self.notice.tick(self.send)
        self.assertFalse(self.sends)
        row=self.store.db.execute("SELECT * FROM notification_outbox WHERE kind='execution_resolved'").fetchone()
        self.assertEqual((row['state'],row['origin_message'],row['delivery_via']),('covered_by_reply','om_reconcile_second','reply'))
        self.assertEqual(self.store.db.execute("SELECT state FROM messages WHERE message='om_reconcile_second'").fetchone()[0],'unknown')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM interaction_feed WHERE source_channel='agent'").fetchone()[0],0)

    def test_owner_rebinding_invalid_timezone_and_stale_pending_gate(self):
        self.enable();self.pending();self.store.timezone(self.who,'not/a_zone')
        self.notice.tick(self.send);self.assertFalse(self.sends)
        self.store.timezone(self.who,'Europe/Berlin')
        self.now+=601;self.notice.tick(self.send);self.assertFalse(self.sends)
        self.pending()
        with self.store.db:self.store.db.execute('UPDATE bindings SET sender=?',('ou_different_fixture',))
        self.notice.tick(self.send);self.assertFalse(self.sends)

    def test_recent_twenty_order(self):
        self.enable()
        with self.store.db:
            for n in range(23):self.notice.enqueue(self.who,'fixture:'+str(n),'execution_unknown','fixture','fixture',[])
        result=read_notifications(self.store.db,self.who)
        self.assertEqual(len(result['items']),20)
        self.assertEqual([r['id'] for r in result['items']],sorted(r['id'] for r in result['items']))

    def test_daily_equal_timestamp_boundary_is_consumed_once(self):
        self.enable()
        self.now=datetime(2026,10,17,18,tzinfo=timezone.utc).timestamp()
        with self.store.db:
            self.notice.enqueue(self.who,'event:boundary:1','execution_outcome','fixture','fixture',['event:boundary:1'])
        self.notice.tick(self.send);self.notice.tick(self.send)
        self.assertEqual(len(self.sends),2)
        self.now+=24*3600;self.notice.tick(self.send)
        self.assertEqual(len(self.sends),2)

    def test_aggregate_omits_expired_pending_but_keeps_other_facts(self):
        self.enable();self.pending()
        child=self.store.db.execute("SELECT * FROM notification_outbox WHERE kind='pending_attention'").fetchone()
        with self.store.db:
            event=self.notice.enqueue(self.who,'event:fixture:1','execution_outcome','fixture','verified fixture interval',['event:fixture:1'])
            self.notice.enqueue(self.who,'fixture:aggregate','quiet_digest','quiet','old pending text',[child['source_key'],'event:fixture:1'])
            self.store.db.execute("UPDATE notification_outbox SET state='cancelled' WHERE id IN (?,?)",(child['id'],event))
        self.now+=601;self.notice.tick(self.send)
        self.assertEqual(len(self.sends),1)
        self.assertEqual(self.sends[0][1],'verified fixture interval')

    def test_reply_coverage_and_sending_share_one_commit(self):
        self.enable();self.now+=1
        self.store.notification_origin='om_atomic_fixture';self.pending()
        self.store.notification_origin=None
        self.store.claim(self.who['app'],'om_atomic_fixture','fixture_uuid',self.who['sender'],'fixture')
        trace=[];self.store.db.set_trace_callback(trace.append)
        self.store.state(self.who['app'],'om_atomic_fixture','sending')
        self.store.db.set_trace_callback(None)
        self.assertEqual(sum(line.strip()=='COMMIT' for line in trace),1)
        self.assertTrue(any('covered_by_reply' in line for line in trace))
        self.assertEqual(self.store.db.execute("SELECT state FROM notification_outbox WHERE origin_message='om_atomic_fixture'").fetchone()[0],'covered_by_reply')

    def test_process_app_scope_does_not_claim_another_apps_outbox(self):
        self.enable();self.pending()
        before=read_notifications(self.store.db,self.who)
        self.notice.tick(self.send,'cli_other_process')
        self.assertFalse(self.sends)
        self.assertEqual(read_notifications(self.store.db,self.who),before)
        self.notice.tick(self.send,self.who['app'])
        self.assertEqual(len(self.sends),1)

    def test_definite_delivery_failure_is_terminal_without_device_failure_claim(self):
        self.enable();self.pending()
        self.notice.tick(lambda *args:{'state':'failed'})
        self.notice.tick(self.send)
        self.assertFalse(self.sends)
        row=read_notifications(self.store.db,self.who)['items'][0]
        self.assertEqual(row['state'],'failed')
        feed=self.gateway.dispatch('GET','/api/assistant/feed',self.cookie)[1]['items'][0]
        self.assertEqual(feed['deliveryState'],'failed');self.assertIn('飞书消息发送失败',feed['agentText'])

    def test_sdk_5xx_and_missing_receipt_stay_unknown(self):
        def response(status,success=True,message='om_fixture'):
            return SimpleNamespace(raw=SimpleNamespace(status_code=status),success=lambda:success,data=SimpleNamespace(message_id=message))
        self.assertEqual(notification_delivery(response(500)),{'state':'unknown'})
        self.assertEqual(notification_delivery(response(200,message=None)),{'state':'unknown'})
        self.assertEqual(notification_delivery(response(403,False)),{'state':'failed'})
        self.assertEqual(notification_delivery(response(200)),{'state':'sent','messageId':'om_fixture'})

    def test_frontend_proxy_forwards_only_fixed_notification_routes(self):
        path=Path(__file__).resolve().parents[2]/'frontend/checks/consumer-ui/test_proxy.py'
        spec=importlib.util.spec_from_file_location('notification_proxy_fixture',path)
        proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)
        checker=proxy.ProxyTests()
        proxy.Connection.requests=[]
        body=b'{"enabled":true}'
        handler=checker.handler('POST','/api/assistant/notification-settings',body,{'Content-Length':str(len(body)),'Cookie':'fixture=only','Origin':'http://127.0.0.1:19388'})
        with patch.object(proxy.module.http.client,'HTTPConnection',proxy.Connection):handler._assistant_proxy()
        self.assertEqual(len(proxy.Connection.requests),1)
        self.assertEqual(proxy.Connection.requests[0][1:3],('/api/assistant/notification-settings',body))
        self.assertEqual(proxy.Connection.requests[0][3]['Cookie'],'fixture=only')
        handler=checker.handler('POST','/api/assistant/notifications',b'{}',{'Content-Length':'2'})
        handler._assistant_proxy();self.assertEqual(handler.errors[0][0],405)



    def outcome_at_current_time(self, label):
        self.chat('帮我安排这一时段',label+'_plan')
        pending=self.store.pending_record(self.who)
        self.chat('执行 '+pending['token'],label+'_execute')
        return pending['data']['episodeId']

    def legacy_notification_schema(self):
        self.store.db.executescript("""
        CREATE TABLE notification_settings(app TEXT,sender TEXT,household TEXT,enabled INTEGER,
          enabled_at REAL,scan_at REAL,daily_cutoff REAL,daily_date TEXT,pending_fingerprint TEXT,
          PRIMARY KEY(app,sender,household));
        CREATE TABLE notification_outbox(id TEXT PRIMARY KEY,app TEXT,sender TEXT,household TEXT,
          source_key TEXT,kind TEXT,state TEXT,title TEXT,text TEXT,created_at REAL,updated_at REAL,due_at REAL,
          episode_id TEXT,source_refs TEXT,origin_message TEXT,outbound_uuid TEXT,provider_id TEXT,
          delivery_via TEXT,consumed_by TEXT,feed_published INTEGER NOT NULL DEFAULT 0,
          UNIQUE(app,sender,household,source_key));
        """)

    def test_first_enable_equal_time_ignores_old_event_but_sends_new_once(self):
        old=self.outcome_at_current_time('web_old')
        self.enable();self.notice.tick(self.send,self.who['app'])
        self.assertFalse(self.sends)
        self.assertEqual(read_notifications(self.store.db,self.who)['items'],[])
        new=self.outcome_at_current_time('web_new')
        for _ in range(3):self.notice.tick(self.send,self.who['app'])
        sent=self.store.db.execute("SELECT episode_id FROM notification_outbox WHERE state='sent'").fetchall()
        self.assertNotEqual(old,new);self.assertEqual([row[0] for row in sent],[new])
        self.assertEqual(len(self.sends),1)

    def test_reenable_equal_time_baseline_survives_restart(self):
        self.enable();self.notice.set_enabled(self.who,False)
        self.outcome_at_current_time('web_disabled')
        self.notice.set_enabled(self.who,True)
        self.notice=Notifications(self.store)
        self.notice.tick(self.send,self.who['app']);self.assertFalse(self.sends)
        self.outcome_at_current_time('web_reenabled')
        for _ in range(3):self.notice.tick(self.send,self.who['app'])
        Notifications(self.store).tick(self.send,self.who['app'])
        self.assertEqual(len(self.sends),1)

    def test_enable_baseline_snapshot_begins_before_its_reads(self):
        self.outcome_at_current_time('web_before_snapshot')
        self.store.timezone(self.who,'Europe/Berlin');self.notice.ensure_schema()
        trace=[];self.store.db.set_trace_callback(trace.append)
        try:self.notice.set_enabled(self.who,True)
        finally:self.store.db.set_trace_callback(None)
        begin=trace.index('BEGIN IMMEDIATE')
        event_read=next(i for i,sql in enumerate(trace) if sql.startswith('SELECT e.id,e.state,v.sequence'))
        write=next(i for i,sql in enumerate(trace) if sql.startswith('INSERT OR REPLACE INTO notification_settings'))
        self.assertLess(begin,event_read);self.assertLess(event_read,write);self.assertLess(write,trace.index('COMMIT'))
        reader=sqlite3.connect(self.store.path)
        try:self.assertEqual(len(json.loads(reader.execute('SELECT event_baseline FROM notification_settings').fetchone()[0])),1)
        finally:reader.close()

    def test_legacy_get_and_tick_never_migrate_then_idle_upgrade_backs_up_once(self):
        self.legacy_notification_schema();self.store.timezone(self.who,'Europe/Berlin')
        before=self.store.db.serialize();dump=tuple(self.store.db.iterdump())
        code,result,_=self.gateway.dispatch('GET','/api/assistant/notifications',self.cookie)
        self.assertEqual((code,result['settings']['enabled']),(200,False))
        self.notice.tick(self.send,self.who['app'])
        self.assertEqual(before,self.store.db.serialize());self.assertFalse(self.sends)
        self.assertIsNone(self.notice.backup_path)
        self.notice.set_enabled(self.who,True);backup=self.notice.backup_path
        reader=sqlite3.connect(backup)
        try:
            self.assertEqual(tuple(reader.iterdump()),dump)
            self.assertNotIn('event_baseline',{row[1] for row in reader.execute('PRAGMA table_info(notification_settings)')})
        finally:reader.close()
        self.notice.set_enabled(self.who,True);self.notice.ensure_schema()
        self.assertEqual(self.notice.backup_path,backup)
        self.assertEqual(len(list(self.store.path.parent.glob('*.before-notifications-*.sqlite3'))),1)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],2)

    def test_active_legacy_requires_explicit_off_on_without_guessing_facts(self):
        self.legacy_notification_schema();self.store.timezone(self.who,'Europe/Berlin')
        with self.store.db:
            self.store.db.execute('INSERT INTO notification_settings VALUES(?,?,?,?,?,?,?,?,?)',
                (*self.store.scope(self.who),1,self.now,self.now,self.now,None,None))
        before=self.store.db.serialize()
        with self.assertRaisesRegex(ValueError,'notification_migration_requires_disabled'):self.notice.ensure_schema()
        self.notice.tick(self.send,self.who['app'])
        self.assertEqual(before,self.store.db.serialize());self.assertFalse(self.sends)
        self.assertIsNone(self.notice.backup_path)
        self.notice.set_enabled(self.who,False)
        self.assertNotIn('event_baseline',{row[1] for row in self.store.db.execute('PRAGMA table_info(notification_settings)')})
        self.notice.set_enabled(self.who,True)
        self.outcome_at_current_time('web_after_migration');self.notice.tick(self.send,self.who['app'])
        self.assertEqual(len(self.sends),1)


if __name__=='__main__':unittest.main()
