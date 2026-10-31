import tempfile
import unittest
from pathlib import Path
from avecove_namer.shelf import SeriesShelf


class ShelfTests(unittest.TestCase):
    def test_watched_persists_date_progress_and_rewatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shelf.json'
            shelf = SeriesShelf(path)
            shelf.request({'id':'1399','action':'watching','enabled':True})
            shelf.request({'id':'1399','action':'progress','season':2,'episode':5})
            row = shelf.request({'id':'1399','action':'watched','enabled':True,'watchedDate':'2025-01-02'})['items'][0]
            self.assertFalse(row['watching'])
            self.assertTrue(row['watched'])
            self.assertEqual(row['episode'], 5)
            self.assertEqual(SeriesShelf(path).request()['timeline'][0]['watchedDate'], '2025-01-02')
            shelf.request({'id':'1399','action':'watching','enabled':True})
            row = shelf.request({'id':'1399','action':'watched','enabled':True,'watchedDate':'2025-02-03'})['items'][0]
            self.assertTrue(row['watching'])
            self.assertEqual(row['watchedDate'], '2025-02-03')
            row = shelf.request({'id':'1399','action':'watched','enabled':False})['items'][0]
            self.assertNotIn('watchedDate', row)
            self.assertTrue(row['watching'])

    def test_timeline_uses_completion_date_not_favorite_update(self):
        with tempfile.TemporaryDirectory() as directory:
            shelf = SeriesShelf(Path(directory) / 'shelf.json')
            shelf.request({'id':'1','action':'watched','enabled':True,'watchedDate':'2025-01-01'})
            shelf.request({'id':'2','action':'watched','enabled':True,'watchedDate':'2025-02-01'})
            data = shelf.request({'id':'1','action':'favorite','enabled':True})
            self.assertEqual([row['id'] for row in data['timeline']], ['2','1'])
            self.assertEqual(shelf.request({'id':'2','action':'watched','enabled':False})['timeline'][0]['id'], '1')

    def test_invalid_watched_date_cannot_change_saved_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shelf.json'
            shelf = SeriesShelf(path)
            shelf.request({'id':'1','action':'watching','enabled':True})
            original = path.read_text()
            for day in ('2025-02-30', '2999-01-01', '1899-01-01', '2025-1-01', '', 12):
                with self.assertRaises(ValueError):
                    shelf.request({'id':'1','action':'watched','enabled':True,'watchedDate':day})
                self.assertEqual(path.read_text(), original)

    def test_artwork_keeps_flags_progress_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            shelf = SeriesShelf(Path(directory) / 'shelf.json')
            shelf.request({'id':'1421','action':'watching','enabled':True,'title':'Modern Family'})
            shelf.enriching = True
            def loader(ident):
                self.assertEqual(ident, 1421)
                shelf.request({'id':'1421','action':'progress','season':2,'episode':5})
                return {'posterPath':'/poster.jpg','backdropPath':'/backdrop.jpg','year':'2009'}
            shelf.metadata_loader = loader
            shelf.enrich()
            row = shelf.request()['items'][0]
            self.assertTrue(row['watching'])
            self.assertEqual(row['episode'],5)
            self.assertEqual(row['posterPath'],'/poster.jpg')
            self.assertFalse(shelf.enriching)

    def test_removed_item_is_not_resurrected_by_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            shelf = SeriesShelf(Path(directory) / 'shelf.json')
            shelf.request({'id':'1421','action':'favorite','enabled':True})
            shelf.enriching = True
            def loader(ident):
                shelf.request({'id':'1421','action':'favorite','enabled':False})
                return {'posterPath':'/poster.jpg'}
            shelf.metadata_loader = loader
            shelf.enrich()
            self.assertEqual(shelf.request()['items'],[])

    def test_failed_metadata_preserves_collection_and_backs_off(self):
        with tempfile.TemporaryDirectory() as directory:
            shelf = SeriesShelf(Path(directory) / 'shelf.json')
            shelf.request({'id':'1421','action':'watching','enabled':True})
            shelf.enriching = True
            def loader(ident): raise RuntimeError('offline')
            shelf.metadata_loader = loader; shelf.enrich()
            data = shelf.request()
            self.assertTrue(data['items'][0]['watching'])
            self.assertFalse(data['artwork_pending'])
            self.assertIn('artworkRetryAt', data['items'][0])

    def test_persistent_flags_and_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shelf.json'
            shelf = SeriesShelf(path)
            row = shelf.request({'id': '1399', 'action': 'favorite', 'enabled': True, 'title': 'Game of Thrones'})['items'][0]
            self.assertTrue(row['favorite'])
            shelf.request({'id': '1399', 'action': 'watching', 'enabled': True})
            shelf.request({'id': '1399', 'action': 'progress', 'season': 0, 'episode': 11})
            row = SeriesShelf(path).request()['items'][0]
            self.assertEqual(row['episode'], 11)
            self.assertTrue(row['watching'])
            shelf.request({'id': '1399', 'action': 'favorite', 'enabled': False})
            self.assertEqual(len(shelf.request()['items']), 1)
            self.assertEqual(shelf.request({'id': '1399', 'action': 'watching', 'enabled': False})['items'], [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_input_and_unsaved_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            shelf = SeriesShelf(Path(directory) / 'shelf.json')
            self.assertEqual(shelf.request({'id': '1399', 'action': 'progress', 'season': 1, 'episode': 1})['items'], [])
            for data in [{'id':'../2','action':'favorite','enabled':True}, {'id':'2','action':'favorite','enabled':'true'}, {'id':'2','action':'progress','season':1,'episode':0}]:
                with self.assertRaises(ValueError): shelf.request(data)
