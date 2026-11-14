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
