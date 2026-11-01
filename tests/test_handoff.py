import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from io import StringIO
from avecove_namer.handoff import HandoffInbox
from avecove_namer.cli import build_parser, run


class HandoffTests(unittest.TestCase):
    def test_durable_deduplication_stale_ack_and_no_cloud_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inbox.sqlite3'
            inbox = HandoffInbox(path)
            event = {'path': '/123/00影/01国/Test', 'source': 'hermes', 'event_id': 'file-v1', 'ready': True}
            self.assertTrue(inbox.receive(event)['accepted'])
            first = inbox.request()['items'][0]
            self.assertEqual(first['kind'], 'movie')
            self.assertTrue(inbox.receive(event)['duplicate'])
            self.assertEqual(HandoffInbox(path).request()['pending'], 1)
            inbox.receive({**event, 'event_id': 'file-v2'})
            self.assertEqual(inbox.request({'items': [first]})['pending'], 1)
            current = inbox.request()['items'][0]
            self.assertEqual(inbox.request({'items': [current]})['pending'], 0)
            self.assertTrue(inbox.receive({**event, 'event_id': 'file-v2'})['duplicate'])
            self.assertEqual(inbox.request()['pending'], 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_reject_broad_in_progress_and_invalid_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = HandoffInbox(Path(directory) / 'inbox.sqlite3')
            for event in ({}, {'path': '/115/00剧/01美'}, {'path': '/115/00剧/01美/../Test'},
                          {'path': '/115/00剧/01美/Test', 'ready': False},
                          {'path': '/115/00剧/01美/Test', 'ready': True, 'kind': 'shell'}):
                with self.assertRaises(ValueError):
                    inbox.receive(event)
            self.assertEqual(inbox.request()['pending'], 0)

    def test_cli_stdin_handoff_without_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(['notify', '--inbox', str(Path(directory) / 'inbox.sqlite3')])
            with patch('sys.stdin', StringIO(json.dumps({'path':'/115/00剧/01美/Test', 'ready':True}))), patch('sys.stdout', new_callable=StringIO) as output:
                self.assertEqual(run(args), 0)
                self.assertTrue(json.loads(output.getvalue())['accepted'])
