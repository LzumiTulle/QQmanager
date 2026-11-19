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
from qqmanager import windows_accounts


def profile(root, account, emoji_files=('emoji.db',)):
    """Create just the profile files used to determine a live QQ account."""
    database = root / account / 'nt_qq' / 'nt_db'
    database.mkdir(parents=True)
    for name in emoji_files:
        (database / name).touch()


class ActiveAccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_windows_returns_only_profiles_owned_by_running_qq_and_deduplicates(self):
        primary, secondary = self.root / 'one', self.root / 'two'
        primary.mkdir()
        secondary.mkdir()
        profile(primary, '12345', ('emoji.db', 'emoji.db-wal', 'emoji.db-shm'))
        profile(primary, '234567', ('emoji.db',))  # historical directory only
        profile(secondary, '12345', ('emoji.db',))  # duplicate Documents root
        profile(secondary, '345678', ('emoji.db',))
        profile(primary, 'not-an-account', ('emoji.db',))

        calls = []

        def qq_uses_files(paths):
            calls.append(paths)
            return paths[0].parents[2].name in {'12345', '345678'}

        with patch.object(windows_accounts.sys, 'platform', 'win32'), \
             patch.object(windows_accounts, '_profile_roots', return_value=[primary, secondary]), \
             patch.object(windows_accounts, '_qq_uses_files', side_effect=qq_uses_files):
            self.assertEqual(windows_accounts.active_accounts(), ['12345', '345678'])

        # The first selected account was examined once despite appearing in both roots;
        # only existing emoji database files become Restart Manager candidates.
        self.assertEqual(len(calls), 3)
        emoji_files = next(paths for paths in calls if paths[0].parents[2].name == '12345')
        self.assertEqual({path.name for path in emoji_files}, {'emoji.db', 'emoji.db-wal', 'emoji.db-shm'})

    def test_accepts_five_to_twenty_digit_account_names(self):
        profile(self.root, '12345')
        profile(self.root, '12345678901234567890')
        profile(self.root, '1234')
        profile(self.root, '123456789012345678901')

        with patch.object(windows_accounts.sys, 'platform', 'win32'), \
             patch.object(windows_accounts, '_profile_roots', return_value=[self.root]), \
             patch.object(windows_accounts, '_qq_uses_files', return_value=True) as uses_files:
            self.assertEqual(windows_accounts.active_accounts(), ['12345', '12345678901234567890'])
        self.assertEqual(uses_files.call_count, 2)

    def test_non_windows_and_unavailable_roots_are_safe(self):
        with patch.object(windows_accounts.sys, 'platform', 'darwin'), \
             patch.object(windows_accounts, '_profile_roots', side_effect=AssertionError('must not probe')):
            self.assertEqual(windows_accounts.active_accounts(), [])

        missing = self.root / 'missing'
        with patch.object(windows_accounts.sys, 'platform', 'win32'), \
             patch.object(windows_accounts, '_profile_roots', return_value=[missing]), \
             patch.object(windows_accounts, '_qq_uses_files', side_effect=AssertionError('no files')):
            self.assertEqual(windows_accounts.active_accounts(), [])


class ActiveAccountsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        with patch('qqmanager.server.sys.platform', 'win32'):
            self.server = make_server(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.db.close()
        self.temp.cleanup()

    def get(self, path, headers=None):
        request = urllib.request.Request(self.url + path, headers=headers or {})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_endpoint_requires_csrf_and_returns_live_accounts(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get('/api/qq/active-accounts')
        self.assertEqual(error.exception.code, 403)

        csrf = self.get('/api/session')['csrf']
        with patch('qqmanager.windows_accounts.active_accounts', return_value=['12345', '345678']) as active:
            self.assertEqual(self.get('/api/qq/active-accounts', {'X-CSRF-Token': csrf}),
                             {'accounts': ['12345', '345678']})
        active.assert_called_once_with()

    def test_installer_config_uses_bound_account_and_preserves_pause(self):
        path = '/api/native/install-config'
        with self.assertRaises(urllib.error.HTTPError) as forbidden:
            self.get(path)
        self.assertEqual(forbidden.exception.code, 403)
        headers = {'X-CSRF-Token': self.get('/api/session')['csrf']}
        with self.assertRaises(urllib.error.HTTPError) as unbound:
            self.get(path, headers)
        self.assertEqual(unbound.exception.code, 409)
        self.store.set_setting('ws_account', '123456')
        self.store.set_setting('bridge_groups', ['654321'])
        config = self.get(path, headers)
        self.assertEqual(config, dict(account='123456', groups=['654321'], url=self.url + '/',
                                      token=self.store.setting('native_token')))
        self.assertFalse(self.store.setting('native_enabled'))
        self.assertFalse(self.store.setting('bridge_enabled'))
