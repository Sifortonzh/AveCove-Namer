import threading
import unittest
from unittest.mock import Mock, patch

from avecove_namer.tmdb import TMDBClient, TMDBError, ranked_images
from avecove_namer.webapp import App


def image(name, rating, votes, width=1000, **extra):
    return dict(file_path=f"/{name}.jpg", vote_average=rating, vote_count=votes, width=width, height=600, **extra)


class ArtworkTests(unittest.TestCase):
    def test_rank_top_five_deduplicates_and_keeps_unrated_last(self):
        data = [image('unrated', 10, 0), image('a', 8, 2), image('b', 8, 20),
                image('c', 9, 1), image('d', 7, 20), image('e', 6, 20), image('f', 5, 20),
                image('b', 8, 20), dict(file_path='https://example.com/x.jpg')]
        result = ranked_images(data)
        self.assertEqual([item['file_path'] for item in result], ['/c.jpg', '/b.jpg', '/a.jpg', '/d.jpg', '/e.jpg'])
        result = ranked_images([image('small', 10, 0, 600), image('big', 0, 0, 2000)])
        self.assertEqual(result[0]['file_path'], '/big.jpg')
        self.assertIsNone(result[0]['vote_average'])

    def make_client(self, fail_episode=False):
        client = TMDBClient('test-token')
        client.details = Mock(return_value={'name':'译名', 'original_name':'원제', 'original_language':'ko',
            'first_air_date':'2020-01-01', 'seasons':[{'season_number':0}, {'season_number':1, 'episode_count':2}]})
        calls = []
        def get(path, params):
            calls.append((path, params))
            if path == '/tv/42/images':
                return {'posters':[image('series', 8, 3)], 'backdrops':[image('background', 7, 2)]}
            if path == '/tv/42/season/1':
                return {'name':'시즌 1', 'episodes':[{'episode_number':1,'name':'첫째','overview':'원문'}, {'episode_number':2,'name':'둘째'}]}
            if path == '/tv/42/season/1/images':
                return {'posters':[image('season1', 8, 3)]}
            if '/episode/' in path:
                episode = int(path.split('/episode/')[1].split('/')[0])
                if episode == 2 and fail_episode:
                    raise TMDBError('timeout')
                return {'stills':[image(f'episode{episode}', 7 + episode, 2)]}
            self.fail(f'Unexpected endpoint: {path}')
        client._get = get
        return client, calls

    def test_series_images_are_multilingual_and_do_not_fetch_every_season(self):
        client, calls = self.make_client()
        result = client.series_artwork(42)
        self.assertEqual(calls, [('/tv/42/images', {})])
        self.assertIsNone(result['season'])
        self.assertEqual(result['posters'][0]['file_path'], '/series.jpg')
        self.assertEqual(result['season_count'], 1)
        self.assertTrue(result['has_specials'])
        self.assertEqual(result['episode_count'], 2)

    def test_selected_season_combines_source_and_true_episode_images(self):
        client, calls = self.make_client()
        progress = []
        result = client.series_artwork(42, 1, lambda count, total: progress.append((count,total)))
        self.assertEqual(result['episodes'][0]['name'], '첫째')
        self.assertEqual(result['posters'][0]['file_path'], '/season1.jpg')
        self.assertEqual(result['stills'][0]['episode'], 2)
        self.assertIn(('/tv/42/season/1', {'language':'ko'}), calls)
        self.assertEqual(progress[-1], (2,2))
        self.assertFalse(any('/season/0' in path for path, _ in calls))

    def test_partial_episode_errors_are_not_misrepresented_as_global_top_five(self):
        client, _ = self.make_client(fail_episode=True)
        result = client.series_artwork(42, 1)
        self.assertEqual(len(result['stills']), 1)
        self.assertIn('E02', result['warnings'][0])
        with self.assertRaises(ValueError):
            client.series_artwork(42, 99)

    def make_app(self):
        app = App.__new__(App)
        app._artwork_lock = threading.Lock()
        app._artwork_slots = threading.BoundedSemaphore(2)
        app._artwork_cache = {}
        app._artwork_inflight = {}
        app._jobs_lock = threading.Lock()
        app._jobs = {}
        client, _ = self.make_client()
        app.tmdb = lambda: client
        return app

    def test_jobs_deduplicate_and_cache_series_and_seasons_separately(self):
        app = self.make_app()
        with patch('avecove_namer.webapp.threading.Thread') as thread:
            first = app.start_artwork({'tmdb_id':42})
            self.assertEqual(app.start_artwork({'tmdb_id':42}), first)
            self.assertEqual(thread.call_count, 1)
        app._run_artwork(first['job_id'], (42,None))
        cached = app.start_artwork({'tmdb_id':42})
        self.assertTrue(cached['cached'])
        self.assertIn('원제', cached['data']['recommended_name'])
        with patch('avecove_namer.webapp.threading.Thread'):
            season = app.start_artwork({'tmdb_id':42,'season':1})
        self.assertNotEqual(first['job_id'], season['job_id'])
        for payload in [{'tmdb_id':0}, {'tmdb_id':'../42'}, {'tmdb_id':42,'season':-1}]:
            with self.assertRaises(ValueError):
                app.start_artwork(payload)

    def test_download_rejects_arbitrary_urls_traversal_and_headers(self):
        app = self.make_app()
        with patch('urllib.request.urlopen') as opener:
            for path in ['https://example.com/image.jpg', '/../x.jpg', '/x.jpg\r\nHost:x', '/x.svg', '/dir/x.jpg']:
                with self.assertRaises(ValueError):
                    app.tmdb_image(path)
            opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
