import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from avecove_namer.directory_index import build_index
from avecove_namer.webapp import App


class DirectoryIndexTests(unittest.TestCase):
    def test_scoped_graph_ignores_files_cycles_and_outside_roots(self):
        state = {'savedAt': 42, 'directories': {
            '1:root': {'items': [{'id':'show','name':'Example (2020)','is_dir':True},
                                  {'id':'bad','name':'../escape','is_dir':True},
                                  {'id':'file','name':'video.mkv','is_dir':False}]},
            '1:show': {'items':[{'id':'root','name':'Season 01','is_dir':True}]},
            '2:outside': {'items':[{'id':'x','name':'Outside','is_dir':True}]}}}
        data = build_index(state,[{'account':1,'parent':'root','path':'/115/00剧/01美','kind':'tv'}])
        self.assertEqual(data['sourceUpdatedAt'],42)
        self.assertEqual(len(data['items']),2)
        self.assertFalse(any('Outside' in row['name'] or 'escape' in row['path'] for row in data['items']))
        self.assertTrue(all(row['provider']=='115' for row in data['items']))

    def test_index_hit_and_miss_never_create_cloud_adapter_or_tmdb(self):
        app = App.__new__(App)
        app.openlist=Mock(side_effect=AssertionError('No cloud calls'))
        app.tmdb=Mock(side_effect=AssertionError('No TMDb calls'))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'index.json'
            path.write_text(json.dumps({'version':1,'generatedAt':42,'items':[
                {'name':'Example (2020)','path':'/115/00影/01外/Example (2020)','kind':'movie','provider':'115'}]}))
            with patch('avecove_namer.directory_index.INDEX_PATH',path):
                hit=app.library_search({'query':'Example','kind':'movie'})
                self.assertTrue(hit['found'])
                self.assertEqual(hit['source'],'directory-index')
                self.assertFalse(app.library_search({'query':'No match','kind':'movie'})['found'])
                self.assertFalse(app.library_search({'query':'Example','kind':'tv'})['found'])
        app.openlist.assert_not_called()
        app.tmdb.assert_not_called()
