"""Deletion checks operate exclusively on temporary synthetic audio and state."""
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import lyrics
import librarytools
import server
import test_library


class BulkDeletionTests(unittest.TestCase):
    setUp = test_library.LibraryTests.setUp
    tearDown = test_library.LibraryTests.tearDown
    request = test_library.LibraryTests.request

    def fixtures(self):
        songs, paths, jobs = [], [], []
        for i in range(3):
            directory = self.output / 'NeteaseMusicClient'
            directory.mkdir(exist_ok=True)
            path = directory / f'fixture-{i}.wav'
            path.write_bytes(test_library.wav_bytes())
            fid, jid = server.file_id(path), f'{i+1:032x}'
            song = {'fileId': fid, 'title': f'歌曲 {i} ♭', 'artist': '测试歌手',
                    'album': '测试专辑', 'source': 'NeteaseMusicClient', 'format': 'WAV',
                    'sourceId': str(100+i), 'duration': '00:00:02'}
            server.JOBS[jid] = {'id': jid, 'status': 'completed', 'fileId': fid,
                                'song': song, 'created': time.time()+i}
            songs.append(song); paths.append(path); jobs.append(jid)
        server.persist_jobs()
        return songs, paths, jobs

    def test_history_only_preserves_audio_sidecars_metadata_and_playlist_after_reload(self):
        songs, paths, jobs = self.fixtures()
        sidecar = paths[0].with_suffix('.lrc'); sidecar.write_text('[00:00]原文', encoding='utf-8')
        fid = songs[0]['fileId']
        lyrics.save(fid, self.state, '[00:00]缓存', '', 'manual')
        pid = librarytools.edit_playlist(self.state, {'action': 'create', 'name': '保留音乐'}, [fid])
        librarytools.edit_playlist(self.state, {'action': 'add', 'id': pid, 'files': [fid]}, [fid])
        failed = 'f'*32
        server.JOBS[failed] = {'id': failed, 'status': 'failed', 'song': songs[0], 'created': time.time()}
        code, result = self.request('/api/downloads/delete', {'ids': [jobs[0], failed, jobs[0]]})
        self.assertEqual(code, 200)
        self.assertEqual(result['deleted'], [jobs[0], failed])
        self.assertTrue(all(path.exists() for path in paths))
        self.assertTrue(sidecar.exists())
        self.assertTrue((self.state/'lyrics'/f'{fid}.json').exists())
        server.JOBS.clear(); server.load_jobs()
        self.assertNotIn(jobs[0], server.JOBS)
        found = next(s for s in self.request('/api/library')[1]['songs'] if s['fileId']==fid)
        for key in ('title', 'artist', 'sourceId', 'album'):
            self.assertEqual(found[key], songs[0][key])
        self.assertEqual(librarytools.read_playlists(self.state)[0]['files'], [fid])

    def test_music_delete_requires_final_confirmation_and_valid_identifiers(self):
        songs, paths, jobs = self.fixtures()
        for body in ({'ids': [songs[0]['fileId']]}, {'ids': [songs[0]['fileId']], 'confirmed': 'true'},
                     {'ids': ['../fixture.wav'], 'confirmed': True}, {'ids': [], 'confirmed': True}):
            self.assertEqual(self.request('/api/library/delete', body)[0], 400)
        self.assertTrue(all(path.exists() for path in paths))
        self.assertEqual(len(server.JOBS), 3)

    def test_batch_music_delete_cleans_all_references_and_only_selected_files(self):
        songs, paths, jobs = self.fixtures()
        ids = [song['fileId'] for song in songs]
        for path, fid in zip(paths, ids):
            for suffix in ('.lrc', '.translation.lrc', '.zh.lrc', '.trans.lrc'):
                path.with_suffix(suffix).write_text('[00:00]歌词', encoding='utf-8')
            lyrics.save(fid, self.state, '[00:00]原文', '', 'manual')
        neighbor = paths[0].parent/'cover.jpg'; neighbor.write_bytes(b'unrelated cover')
        server.IMPORTS['import'] = {'id': 'import', 'fileId': ids[0], 'song': songs[0], 'created': time.time()}
        pid = librarytools.edit_playlist(self.state, {'action': 'create', 'name': '自选'}, ids)
        librarytools.edit_playlist(self.state, {'action': 'add', 'id': pid, 'files': ids}, ids)
        librarytools.save_json(self.state, 'library-metadata.json', dict(zip(ids, songs)))
        code, result = self.request('/api/library/delete', {'ids': ids[:2]+ids[:1], 'confirmed': True})
        self.assertEqual(code, 200)
        self.assertEqual(result['deleted'], ids[:2]); self.assertEqual(result['errors'], [])
        self.assertFalse(paths[0].exists()); self.assertFalse(paths[1].exists()); self.assertTrue(paths[2].exists())
        self.assertTrue(neighbor.exists())
        for path, fid in zip(paths[:2], ids[:2]):
            self.assertFalse(path.with_suffix('.lrc').exists())
            self.assertFalse(path.with_suffix('.translation.lrc').exists())
            self.assertFalse((self.state/'lyrics'/f'{fid}.json').exists())
        self.assertEqual(list(server.JOBS), jobs[2:]); self.assertEqual(server.IMPORTS, {})
        self.assertEqual(librarytools.read_playlists(self.state)[0]['files'], ids[2:])
        self.assertEqual(list(librarytools.read_metadata(self.state)), ids[2:])
        server.JOBS.clear(); server.load_jobs(); server.load_imports()
        self.assertEqual(list(server.JOBS), jobs[2:]); self.assertEqual(server.IMPORTS, {})
        self.assertEqual([s['fileId'] for s in self.request('/api/library')[1]['songs']], ids[2:])
        self.assertEqual(self.request('/api/lyrics/'+ids[0])[0], 404)
        self.assertEqual(self.request('/api/library/delete', {'ids': ids[:2], 'confirmed': True})[1]['missing'], ids[:2])

    def test_active_downloads_cannot_be_deleted_and_are_hidden_from_library(self):
        songs, paths, jobs = self.fixtures()
        server.JOBS[jobs[1]].update(status='downloading', pendingFileId=songs[1]['fileId'])
        self.assertEqual(self.request('/api/downloads/delete', {'ids': jobs[:2]})[0], 400)
        self.assertEqual(self.request('/api/library/delete', {'ids': [s['fileId'] for s in songs[:2]], 'confirmed': True})[0], 400)
        self.assertTrue(all(path.exists() for path in paths))
        self.assertEqual(len(server.JOBS), 3)
        self.assertNotIn(songs[1]['fileId'], [s['fileId'] for s in server.library()])

    def test_locked_file_keeps_its_records_and_other_selected_file_is_deleted(self):
        songs, paths, jobs = self.fixtures()
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == paths[1]: raise PermissionError('in use')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink):
            code, result = self.request('/api/library/delete', {'ids': [s['fileId'] for s in songs[:2]], 'confirmed': True})
        self.assertEqual(code, 200); self.assertEqual(result['deleted'], [songs[0]['fileId']])
        self.assertEqual(result['errors'][0]['id'], songs[1]['fileId'])
        self.assertTrue(paths[1].exists()); self.assertIn(jobs[1], server.JOBS)

    def test_shared_sidecar_is_retained_until_last_audio_format_is_deleted(self):
        songs, paths, jobs = self.fixtures()
        alternate = paths[0].with_suffix('.flac')
        alternate.write_bytes(b'temporary second audio format')
        sidecar = paths[0].with_suffix('.lrc')
        sidecar.write_text('[00:00]共享歌词', encoding='utf-8')
        result = self.request('/api/library/delete', {'ids': [songs[0]['fileId']], 'confirmed': True})
        self.assertEqual(result[0], 200)
        self.assertTrue(alternate.exists()); self.assertTrue(sidecar.exists())
        result = self.request('/api/library/delete', {'ids': [server.file_id(alternate)], 'confirmed': True})
        self.assertEqual(result[0], 200)
        self.assertFalse(alternate.exists()); self.assertFalse(sidecar.exists())

    def test_preflight_blocks_unsafe_sidecar_and_corrupt_playlists_before_any_deletion(self):
        songs, paths, jobs = self.fixtures()
        outside = Path(self.temp.name)/'outside.lrc'; outside.write_text('keep', encoding='utf-8')
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == paths[1].with_suffix('.translation.lrc'): return outside
            return original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve):
            self.assertEqual(self.request('/api/library/delete', {'ids': [s['fileId'] for s in songs[:2]], 'confirmed': True})[0], 400)
        self.assertTrue(all(path.exists() for path in paths)); self.assertTrue(outside.exists())
        librarytools.save_json(self.state, 'playlists.json', [])
        (self.state/'playlists.json').write_text('corrupt')
        self.assertEqual(self.request('/api/library/delete', {'ids': [songs[0]['fileId']], 'confirmed': True})[0], 400)
        self.assertTrue(paths[0].exists())

    def test_background_lyric_lookup_cannot_recreate_deleted_music_state(self):
        songs, paths, jobs = self.fixtures()
        fid = songs[0]['fileId']
        server.IMPORTS['background'] = {'id': 'background', 'fileId': fid, 'song': songs[0], 'status': 'checking'}
        def fetch(*args):
            server.delete_music({'ids': [fid], 'confirmed': True})
            return {'original': '[00:00]歌词', 'translation': ''}
        with patch.object(lyrics, 'candidates', return_value=[{'id': '1'}]), \
                patch.object(librarytools, 'exact_lyric_match', return_value={'id': '1'}), \
                patch.object(lyrics, 'netease_lyrics', side_effect=fetch):
            server.import_lyrics_task('background')
        self.assertFalse(paths[0].exists()); self.assertNotIn('background', server.IMPORTS)
        self.assertFalse((self.state/'lyrics'/f'{fid}.json').exists())

    def test_deletion_endpoints_keep_existing_auth_and_origin_checks(self):
        songs, paths, jobs = self.fixtures()
        for endpoint, body in [('/api/downloads/delete', {'ids': [jobs[0]]}),
                               ('/api/library/delete', {'ids': [songs[0]['fileId']], 'confirmed': True})]:
            with patch.object(server, 'AUTH_PASSWORD', 'test-only-password'):
                self.assertEqual(self.request(endpoint, body)[0], 401)
            self.assertEqual(self.request(endpoint, body, {'Origin': 'https://evil.example'})[0], 403)
        self.assertTrue(all(path.exists() for path in paths))


if __name__ == '__main__':
    unittest.main()
