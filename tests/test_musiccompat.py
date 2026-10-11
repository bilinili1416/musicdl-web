import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import fastsearch
import musiccompat
from musicdl.modules.sources.qq import QQMusicClient


RAW = {'mid': 'qq-mid', 'title': 'M♭', 'singer': [{'name': '歌手'}],
       'album': {'title': '专辑'}, 'interval': 196}


def response(data):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(data).encode('utf-8')
    return result


def qq_client():
    client = QQMusicClient.__new__(QQMusicClient)
    client.default_cookies = {}
    client.logger_handle = Mock()
    return musiccompat.adapt_client(client)


class MusicCompatibilityTests(unittest.TestCase):
    def test_real_qq_endpoints_reach_http_with_one_bounded_timeout(self):
        client = qq_client()
        methods = ('vkeys', 'xcvts', 'chksz', 'qqovo', 'nki', 'tang', 'hk0cc',
                   'cy', 'lzmhhh', 'xunhuisi', 'xianyuw', 'yutangxiaowu', 'lxmusic', 'lpz')
        overrides = {'timeout': (6, 12), 'proxies': {'https': 'http://proxy.example'}}
        for method in methods:
            with self.subTest(method=method), \
                    patch('requests.get', side_effect=RuntimeError('fixture')) as get, \
                    patch('requests.post', side_effect=RuntimeError('fixture')) as post, \
                    patch('requests.Session.post', side_effect=RuntimeError('fixture')) as session_post:
                with self.assertRaisesRegex(RuntimeError, 'fixture'):
                    getattr(client, '_parsewith' + method + 'api')(RAW, overrides)
                calls = get.call_args_list + post.call_args_list + session_post.call_args_list
                self.assertEqual(len(calls), 1)
                self.assertIn(calls[0].kwargs['timeout'], (10, 20))
                self.assertEqual(calls[0].kwargs['proxies'], overrides['proxies'])
        self.assertEqual(overrides['timeout'], (6, 12))
        self.assertIn('RuntimeError', client.logger_handle.warning.call_args.args[0])

    def test_mikus_keeps_link_tester_timeout_and_adaptation_is_idempotent(self):
        client = qq_client()
        parser = client._parsewithmikusapi
        self.assertIs(musiccompat.adapt_client(client)._parsewithmikusapi, parser)
        client.audio_link_tester = SimpleNamespace(test=Mock(side_effect=RuntimeError('fixture')))
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            parser(RAW, {'timeout': (6, 12)})
        self.assertEqual(client.audio_link_tester.test.call_args.kwargs['request_overrides']['timeout'], (6, 12))

    def test_actual_qq_resolver_returns_exact_song_in_fast_and_full_search(self):
        client = qq_client()
        client.use_encrypted_endpoint = False
        client.audio_link_tester = SimpleNamespace(test=Mock(return_value={
            'ok': True, 'ext': 'flac', 'file_size_bytes': 10_000_000, 'file_size': '10 MB',
            'download_url': 'https://audio.example/test.flac'}))
        descriptor = fastsearch.metadata(client.source, RAW)
        with patch('requests.get', return_value=response({'data': {'url': 'https://audio.example/test.flac'}})) as get:
            song = fastsearch.resolve(client, descriptor)
            self.assertEqual(song.identifier, RAW['mid'])
            self.assertTrue(song.with_valid_download_url)
            self.assertEqual(get.call_args_list[0].kwargs['timeout'], 10)
            # Full search calls the same adapted dispatcher through the actual _search.
            client.post = Mock(return_value=response({
                'music.search.SearchCgiService.DoSearchForQQMusicMobile': {
                    'data': {'body': {'item_song': [RAW]}}}}))
            client.default_search_headers = client.default_headers = {}
            client.default_search_cookies = {}
            client.enable_search_curl_cffi = client.enable_curl_cffi = False
            client.strict_limit_search_size_per_page = False
            client.disable_print = True
            client._initsession = Mock()  # Keep the mocked transport/link tester across the search decorator.
            songs = []
            with musiccompat.WebProgress() as progress:
                client._search('M♭', {'url': 'https://catalog.example', 'page_no': 1},
                               {'timeout': (6, 12)}, songs, progress)
            self.assertEqual([s.identifier for s in songs], [RAW['mid']])

    def test_stdio_uses_utf8_and_children_inherit_it(self):
        with io.TextIOWrapper(io.BytesIO(), encoding='gbk') as stdout, \
                io.TextIOWrapper(io.BytesIO(), encoding='gbk') as stderr, \
                patch('sys.stdout', stdout), patch('sys.stderr', stderr), patch.dict(os.environ):
            musiccompat.configure_stdio()
            stdout.write('M♭ 🎵')
            stderr.write('M♭ 🎵')
            self.assertEqual(stdout.encoding, 'utf-8')
            self.assertEqual(stderr.errors, 'backslashreplace')
            self.assertEqual(os.environ['PYTHONIOENCODING'], 'utf-8:backslashreplace')


if __name__ == '__main__':
    unittest.main()
