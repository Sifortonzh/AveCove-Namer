import unittest
from unittest.mock import Mock
from avecove_namer.tmdb import TMDBClient, series_summary
from avecove_namer.webapp import App


class SeriesSummaryTests(unittest.TestCase):
    def test_counts_specials_genres_and_region_specific_ratings(self):
        result = series_summary({'number_of_seasons':2, 'number_of_episodes':16,
            'type':'Scripted', 'genres':[{'name':'剧情'}, {'name':'悬疑'}, {'name':'剧情'}],
            'origin_country':['KR'], 'content_ratings':{'results':[
                {'iso_3166_1':'US','rating':'TV-MA'}, {'iso_3166_1':'KR','rating':'19'},
                {'iso_3166_1':'US','rating':'TV-MA'}, {'iso_3166_1':'CN','rating':''}]},
            'seasons':[{'season_number':2,'episode_count':6}, {'season_number':0,'episode_count':4},
                       {'season_number':1,'episode_count':10}]})
        self.assertEqual(result['season_count'], 2)
        self.assertEqual(result['episode_count'], 16)
        self.assertEqual([s['number'] for s in result['seasons']], [0,1,2])
        self.assertTrue(result['has_specials'])
        self.assertEqual(result['genres'], ['剧情','悬疑'])
        self.assertEqual(result['content_ratings'], [{'country':'KR','rating':'19'},{'country':'US','rating':'TV-MA'}])

    def test_missing_counts_are_unknown_not_zero_and_specials_are_separate(self):
        result = series_summary({'seasons':[{'season_number':0,'episode_count':7}, {'season_number':1,'episode_count':None}]})
        self.assertEqual(result['season_count'], 1)
        self.assertIsNone(result['episode_count'])
        self.assertIsNone(result['seasons'][1]['episode_count'])
        self.assertEqual(result['content_ratings'], [])
        self.assertIsNone(series_summary({})['season_count'])
        self.assertIsNone(series_summary({})['episode_count'])
        result = series_summary({'seasons':[{'season_number':0,'episode_count':7}, {'season_number':1,'episode_count':8}]})
        self.assertEqual(result['episode_count'], 8)

    def test_details_appends_ratings_in_one_request_without_changing_movies(self):
        client = TMDBClient('test')
        client._get = Mock(return_value={})
        client.details(1399, 'tv', 'zh-CN')
        client._get.assert_called_once_with('/tv/1399', {'language':'zh-CN','append_to_response':'content_ratings'})
        client._get.reset_mock(); client.details(843, 'movie', 'zh-CN')
        client._get.assert_called_once_with('/movie/843', {'language':'zh-CN'})

    def test_lightweight_summary_never_fetches_images_or_cloud(self):
        app = App.__new__(App)
        client = Mock(); app.tmdb = lambda: client
        client.details.return_value = {'number_of_seasons':8, 'number_of_episodes':73}
        result = app.series_summary({'tmdb_id':'1399'})
        self.assertEqual(result['season_count'], 8)
        client.details.assert_called_once_with(1399, 'tv', 'zh-CN')
        client._get.assert_not_called()
        for ident in ('../1399', '-1', '', '0'):
            with self.assertRaises(ValueError): app.series_summary({'tmdb_id':ident})
