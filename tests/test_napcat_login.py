import base64
import hashlib
import unittest
from unittest.mock import patch, call

from qqmanager.napcat_login import NapCatLogin


def success(data):
    return {'code': 0, 'data': data}


class NapCatLoginTest(unittest.TestCase):
    def setUp(self):
        self.login = NapCatLogin()
        mock = patch('qqmanager.napcat_login._post')
        self.post = mock.start()
        self.addCleanup(mock.stop)

    def test_connection_and_authentication(self):
        self.post.side_effect = [ConnectionRefusedError(), {'code': -1, 'message': 'Unauthorized'},
                                 success({'require2FA': True}), success({'Credential': 'local-session'}),
                                 success({'loginPhase': 'waiting_qrcode', 'qrcodeurl': 'https://txz.qq.com/p?k=sample'}),
                                 success({'qrLoginAccepted': True}), success({'isLogin': True})]
        self.assertEqual(self.login.update({})['state'], 'unavailable')
        self.assertEqual(self.login.update({})['state'], 'authorize')
        self.assertEqual(self.login.update({'token': 'local-token'})['state'], 'two_factor')
        result = self.login.update({'token': 'local-token', 'otp': '012345'})
        self.assertEqual(result['state'], 'ready')
        self.assertTrue(base64.b64decode(result['image'].split(',')[1]).startswith(b'\x89PNG'))
        self.assertEqual(self.login.update({})['state'], 'scanned')
        self.assertEqual(self.login.update({})['state'], 'online')
        self.assertIn(call('auth/login', {'hash': hashlib.sha256(b'local-token.napcat').hexdigest(),
                                          'totpCode': '012345'}), self.post.call_args_list)
        self.assertEqual(self.post.call_args, call('QQLogin/CheckLoginStatus', {}, 'local-session'))

    def test_refresh_discards_old_code(self):
        expired = success({'loginError': 'expired', 'qrcodeurl': 'https://txz.qq.com/old'})
        self.post.side_effect = [expired, expired, success({'qrcodeurl': ''})]
        self.assertEqual(self.login.update({}), {'state': 'refresh_needed'})
        self.assertEqual(self.login.update({'refresh': True}), {'state': 'waiting'})
        self.assertEqual(self.post.call_args, call('QQLogin/RefreshQRcode', {}, ''))

    def test_never_refreshes_an_online_account(self):
        self.post.return_value = success({'isLogin': True})
        self.assertEqual(self.login.update({'refresh': True}), {'state': 'online'})
        self.assertEqual(self.post.call_count, 1)

    def test_rejects_non_qq_link(self):
        self.post.return_value = success({'qrcodeurl': 'https://txz.qq.com.example.org/login'})
        with self.assertRaises(ValueError):
            self.login.update({})


class QQBindingTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from qqmanager.connector import Connector
        from qqmanager.napcat_login import QQBinding
        from qqmanager.store import Store
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(directory.name)
        self.addCleanup(self.store.db.close)
        self.binding = QQBinding(self.store, Connector(self.store))
        self.binding.runtime.keys = {'onebot_token': 'socket-test', 'webui_token': 'web-test'}
        self.binding.runtime.phase = 'ready'

    def bind(self, account):
        with patch('qqmanager.napcat_login._post', side_effect=[
                success({'Credential': 'test-session'}), success({'isLogin': True}), success({'uin': account})]):
            return self.binding.update({})

    def test_bind_and_return_without_inheriting_unowned_groups(self):
        self.store.set_setting('bridge_groups', ['555555'])
        self.assertEqual(self.bind('123456')['state'], 'bound')
        self.assertEqual(self.store.setting('ws_account'), '123456')
        self.assertEqual(self.store.setting('bridge_groups'), [])
        self.assertTrue(self.store.setting('ws_enabled'))
        self.assertEqual((self.binding.runtime.base/'config/qqmanager-account').read_text(),'123456')

    def test_existing_binding_keeps_group_consent(self):
        self.store.set_setting('ws_account', '123456')
        self.store.set_setting('bridge_groups', ['555555'])
        self.assertFalse(self.bind('123456')['select_groups'])
        self.assertEqual(self.store.setting('bridge_groups'), ['555555'])

    def test_other_account_cannot_take_over_existing_workspace(self):
        self.store.set_setting('ws_account', '654321')
        self.assertEqual(self.bind('123456')['state'], 'failed')
        self.assertEqual(self.store.setting('ws_account'), '654321')
        self.assertFalse(self.store.setting('ws_enabled'))

    def test_logout_disconnects_sources_persists_and_rejects_stale_polls(self):
        self.bind('123456')
        self.store.set_setting('bridge_groups', ['555555'])
        self.store.set_setting('bridge_enabled', True)
        self.store.set_setting('native_enabled', True)
        self.store.set_setting('interests', '考试')
        old_token = self.store.setting('native_token')
        with self.assertRaises(ValueError):
            self.binding.logout('999999', '')
        self.binding.logout('123456', '')
        self.binding.logout('123456', '')
        for key in ('ws_account', 'ws_enabled', 'ws_token', 'bridge_groups', 'bridge_enabled',
                    'native_enabled', 'qq_managed_login'):
            self.assertFalse(self.store.setting(key))
        self.assertNotEqual(self.store.setting('native_token'), old_token)
        self.assertEqual(self.store.setting('interests'), '考试')
        self.assertFalse((self.binding.runtime.base/'config/qqmanager-account').exists())
        revision = self.store.setting('qq_login_revision')
        self.assertTrue(revision)
        self.assertEqual(self.binding.update({})['state'], 'expired')
        # Persisted provider login requires an explicit choice after signing out locally.
        with patch('qqmanager.napcat_login._post', side_effect=[
                success({'Credential': 'session'}), success({'isLogin': True}), success({'uin': '123456'}),
                success({'isLogin': True}), success({'uin': '123456'})]):
            self.assertEqual(self.binding.update({'revision': revision})['state'], 'confirm_account')
            self.assertFalse(self.store.setting('ws_account'))
            self.assertEqual(self.binding.update({'revision': revision, 'confirm_account': '123456'})['state'], 'bound')
