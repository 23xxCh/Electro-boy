"""CH-03 temporary-owner Feishu flow; real local LP, no HA or Feishu network."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from assistant.runtime_api import RuntimeOutcomeUnknown
from assistant.runtime_tools import RuntimeTools
from assistant.service import Service
from assistant.store import Store
from assistant.web_gateway import WebGateway, COOKIE
from channels.feishu import Worker, normalize
from energy_runtime.runtime import Runtime
from test_decision import API, Source


class NoModel:
    def intent(self, *args):
        raise RuntimeError('offline')

    def advice(self, *args):
        raise RuntimeError('offline')


class CH03Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'assistant.sqlite'
        self.store = Store(self.path)
        self.owner = {'app': 'cli_qa_ch03', 'sender': 'ou_qa_ch03', 'household': 'site_1'}
        self.store.bind(self.owner['app'], self.owner['sender'], self.store.pair_code(self.owner['app']))
        self.source = Source()
        self.core = Runtime(self.source, Path(self.temp.name) / 'core.sqlite')
        self.api = API(self.core, self.source)
        self.model = NoModel()
        self.service = Service(self.store, object(), self.model,
                               lambda: datetime(2026, 10, 17, tzinfo=timezone.utc),
                               runtime=RuntimeTools(self.store, self.api))
        self.replies = []
        self.worker = Worker(self.owner['app'], self.store, self.service,
                             lambda *args: self.replies.append(args) or True)
        self.sequence = 0

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def send(self, text, message=None, sender=None):
        self.sequence += 1
        raw = {'header': {'app_id': self.owner['app'], 'event_type': 'im.message.receive_v1'},
               'event': {'sender': {'sender_type': 'user', 'sender_id': {'open_id': sender or self.owner['sender']}},
                         'message': {'message_id': message or 'om_qa_ch03_' + str(self.sequence),
                                     'chat_id': 'oc_qa_ch03', 'chat_type': 'p2p', 'message_type': 'text',
                                     'content': json.dumps({'text': text})}}}
        self.worker.receive(normalize(raw, self.owner['app']))
        return self.replies[-1][1]

    def pending(self):
        return self.store.pending_record(self.owner)

    def test_bound_welcome_does_not_request_another_binding(self):
        reply = self.send('你好')
        self.assertNotIn('先用', reply)
        self.assertIn('本机', reply)
        self.assertIn('记下', reply)
        self.assertIn('执行', reply)
        self.assertIn('先用', self.send('你好', sender='ou_qa_unbound'))
        self.assertEqual(self.source.execute_calls, 0)

    def test_common_reads_do_not_depend_on_model_or_write_device(self):
        for text in ('状态', '当前状态', '家里现在怎么样？', '电池还有多少电'):
            self.assertIn('最新设备回读', self.send(text))
        for text in ('资料', '我的偏好', '你记住了什么？'):
            self.assertIn('目前没有保存', self.send(text))
        self.assertEqual(self.source.execute_calls, 0)
        self.assertIsNone(self.pending())

    def test_explicit_reserve_is_candidate_then_separate_execution(self):
        self.send('我想设置最低留电量为50%')
        memory = self.pending()
        self.assertEqual(memory['data']['constraint']['value']['minimumPercent'], 50)
        self.assertEqual(self.store.memories(self.owner), [])
        self.assertEqual(self.source.execute_calls, 0)
        self.assertIn('不匹配', self.send('执行 ' + memory['token']))
        self.send('记下 ' + memory['token'])
        plan = self.pending()
        self.assertEqual(plan['data']['action'], 'runtime_plan')
        self.assertNotEqual(plan['token'], memory['token'])
        self.assertEqual(len(self.store.memories(self.owner)), 1)
        self.assertEqual(self.source.execute_calls, 0)
        self.assertIn('确认号', self.send('执行吧'))
        self.assertEqual(self.pending()['token'], plan['token'])
        self.assertEqual(self.source.execute_calls, 0)
        complete = self.send('执行 ' + plan['token'], message='om_qa_ch03_execute')
        self.assertIn('读回核验并结算', complete)
        self.assertIn('实际电池', complete)
        self.assertIn('SOC', complete)
        self.assertEqual(self.source.execute_calls, 1)
        self.send('执行 ' + plan['token'], message='om_qa_ch03_execute')
        self.assertEqual(self.source.execute_calls, 1)

    def test_greeting_and_reads_do_not_replace_pending(self):
        self.send('保底 50%')
        token = self.pending()['token']
        self.send('帮助')
        self.send('我的偏好')
        self.assertEqual(token, self.pending()['token'])

    def test_receipt_does_not_borrow_other_episode_or_missing_actual(self):
        self.send('帮我安排这一时段')
        plan=self.pending()
        self.send('执行 '+plan['token'])
        episode_id=plan['data']['episodeId']
        decision=self.api.decision
        original=decision(self.owner,episode_id=episode_id)
        for field,value in (('episodeId','foreign'),('actual',None)):
            self.api.decision=lambda *args,**kwargs:{**original,field:value}
            reply=self.service.runtime.receipt_text(self.owner,episode_id)
            self.assertIn('读回核验并结算',reply)
            self.assertNotIn('实际电池',reply)
        self.assertEqual(self.source.execute_calls,1)

    def test_future_dated_reserve_does_not_bypass_model_as_timeless(self):
        self.send('明天最低留电量70%')
        self.assertIsNone(self.pending())
        self.assertEqual(self.store.memories(self.owner), [])

    def test_question_does_not_become_reserve_candidate(self):
        self.send('设置最低留电量55%？')
        self.assertIsNone(self.pending())
        self.assertEqual(self.store.memories(self.owner), [])

    def test_old_and_other_owner_tokens_do_not_execute(self):
        self.send('保底 50%')
        old = self.pending()['token']
        self.send('保底 60%')
        new = self.pending()['token']
        self.assertIn('不匹配', self.send('记下 ' + old))
        self.send('记下 ' + new, sender='ou_qa_unbound')
        self.assertEqual(self.store.memories(self.owner), [])
        self.assertEqual(self.pending()['token'], new)
        self.assertEqual(self.source.execute_calls, 0)

    def test_confirmed_habit_survives_restart_and_remains_context_only(self):
        self.model.intent = lambda *args: {'action': 'memory', 'kind': 'habit', 'key': '到家时间', 'value': '通常18点到家'}
        self.send('记住我通常18点到家')
        self.assertEqual(self.store.memories(self.owner), [])
        self.send('记下 ' + self.pending()['token'])
        reopened = Store(self.path)
        try:
            memory = reopened.enable_memory_v2()
            result = memory.effective_constraints(self.owner, '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'device_local_naive')
            self.assertEqual(len(reopened.memories(self.owner)), 1)
            self.assertEqual(result['usedMemoryIds'], [])
            self.assertEqual(len(result['contextOnlyMemoryIds']), 1)
        finally:
            reopened.db.close()

    def test_unknown_uses_original_episode_only_and_never_reconfirms(self):
        self.send('帮我安排这一时段')
        plan = self.pending()
        calls = {'confirm': 0, 'reconcile': 0}
        reconcile = self.api.reconcile

        def unknown(*args):
            calls['confirm'] += 1
            raise RuntimeOutcomeUnknown('timeout')

        def read(identity, episode):
            calls['reconcile'] += 1
            self.assertEqual(episode, plan['data']['episodeId'])
            return reconcile(identity, episode)

        self.api.confirm, self.api.reconcile = unknown, read
        reply = self.send('执行 ' + plan['token'])
        self.assertIn(plan['data']['episodeId'], reply)
        self.assertIn('未重发', reply)
        self.send('仿真进度 ' + plan['data']['episodeId'])
        self.send('执行 ' + plan['token'])
        self.assertEqual(calls, {'confirm': 1, 'reconcile': 2})
        self.assertEqual(self.source.execute_calls, 0)

    def test_worker_exception_never_claims_control_was_not_sent(self):
        self.service.handle = lambda *args: (_ for _ in ()).throw(RuntimeError('after_possible_write'))
        reply = self.send('执行 ABCDEF')
        self.assertNotIn('没有下发', reply)
        self.assertIn('未核实', reply)
        self.assertIn('仿真进度', reply)

    def test_feishu_and_web_share_feed_memory_and_selected_episode(self):
        gateway = WebGateway(self.owner['app'], self.store, self.service)
        _, _, token = gateway.dispatch('GET', '/api/assistant/session', None)
        cookie = COOKIE + '=' + token
        self.send('保底 50%')
        self.send('记下 ' + self.pending()['token'])
        plan = self.pending()
        feed = gateway.read_dispatch('/api/assistant/feed', cookie)[1]
        session = gateway.dispatch('GET', '/api/assistant/session', cookie)[1]
        decision = gateway.read_dispatch('/api/assistant/decision', cookie)[1]
        self.assertTrue(all(row['sourceChannel'] == 'feishu' for row in feed['items']))
        self.assertEqual(session['memoryVersion'], 1)
        self.assertEqual(session['pending']['token'], plan['token'])
        self.assertEqual(decision['episodeId'], plan['data']['episodeId'])
        self.assertNotIn('om_qa', json.dumps(feed))
        self.assertEqual(self.source.execute_calls, 0)


if __name__ == '__main__':
    unittest.main()
