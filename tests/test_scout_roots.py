import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from avecove_namer.backends import LitePanWebDAVBackend, BackendError


class ScoutRoots(unittest.TestCase):
    def test_per_category_inbox(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'roots.json'
            config.write_text(json.dumps({'GuangYa':{'00剧':'/Root/00剧/Media Scout'}}))
            backend = LitePanWebDAVBackend('http://lite','u','p',{'GuangYa':'/Root'})
            with patch.dict(os.environ, {'AVECOVE_SCOUT_ROOTS_FILE':str(config)}):
                with self.assertRaisesRegex(BackendError, '接入已移除'):
                    backend._physical_parts('/GuangYa/@scout/00剧/Show')
                self.assertEqual(backend._physical_parts('/GuangYa/00剧/01中/Show'), ['Root','00剧','01中','Show'])
                with self.assertRaises(BackendError):backend._physical_parts('/GuangYa/@scout/00影')

    def test_scoped_alias_preserves_existing_library(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'roots.json'; config.write_text(json.dumps({'115':'/Mediary Scout'}))
            backend = LitePanWebDAVBackend('http://lite','u','p', {'115':'/长夜留灯'})
            with patch.dict(os.environ, {'AVECOVE_SCOUT_ROOTS_FILE':str(config)}):
                with self.assertRaisesRegex(BackendError, '接入已移除'):
                    backend._url('/115/@scout/TV/Show')
                self.assertNotIn('Mediary', backend._url('/115/00剧/01美/Show'))
                with self.assertRaises(BackendError): backend._url('/115/@scout/../secret')
                with self.assertRaises(BackendError): backend._url('/123/@scout/TV')
