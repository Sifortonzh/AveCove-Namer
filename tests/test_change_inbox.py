import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from avecove_namer.webapp import App


class ChangeInbox(unittest.TestCase):
    def test_ack_requires_matching_version_and_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'namer-changes.json'
            app = App.__new__(App)
            app._namer_completed_lock = threading.Lock()
            event = {'id': '1:work', 'signature': 'new', 'path': '/115/00剧/01美/Test', 'detectedAt': 1}
            source = json.dumps({'events': {'1:work': event}, 'known': {'1:work': 'new'}, 'updatedAt': 1})
            path.write_text(source)
            with patch('avecove_namer.webapp.LITEPAN_CHANGES_FILE', path):
                self.assertEqual(app.litepan_changes()['pending'], 1)
                self.assertEqual(app.litepan_changes({'items': [{'id': '1:work', 'signature': 'old'}]})['pending'], 1)
                self.assertEqual(app.litepan_changes({'items': [event]})['pending'], 0)
                self.assertEqual(path.read_text(), source)
                event['signature'] = 'next'
                path.write_text(json.dumps({'events': {'1:work': event}, 'known': {}, 'updatedAt': 2}))
                self.assertEqual(app.litepan_changes()['pending'], 1)


if __name__ == '__main__': unittest.main()
