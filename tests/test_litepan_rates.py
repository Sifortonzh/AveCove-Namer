import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from avecove_namer.backends import BackendError, LitePanWebDAVBackend


class LitePanRateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'rates.json'
        env = patch.dict(os.environ, {'AVECOVE_LITEPAN_RENAME_RATES': str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        sleep = patch('avecove_namer.backends.time.sleep')
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def backend(self):
        return LitePanWebDAVBackend('https://lite.example', 'user', 'test',
            {name: '/root' for name in LitePanWebDAVBackend.initial_ms})

    def test_initial_provider_intervals(self):
        backend = self.backend()
        with patch.object(backend, '_request'):
            for provider, interval in backend.initial_ms.items():
                backend.rename(f'/{provider}/old', f'/{provider}/new')
                self.assertEqual(json.loads(self.path.read_text())[provider]['interval_ms'], interval)

    def test_fifty_successes_reduce_interval_by_fifty_ms(self):
        backend = self.backend()
        with patch.object(backend, '_request'):
            for _ in range(50):
                backend.rename('/115/old', '/115/new')
        self.assertEqual(json.loads(self.path.read_text())['115']['interval_ms'], 450)

    def test_limits_and_provider_state_are_shared_across_instances(self):
        first, second = self.backend(), self.backend()
        with patch.object(first, '_request', side_effect=BackendError('LitePan WebDAV HTTP 429: busy')) as request:
            with self.assertRaises(BackendError):
                first.rename('/115/old', '/115/new')
        self.assertEqual(request.call_count, 1)
        with patch.object(second, '_request'):
            second.rename('/115/old', '/115/new')
        self.assertEqual(json.loads(self.path.read_text())['115']['interval_ms'], 1000)
        self.assertTrue(self.sleep.called)

    def test_file_conflict_does_not_change_interval_or_retry(self):
        backend = self.backend()
        with patch.object(backend, '_request', side_effect=BackendError('HTTP 409: exists')) as request:
            with self.assertRaises(BackendError):
                backend.rename('/123/old', '/123/new')
        self.assertEqual(request.call_count, 1)
        self.assertFalse(self.path.exists())

    def test_non_115_providers_have_no_fixed_wait_and_keep_backoff(self):
        backend = self.backend()
        with patch.object(backend, '_request'):
            for provider in ('123','GuangYa','Baidu','Quark'):
                backend.rename(f'/{provider}/old', f'/{provider}/new')
        self.assertFalse(self.sleep.called)
        for provider in ('123','GuangYa','Baidu','Quark'):
            self.assertEqual(json.loads(self.path.read_text())[provider]['interval_ms'], 0)
        with patch.object(backend, '_request', side_effect=BackendError('HTTP 429: busy')):
            with self.assertRaises(BackendError): backend.rename('/123/a', '/123/b')
        self.assertEqual(json.loads(self.path.read_text())['123']['interval_ms'], 1000)
