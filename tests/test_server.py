"""Integration checks use synthetic, locally generated audio; no platform downloads."""
import io
import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from unittest.mock import patch

import server
import lyrics
import songtools


def synthetic_song():
    from musicdl.modules import SongInfo
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 2000)
    return SongInfo(song_name="本地测试 <script>", singers="测试歌手", album="测试专辑",
                    source="KuwoMusicClient", ext="wav", downloaded_contents=audio.getvalue(),
                    duration="00:00:01", identifier="test", protocol="HTTP")


class WebIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name) / "outputs"
        cls.output.mkdir()
        cls.patches = [patch.object(server, "OUTPUT", cls.output),
                       patch.object(server, "STATE", Path(cls.temp.name) / "state"),
                       patch.object(server, "JOBS", {}), patch.object(server, "IMPORTS", {}), patch.object(server, "CACHE", {}),
                       patch.object(server, "SEARCHES", {}), patch.object(server, "AUTH_PASSWORD", ""),
                       patch.object(server, "PUBLIC_ORIGIN", "")]
        for p in cls.patches:
            p.start()
        cls.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        for p in reversed(cls.patches):
            p.stop()
        cls.temp.cleanup()

    def request(self, path, body=None, headers=None):
        headers = dict(headers or {})
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def setUp(self):
        server.JOBS.clear()
        server.CACHE.clear()
        server.SEARCHES.clear()

    def test_status_and_static_routes(self):
        code, _, body = self.request("/api/status")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ready"])
        self.assertEqual(len(json.loads(body)["sources"]), 6)
        for path in ["/", "/styles.css", "/lyrics.css", "/app.js", "/memory.js", "/queue.js", "/favicon.svg"]:
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request("/server.py")[0], 404)
        self.assertEqual(self.request("/../server.py")[0], 404)

    def test_input_and_origin_validation(self):
        self.assertEqual(self.request("/api/search", {"keyword": "  "})[0], 400)
        self.assertEqual(self.request("/api/search", {"keyword": "test", "sources": ["InvalidClient"]})[0], 400)
        self.assertEqual(self.request("/api/search", {"keyword": "test", "sources": []})[0], 400)
        self.assertEqual(self.request("/api/search", {"keyword": "test", "limit": 31})[0], 400)
        self.assertEqual(self.request("/api/search", {"keyword": "test", "mode": "invalid"})[0], 400)
        self.assertEqual(self.request("/api/downloads", {"ids": ["expired"]})[0], 400)
        self.assertEqual(self.request("/api/downloads", {"ids": []})[0], 400)
        self.assertEqual(self.request("/api/downloads", {"ids": [["bad"]]})[0], 400)
        self.assertEqual(self.request("/api/status", headers={"Origin": "https://untrusted.example"})[0], 403)
        self.assertEqual(self.request("/api/status", headers={"Host": "untrusted.example"})[0], 403)

    def test_password_protects_pages_api_and_audio(self):
        password = "test-only-password-not-a-real-secret"
        auth = "Basic " + base64.b64encode(f"musicdl:{password}".encode()).decode()
        with patch.object(server, "AUTH_PASSWORD", password), patch.object(server, "AUTH_USER", "musicdl"):
            for path in ["/", "/app.js", "/api/status", "/api/downloads", "/api/library", "/api/files/nonexistent"]:
                code, headers, _ = self.request(path)
                self.assertEqual(code, 401)
                self.assertIn("Basic", headers["WWW-Authenticate"])
            self.assertEqual(self.request("/api/status", headers={"Authorization": "Basic invalid!"})[0], 401)
            self.assertEqual(self.request("/api/status", headers={"Authorization": "Bearer invalid"})[0], 401)
            self.assertEqual(self.request("/api/status", headers={"Authorization": auth})[0], 200)
            self.assertEqual(self.request("/api/downloads", {"ids": []})[0], 401)

    def test_cloud_origin_and_minimal_healthcheck(self):
        password = "test-only-password-not-a-real-secret"
        auth = "Basic " + base64.b64encode(f"musicdl:{password}".encode()).decode()
        with patch.object(server, "AUTH_PASSWORD", password), patch.object(server, "AUTH_USER", "musicdl"), \
             patch.object(server, "PUBLIC_ORIGIN", "https://music.example.test"):
            headers = {"Host": "music.example.test", "Authorization": auth, "Origin": "https://music.example.test"}
            code, _, data = self.request("/api/status", headers=headers)
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(data)["deployment"], "cloud")
            self.assertEqual(self.request("/api/status", headers={**headers, "Origin": "https://attacker.example"})[0], 403)
            self.assertEqual(self.request("/api/status", headers={**headers, "Host": "attacker.example"})[0], 403)
            self.assertEqual(self.request("/api/status", headers={**headers, "Host": "music.example.test@attacker.example"})[0], 403)
            self.assertEqual(self.request("/api/status", headers={**headers, "Sec-Fetch-Site": "cross-site"})[0], 403)
            self.assertEqual(self.request("/", headers={**headers, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})[0], 200)
            self.assertEqual(self.request("/api/downloads", {"ids": []}, headers={**headers, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})[0], 403)
            self.assertEqual(self.request("/api/status", headers={"Host": "["})[0], 403)
            code, _, body = self.request("/healthz", headers={"Host": "healthcheck.railway.app"})
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body), {"ok": True})

    def test_external_listener_requires_strong_password_and_https_origin(self):
        server.validate_deployment("127.0.0.1", "", "")
        server.validate_deployment("0.0.0.0", "a-long-test-password", "https://music.example.test")
        with self.assertRaises(ValueError):
            server.validate_deployment("0.0.0.0", "", "")
        with self.assertRaises(ValueError):
            server.validate_deployment("0.0.0.0", "short", "https://music.example.test")
        for origin in ["http://music.example.test", "https://music.example.test/path", "https://user@music.example.test", "https://music.example.test:bad"]:
            with self.assertRaises(ValueError):
                server.validate_deployment("0.0.0.0", "a-long-test-password", origin)

    def test_search_partial_failure_and_song_cache(self):
        def fake_source(source, keyword, limit):
            return ([synthetic_song()], None) if source == "KuwoMusicClient" else ([], "平台响应超时")
        with patch.object(server, "search_source", fake_source):
            code, _, body = self.request("/api/search", {"keyword": "test", "sources": ["KuwoMusicClient", "QQMusicClient"], "limit": 5})
            self.assertEqual(code, 202)
            sid = json.loads(body)["id"]
            for _ in range(100):
                result = json.loads(self.request(f"/api/search/{sid}")[2])
                if result["status"] != "searching":
                    break
                time.sleep(.02)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["completed"], 2)
            self.assertEqual(len(result["songs"]), 1)
            self.assertEqual(len(result["errors"]), 1)
            self.assertIn(result["songs"][0]["id"], server.CACHE)
            self.assertNotIn("download_url", result["songs"][0])
            self.assertEqual(result["songs"][0]["title"], "本地测试 <script>")

    def test_real_musicdl_download_library_and_audio_range(self):
        song = synthetic_song()
        sid = "synthetic-download"
        server.CACHE[sid] = {"song": song, "view": server.song_view(song, sid), "created": time.time()}
        code, _, body = self.request("/api/downloads", {"ids": [sid, sid]})
        self.assertEqual(code, 202)
        ids = json.loads(body)["ids"]
        self.assertEqual(len(ids), 1)
        job = server.JOBS[ids[0]]
        for _ in range(200):
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(.05)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertTrue((server.STATE / "history.json").exists())
        songs = json.loads(self.request("/api/library")[2])["songs"]
        entry = next(s for s in songs if s["fileId"] == job["fileId"])
        self.assertEqual(entry["title"], song.song_name)
        self.assertGreater(entry["sizeBytes"], 0)
        code, headers, content = self.request(f'/api/files/{job["fileId"]}', headers={"Range": "bytes=0-15"})
        self.assertEqual(code, 206)
        self.assertEqual(len(content), 16)
        self.assertTrue(headers["Content-Range"].startswith("bytes 0-15/"))
        self.assertEqual(content[:4], b"RIFF")
        self.assertEqual(self.request(f'/api/files/{job["fileId"]}', headers={"Range": "bytes=-8"})[0], 206)
        self.assertEqual(self.request(f'/api/files/{job["fileId"]}', headers={"Range": "bytes=999999999-"})[0], 416)
        self.assertIn("filename*=UTF-8", self.request(f'/api/files/{job["fileId"]}?download=1')[1]["Content-Disposition"])
        self.assertEqual(self.request('/api/files/nonexistent')[0], 404)

    def test_fast_search_deferred_download_and_private_record(self):
        raw = {'MUSICRID': 'MUSIC_test', 'SONGNAME': '本地测试', 'ARTIST': '测试歌手'}
        descriptor = server.fastsearch.metadata('KuwoMusicClient', raw)
        calls = []
        def fake_source(source, keyword, limit, mode):
            calls.append(mode)
            return [descriptor], None
        with patch.object(server, 'search_source', fake_source):
            code, _, body = self.request('/api/search', {'keyword': 'test', 'sources': ['KuwoMusicClient'], 'mode': 'fast'})
            self.assertEqual(code, 202)
            sid = json.loads(body)['id']
            for _ in range(100):
                result = json.loads(self.request(f'/api/search/{sid}')[2])
                if result['status'] == 'completed':
                    break
                time.sleep(.02)
        self.assertEqual(calls, ['fast'])
        view = result['songs'][0]
        self.assertTrue(view['needsResolve'])
        self.assertNotIn('raw', view)
        self.assertNotIn('download_url', view)
        song = synthetic_song()
        def resolve(selected):
            self.assertEqual(selected['raw'], raw)
            self.assertEqual(next(iter(server.JOBS.values()))['status'], 'resolving')
            return song
        with patch.object(server, 'resolve_source', side_effect=resolve):
            code, _, body = self.request('/api/downloads', {'ids': [view['id']]})
            self.assertEqual(code, 202)
            job = server.JOBS[json.loads(body)['ids'][0]]
            for _ in range(200):
                if job['status'] in {'completed', 'failed'}:
                    break
                time.sleep(.05)
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertEqual(job['song']['format'], 'WAV')
        self.assertNotIn('needsResolve', job['song'])

    def test_special_titles_download_in_sequence_and_parallel_with_gbk_output(self):
        import rich
        from rich.console import Console
        from rich.segment import Segment

        # Simulate a redirected Windows console, including the stale progress text
        # that used to make even ordinary subsequent song names fail.
        with io.TextIOWrapper(io.BytesIO(), encoding='gbk', errors='strict') as output:
            console = Console(file=output)
            console._buffer.append(Segment('M♭ (Preparing)'))
            before = list(console._buffer)
            with patch('sys.stdout', output), patch.object(rich, '_console', console):
                for parallel in (False, True):
                    tasks = []
                    for index, title in enumerate(('M♭', '桜色ダイアリー', 'normal', '🎵 music')):
                        song = synthetic_song()
                        song.source, song.song_name = 'NeteaseMusicClient', title
                        jid = f'gbk-{parallel}-{index}'
                        server.JOBS[jid] = {'id': jid, 'status': 'queued', 'song': server.song_view(song, jid)}
                        tasks.append((jid, song))
                    if parallel:
                        with server.ThreadPoolExecutor(max_workers=2) as pool:
                            futures = [pool.submit(server.download_task, jid, song) for jid, song in tasks]
                            for future in futures:
                                future.result()
                    else:
                        for jid, song in tasks:
                            server.download_task(jid, song)
                    for jid, song in tasks:
                        job = server.JOBS[jid]
                        self.assertEqual(job['status'], 'completed', job.get('error'))
                        path = server.find_file(job['fileId'])
                        self.assertIsNotNone(path)
                        self.assertEqual(path.read_bytes()[:4], b'RIFF')
                        self.assertIn(song.song_name, job['fileName'])
                self.assertEqual(console._buffer, before)

    def test_failed_fast_resolution_and_restart_recovery(self):
        descriptor = server.fastsearch.metadata('KuwoMusicClient', {'MUSICRID': 'MUSIC_123', 'SONGNAME': '歌曲'})
        jid = 'resolution-failure'
        server.JOBS[jid] = {'id': jid, 'status': 'queued', 'song': server.song_view(descriptor, jid)}
        with patch.object(server, 'resolve_source', side_effect=ValueError('暂不可下载')):
            server.download_task(jid, descriptor)
        self.assertEqual(server.JOBS[jid]['status'], 'failed')
        self.assertNotIn('fileId', server.JOBS[jid])
        server.JOBS[jid]['status'] = 'resolving'
        server.persist_jobs()
        with patch.object(server, 'JOBS', {}):
            server.load_jobs()
            self.assertEqual(server.JOBS[jid]['status'], 'failed')

    def test_download_failure_and_restart_recovery(self):
        from types import SimpleNamespace
        song = synthetic_song()
        jid = "failed-download"
        server.JOBS[jid] = {"id": jid, "status": "queued", "song": server.song_view(song, jid), "created": time.time()}
        fake = SimpleNamespace(music_clients={"KuwoMusicClient": SimpleNamespace(download=lambda *a, **kw: [])})
        with patch.object(server, "build_client", return_value=fake):
            server.download_task(jid, song)
        self.assertEqual(server.JOBS[jid]["status"], "failed")
        self.assertNotIn("fileId", server.JOBS[jid])
        server.JOBS["interrupted"] = {"id": "interrupted", "status": "downloading", "song": {}, "created": time.time()}
        server.persist_jobs()
        with patch.object(server, "JOBS", {}):
            server.load_jobs()
            self.assertEqual(server.JOBS["interrupted"]["status"], "failed")
            self.assertIn("服务已重启", server.JOBS["interrupted"]["error"])

    def test_lyrics_local_import_alignment_and_persistence(self):
        audio = self.output / "lyric-fixture.wav"
        audio.write_bytes(synthetic_song().downloaded_contents)
        fid = server.file_id(audio)
        audio.with_suffix(".lrc").write_text("[00:01.00]Original <script>\n[00:03.50]Second\n[00:05.00]\n", encoding="utf-8")
        code, _, data = self.request(f"/api/lyrics/{fid}")
        self.assertEqual(code, 200)
        parsed = json.loads(data)
        self.assertTrue(parsed["synced"])
        self.assertEqual(parsed["lines"][2]["text"], "")
        self.assertFalse(parsed["hasTranslation"])
        code, _, data = self.request(f"/api/lyrics/{fid}/import", {"kind": "translation", "text": "[00:01.05]译文一\n[00:03.50]译文二\n[00:09.00]不匹配"})
        self.assertEqual(code, 200)
        parsed = json.loads(data)
        self.assertEqual(parsed["lines"][0]["translation"], "译文一")
        self.assertEqual(parsed["unmatchedTranslations"], 1)
        # A fresh disk read, independent of the current search cache and song history.
        raw = lyrics.load(audio, fid, server.STATE)
        self.assertIn("译文二", raw["translation"])
        self.assertEqual(self.request(f"/api/lyrics/{fid}/import", {"kind": "translation", "text": ""})[0], 400)
        self.assertEqual(self.request("/api/lyrics/../import", {"kind": "original", "text": "x"})[0], 400)
        self.assertEqual(self.request("/api/lyrics/nonexistent")[0], 404)
        with patch.object(server, "AUTH_PASSWORD", "test-only-password"):
            self.assertEqual(self.request(f"/api/lyrics/{fid}")[0], 401)
            self.assertEqual(self.request(f"/api/lyrics/{fid}/import", {"kind": "original", "text": "x"})[0], 401)

    def test_lyrics_online_exact_version_and_manual_selection(self):
        audio = self.output / "online-lyric-fixture.wav"
        audio.write_bytes(synthetic_song().downloaded_contents)
        fid = server.file_id(audio)
        server.JOBS["lyrics"] = {"fileId": fid, "song": {"title": "Test", "artist": "Singer", "source": "NeteaseMusicClient"}}
        choices = [{"id": "101", "title": "Test", "artist": "Other"}, {"id": "102", "title": "Test", "artist": "Singer"}]
        fetched = {"original": "[00:01]Original", "translation": "[00:01]译文"}
        with patch.object(lyrics, "candidates", return_value=choices), patch.object(lyrics, "netease_lyrics", return_value=fetched) as fetch:
            code, _, data = self.request(f"/api/lyrics/{fid}/lookup", {})
            self.assertEqual(code, 200)
            fetch.assert_called_once_with("102")
            self.assertTrue(json.loads(data)["hasTranslation"])
            code, _, data = self.request(f"/api/lyrics/{fid}/lookup", {"keyword": "Test"})
            self.assertEqual(json.loads(data)["candidates"], choices)
        self.assertIsNone(lyrics.exact_candidate({"title": "Test", "artist": "Unmatched"}, choices))

    def test_reading_endpoint_preserves_timing_and_uses_local_dictionary(self):
        audio = self.output / "reading-fixture.wav"
        audio.write_bytes(synthetic_song().downloaded_contents)
        audio.with_suffix(".lrc").write_text("[00:01.20]青い空\n[00:04]君の笑顔", encoding="utf-8")
        fid = server.file_id(audio)
        code, _, data = self.request(f"/api/lyrics/{fid}/furigana")
        self.assertEqual(code, 200)
        parsed = json.loads(data)
        self.assertEqual(parsed["lines"][0]["time"], 1.2)
        self.assertIn({"text": "君", "reading": "きみ"}, parsed["lines"][1]["ruby"])
        self.assertEqual(self.request("/api/lyrics/../furigana")[0], 404)
        with patch.object(server, "AUTH_PASSWORD", "test-password"):
            self.assertEqual(self.request(f"/api/lyrics/{fid}/furigana")[0], 401)

    def test_article_lookup_validates_input_and_requires_existing_auth(self):
        for body in ({"title": ""}, {"title": "a|b"}, {"title": []}, {"title": "test", "artist": []}):
            self.assertEqual(self.request("/api/moegirl", body)[0], 400)
        with patch.object(songtools, "moegirl", return_value={"status": "matched", "article": {"title": "苍蓝所栖"}}) as lookup:
            code, _, data = self.request("/api/moegirl", {"title": " 青のすみか ", "artist": "Singer"})
            self.assertEqual(code, 200)
            lookup.assert_called_once_with("青のすみか", "Singer")
            self.assertEqual(json.loads(data)["article"]["title"], "苍蓝所栖")
        with patch.object(server, "AUTH_PASSWORD", "test-password"):
            self.assertEqual(self.request("/api/moegirl", {"title": "青のすみか"})[0], 401)


class LyricParsingTests(unittest.TestCase):
    def test_offset_multistamp_bilingual_and_fraction_precision(self):
        data = lyrics.align("[ar:Artist]\n[offset:-500]\n[00:01.2][00:03.200]Original\n[00:01.20]译文\n[00:04.50]\n[00:07]Last")
        self.assertEqual([l["time"] for l in data["lines"]], [.7, 2.7, 4., 6.5])
        self.assertEqual(data["lines"][0]["translation"], "译文")
        self.assertEqual(data["lines"][2]["text"], "")
        self.assertEqual(data["plain"], "")
        self.assertTrue(data["hasTranslation"])

    def test_plain_lyrics_and_raw_platform_translation(self):
        from types import SimpleNamespace
        self.assertFalse(lyrics.align("[ti:Title]\nPlain lyrics")["synced"])
        self.assertEqual(lyrics.align("Original", "Translation")["translationPlain"], "Translation")
        self.assertEqual(lyrics.raw_translation(SimpleNamespace(raw_data={"lyric": {"tlyric": {"lyric": "[00:01]译文"}}})), "[00:01]译文")

    def test_local_text_encodings_and_embedded_lyrics(self):
        import mutagen
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.lrc"
            for encoding in ("utf-8-sig", "utf-16", "gb18030"):
                path.write_bytes("[00:01]测试歌词".encode(encoding))
                self.assertEqual(lyrics.read_text(path), "[00:01]测试歌词")
            audio = Path(directory) / "fixture.wav"
            audio.write_bytes(synthetic_song().downloaded_contents)
            from mutagen.id3 import TIT2, TPE1, USLT
            song = mutagen.File(audio)
            song.add_tags()
            song.tags.add(TIT2(encoding=3, text="Embedded title"))
            song.tags.add(TPE1(encoding=3, text="Embedded artist"))
            song.tags.add(USLT(encoding=3, lang="eng", desc="Lyrics", text="[00:01]Embedded lyric"))
            song.save()
            self.assertEqual(lyrics.embedded_lyrics(audio), "[00:01]Embedded lyric")
            self.assertEqual(lyrics.audio_metadata(audio)["title"], "Embedded title")


if __name__ == "__main__":
    unittest.main()
