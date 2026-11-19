import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from qqmanager.connector import Connector, validate_url
from qqmanager.store import Store


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        self.connector = Connector(self.store)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def login(self, uid='456'):
        self.connector.pending = {'1': ('get_login_info', 0), '2': ('get_group_list', 0)}
        self.connector.handle_packet({'echo': '1', 'status': 'ok', 'retcode': 0, 'data': {'user_id': uid, 'nickname': '测试账号'}})
        self.connector.handle_packet({'echo': '2', 'status': 'ok', 'retcode': 0, 'data': [
            {'group_id': 123, 'group_name': '项目群'}, {'group_id': 789, 'group_name': '未选群'}]})

    def event(self, **overrides):
        return dict({'post_type': 'message', 'message_type': 'group', 'group_id': 123,
                     'self_id': 456, 'message_id': 91, 'time': 1794618000,
                     'sender': {'nickname': '群友'}, 'message': [{'type': 'text', 'data': {'text': '请提交报告'}}]}, **overrides)

    def test_select_groups_and_only_selected_messages_persist(self):
        self.login()
        self.connector.handle_packet(self.event())
        self.assertEqual(self.store.snapshot()['counts']['messages'], 0)
        self.connector.select_groups(['123'])
        self.connector.handle_packet(self.event())
        self.connector.handle_packet(self.event(group_id=789, message_id=92))
        self.assertEqual(self.store.snapshot()['counts']['messages'], 1)
        self.assertEqual(self.store.snapshot()['messages'][0]['group_name'], '项目群')
        self.assertEqual(self.connector.snapshot()['saved'], 1)

    def test_restart_and_renamed_group_dedup(self):
        self.login(); self.connector.select_groups(['123'])
        self.connector.handle_packet(self.event())
        self.connector = Connector(self.store); self.login()
        self.connector.update(groups=[{'group_id': '123', 'group_name': '新群名'}])
        self.connector.handle_packet(self.event(sender={'nickname': '新昵称'}))
        self.assertEqual(self.store.snapshot()['counts']['messages'], 1)

    def test_own_group_message_and_duplicate_event_forms(self):
        self.login(); self.connector.select_groups(['123'])
        self.connector.handle_packet(self.event(post_type='message_sent'))
        self.connector.handle_packet(self.event(post_type='message'))
        self.assertEqual(self.store.snapshot()['counts']['messages'], 1)

    def test_account_switch_requires_selection_again(self):
        self.login(); self.connector.select_groups(['123'])
        self.login('999')
        self.connector.handle_packet(self.event(self_id=999))
        self.assertEqual(self.store.snapshot()['counts']['messages'], 0)
        self.connector.select_groups(['123'])
        self.connector.handle_packet(self.event(self_id=999))
        self.assertEqual(self.store.snapshot()['counts']['messages'], 1)

    def test_invalid_group_and_stop_collection(self):
        self.login()
        with self.assertRaises(ValueError): self.connector.select_groups(['9999'])
        self.connector.select_groups(['123']); self.connector.select_groups([])
        self.connector.handle_packet(self.event())
        self.assertFalse(self.store.setting('bridge_enabled'))
        self.assertEqual(self.store.snapshot()['counts']['messages'], 0)

    def test_managed_history_resume_replay_and_revocation(self):
        from unittest.mock import Mock
        self.login()
        self.store.set_setting('ws_enabled',True)
        self.store.set_setting('qq_managed_login',True)
        self.connector.select_groups(['123','789'], {'123':'2020-01-01'})
        self.assertIn('未选群',self.store.snapshot()['groups'])
        db=self.store.db
        def request():
            socket=Mock()
            self.connector.history_tick=0
            self.connector.poll_history(socket)
            return {p['params']['group_id']:p for (raw,),_ in socket.send.call_args_list for p in [json.loads(raw)]}
        def reply(req, rows):
            self.connector.handle_packet({'echo':req['echo'],'retcode':0,'data':{'messages':rows}})
        req=request()
        m=self.event()
        same_second=self.event(message_id=92,message=[])
        reply(req['123'],[m,same_second])
        second=request()['123']
        self.assertEqual(second['params']['message_seq'],'91')
        reply(second,[self.event(time=1,message_id=90),m])
        self.assertEqual(db.execute("select cursor from connector_history where group_id='123'").fetchone()[0],m['time'])
        self.assertEqual(len(self.store.backfill_receipts()),2)
        self.assertIn('[其他消息类型：未解析]',[r['preview'] for r in self.store.backfill_receipts()])
        self.connector.handle_packet(m)
        self.connector=Connector(self.store)
        self.login()
        replay=request()
        arriving_offline=self.event(message_id=93,time=m['time']+1)
        reply(replay['123'],[m,same_second,arriving_offline])
        continuation=request()['123']
        reply(continuation,[self.event(time=1,message_id=90),m])
        self.connector.handle_packet(arriving_offline)
        self.assertEqual(len(self.store.backfill_receipts()),3)
        self.assertEqual(db.execute("select cursor from connector_history where group_id='123'").fetchone()[0],m['time']+1)
        self.store.read_backfill([r['id'] for r in self.store.backfill_receipts()])
        self.assertEqual(self.store.snapshot()['backfill_count'],0)
        self.connector.select_groups(['123'])
        reply(replay['789'],[self.event(group_id=789)])
        self.assertEqual(db.execute('select count(*) from messages').fetchone()[0],3)
        self.assertEqual(len(self.store.snapshot()['groups']),1)
        self.assertEqual(self.store.snapshot()['history_groups'],['项目群'])

    def test_managed_archive_retains_card_titles_and_empty_selected_group(self):
        self.login()
        self.connector.select_groups(['123','789'])
        self.connector.handle_packet(self.event(message=[{'type':'json','data':{'data':json.dumps({
            'meta':{'news':{'title':'生涯启航 科学规划 逐梦未来'}}})}}]))
        self.connector.handle_packet(self.event(message_id=92,message=[{'type':'at','data':{'qq':'all'}}]))
        snapshot=self.store.snapshot()
        self.assertEqual({r['content'] for r in snapshot['messages']},{'[分享卡片] 生涯启航 科学规划 逐梦未来','@全体成员'})
        self.connector.select_groups(['789'])
        self.assertEqual(self.store.snapshot()['groups'],['未选群'])
        self.assertIn('项目群',self.store.snapshot()['history_groups'])

    def test_only_loopback_urls(self):
        self.assertEqual(validate_url('ws://127.0.0.1:3001'), 'ws://127.0.0.1:3001')
        for url in ['ws://example.org:3001', 'ws://127.0.0.1:3001?access_token=x', 'ws://user:pass@localhost:3001', 'http://localhost:3001']:
            with self.assertRaises(ValueError): validate_url(url)

    def test_readonly_rpc_and_offline_heartbeat(self):
        self.login()
        self.connector.handle_packet({'post_type': 'meta_event', 'meta_event_type': 'heartbeat', 'status': {'online': False}})
        self.assertEqual(self.connector.snapshot()['status'], 'waiting_login')
        with self.assertRaises(ValueError): self.connector.rpc(None, 'send_group_msg')

    def test_actual_worker_dispatch_and_reconnect_without_secret_in_errors(self):
        self.store.set_setting('ws_enabled', True)
        self.store.set_setting('ws_token', 'private-test-token')
        self.store.set_setting('ws_account', '456')
        self.store.set_setting('bridge_groups', ['123'])
        self.store.set_setting('bridge_enabled', True)
        stop = threading.Event()
        connector = self.connector

        class FakeSocket:
            def __init__(self): self.replies = []; self.closed = False
            def settimeout(self, value): pass
            def send(self, raw):
                request = json.loads(raw)
                data = {'get_login_info': {'user_id': 456, 'nickname': '测试'}, 'get_group_list': [{'group_id': 123, 'group_name': '项目群'}], 'get_status': {'online': True}}[request['action']]
                self.replies.append(json.dumps({'echo': request['echo'], 'retcode': 0, 'status': 'ok', 'data': data}))
            def recv(self):
                if self.replies: return self.replies.pop(0)
                stop.set()
                return json.dumps(ConnectorTests.event(self))
            def close(self): self.closed = True

        socket = FakeSocket()
        with patch('websocket.create_connection', return_value=socket) as create:
            self.connector.run(stop)
            self.assertEqual(create.call_args.kwargs['header']['Authorization'], 'Bearer private-test-token')
            self.assertTrue(socket.closed)
        self.assertEqual(self.store.snapshot()['counts']['messages'], 1)
        self.assertNotIn('private-test-token', json.dumps(connector.snapshot()))


if __name__ == '__main__': unittest.main()
