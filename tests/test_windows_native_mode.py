import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from qqmanager.server import make_server
from qqmanager.store import Store


class WindowsNativeModeTests(unittest.TestCase):
    """Windows never starts the managed container login runtime."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
        self.store.db.close()
        self.temp.cleanup()

    def start_windows_server(self):
        with patch('qqmanager.server.sys.platform', 'win32'):
            self.server = make_server(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.csrf = self.get('/api/session')['csrf']

    def get(self, path):
        with urllib.request.urlopen(self.url + path) as response:
            return json.load(response)

    def post(self, path, body, csrf=True):
        headers = {'Content-Type': 'application/json'}
        if csrf:
            headers['X-CSRF-Token'] = self.csrf
        request = urllib.request.Request(self.url + path, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def assert_bad_request(self, path, body):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(path, body)
        self.assertEqual(error.exception.code, 400)

    def test_legacy_managed_profile_is_retired_without_docker_or_data_loss(self):
        self.store.set_setting('ws_account', '123456')
        self.store.set_setting('bridge_groups', ['555555', '666666'])
        self.store.set_setting('bridge_enabled', True)
        self.store.set_setting('ws_enabled', True)
        self.store.set_setting('ws_token', 'legacy-container-token')
        self.store.set_setting('qq_managed_login', True)
        self.store.import_data('history.txt', '旧群消息'.encode(), '旧群', 'chat')

        with patch('qqmanager.login_runtime.LoginRuntime', side_effect=AssertionError('must not start Docker')) as runtime:
            self.start_windows_server()
        runtime.assert_not_called()

        self.assertEqual(self.store.setting('ws_account'), '123456')
        self.assertEqual(self.store.setting('bridge_groups'), ['555555', '666666'])
        self.assertTrue(self.store.setting('bridge_enabled'))
        self.assertFalse(self.store.setting('qq_managed_login'))
        self.assertFalse(self.store.setting('ws_enabled'))
        self.assertEqual(self.store.setting('ws_token'), '')
        self.assertEqual(self.store.snapshot()['counts']['messages'], 1)
        connector = self.get('/api/connector')
        self.assertFalse(connector['managed_login_supported'])
        self.assertFalse(connector['managed'])
        self.assertFalse(connector['enabled'])
        self.assertEqual(connector['selected_account'], '123456')
        self.assertEqual(connector['selected'], ['555555', '666666'])

    def test_external_connector_is_left_as_configured(self):
        self.store.set_setting('ws_account', '123456')
        self.store.set_setting('ws_url', 'ws://127.0.0.1:3001')
        self.store.set_setting('ws_token', 'external-onebot-token')
        self.store.set_setting('ws_enabled', True)
        self.store.set_setting('qq_managed_login', False)

        with patch('qqmanager.login_runtime.LoginRuntime', side_effect=AssertionError('must not create runtime')) as runtime:
            self.start_windows_server()
        runtime.assert_not_called()

        self.assertTrue(self.store.setting('ws_enabled'))
        self.assertEqual(self.store.setting('ws_token'), 'external-onebot-token')
        self.assertFalse(self.store.setting('qq_managed_login'))
        connector = self.get('/api/connector')
        self.assertTrue(connector['enabled'])
        self.assertFalse(connector['managed'])

    def test_windows_rejects_container_binding_and_binds_local_account_without_groups(self):
        self.start_windows_server()

        self.assert_bad_request('/api/qq/bind', {'revision': ''})
        self.assert_bad_request('/api/qq/native-account', {'account': '12', 'revision': ''})
        self.assert_bad_request('/api/qq/native-account', {'account': '123456', 'revision': 'stale'})
        self.assertFalse(self.store.setting('ws_account'))

        bound = self.post('/api/qq/native-account', {'account': '123456', 'revision': ''})
        revision = self.store.setting('qq_login_revision')
        self.assertEqual(bound, {'account': '123456'})
        self.assertTrue(revision)
        self.assertEqual(self.store.setting('bridge_groups'), [])
        self.assertFalse(self.store.setting('bridge_enabled'))
        self.assertFalse(self.store.setting('native_enabled'))
        self.assertFalse(self.store.setting('ws_enabled'))

        # Repeating the same local selection does not rotate the revision or grant groups.
        self.assertEqual(self.post('/api/qq/native-account', {'account': '123456', 'revision': 'ignored'}), bound)
        self.assertEqual(self.store.setting('qq_login_revision'), revision)
        self.assertEqual(self.store.setting('bridge_groups'), [])
        self.assert_bad_request('/api/qq/native-account', {'account': '654321', 'revision': revision})
        self.assertEqual(self.store.setting('ws_account'), '123456')


if __name__ == '__main__':
    unittest.main()
