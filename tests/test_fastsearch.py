import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import requests
import fastsearch


FIXTURES = {
    'NeteaseMusicClient': {'id': 1, 'name': '青のすみか', 'ar': [{'name': '歌手'}], 'al': {'name': '专辑'}, 'dt': 196000},
    'QQMusicClient': {'mid': 'qq-mid', 'title': '<em>歌曲</em>', 'singer': [{'name': '歌手'}], 'album': {'title': '专辑'}, 'interval': 196},
    'KuwoMusicClient': {'MUSICRID': 'MUSIC_123', 'SONGNAME': '歌曲', 'ARTIST': '歌手', 'ALBUM': '专辑', 'DURATION': '196'},
    'KugouMusicClient': {'FileHash': 'HASH', 'SongName': '歌曲', 'SingerName': '歌手', 'AlbumName': '专辑', 'Duration': 196},
    'MiguMusicClient': {'contentId': 'migu-id', 'copyrightId': 'copyright', 'name': '歌曲', 'singers': [{'name': '歌手'}], 'album': '专辑', 'duration': 196},
    'QianqianMusicClient': {'TSID': 'track-id', 'title': '歌曲', 'artist': [{'name': '歌手'}], 'albumTitle': '专辑', 'duration': 196},
}


def catalog_response(source, records):
    data = records
    for key in reversed(fastsearch.CATALOG_PATHS[source]):
        data = {key: data}
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(data).encode()
    return response


class FastSearchTests(unittest.TestCase):
    def test_six_catalogs_search_without_resolving_links(self):
        for source, raw in FIXTURES.items():
            with self.subTest(source=source):
                client = SimpleNamespace(source=source, search_size_per_page=10,
                                         _constructsearchurls=Mock(return_value=[{'url': 'https://catalog.example', 'data': b'{}', 'page': 1}]),
                                         post=Mock(return_value=catalog_response(source, [raw, raw])),
                                         _parsewithofficialapiv1=Mock(side_effect=AssertionError('Must not resolve during search')))
                songs = fastsearch.search(client, '歌曲', 12)
                self.assertEqual(len(songs), 1)
                self.assertEqual(songs[0]['artist'], '歌手')
                self.assertEqual(songs[0]['album'], '专辑')
                self.assertEqual(songs[0]['duration'], '3:16')
                self.assertEqual(songs[0]['format'], '待解析')
                self.assertTrue(songs[0]['needsResolve'])
                self.assertNotIn('download_url', songs[0])
                self.assertEqual(client.search_size_per_page, 12)
                client.post.assert_called_once()
                client._parsewithofficialapiv1.assert_not_called()

    def test_get_catalog_and_invalid_or_empty_responses(self):
        source = 'KuwoMusicClient'
        client = SimpleNamespace(source=source, _constructsearchurls=lambda **kw: ['https://catalog.example'],
                                 get=Mock(return_value=catalog_response(source, [])))
        self.assertEqual(fastsearch.search(client, 'test', 5), [])
        client.get.return_value = catalog_response(source, [FIXTURES[source], {'SONGNAME': 'no ID'}])
        self.assertEqual(len(fastsearch.search(client, 'test', 5)), 1)
        client.get.return_value = catalog_response('NeteaseMusicClient', [])
        with self.assertRaisesRegex(ValueError, '返回异常'):
            fastsearch.search(client, 'test', 5)

    def test_resolve_uses_exact_record_and_preserves_fallback(self):
        descriptor = fastsearch.metadata('QQMusicClient', FIXTURES['QQMusicClient'])
        good = SimpleNamespace(with_valid_download_url=True, identifier='qq-mid', episodes=[])
        client = SimpleNamespace(source='QQMusicClient', default_cookies={},
                                 _parsewiththirdpartapis=Mock(return_value=good),
                                 _parsewithofficialapiv1=Mock(side_effect=RuntimeError('official unavailable')))
        self.assertIs(fastsearch.resolve(client, descriptor), good)
        self.assertEqual(client._parsewiththirdpartapis.call_args.kwargs['search_result'], FIXTURES['QQMusicClient'])
        self.assertEqual(descriptor['raw'], FIXTURES['QQMusicClient'])
        good.identifier = 'different-record'
        with self.assertRaisesRegex(ValueError, '标识不匹配'):
            fastsearch.resolve(client, descriptor)
        good.with_valid_download_url = False
        with self.assertRaisesRegex(ValueError, '没有可用下载链接'):
            fastsearch.resolve(client, descriptor)


if __name__ == '__main__':
    unittest.main()
