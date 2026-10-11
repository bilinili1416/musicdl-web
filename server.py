"""Local web interface for musicdl. Run with .venv/Scripts/python.exe server.py."""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import hmac
import importlib.metadata
import json
import mimetypes
import multiprocessing as mp
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import lyrics
import songtools
import fastsearch
import librarytools
import musiccompat

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("MUSICDL_DATA_DIR", str(ROOT))).resolve()
OUTPUT = DATA_ROOT / "musicdl_outputs"
STATE = DATA_ROOT / ".musicdl-web"
PUBLIC_ORIGIN = (os.environ.get("MUSICDL_PUBLIC_ORIGIN") or
                 ("https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"] if os.environ.get("RAILWAY_PUBLIC_DOMAIN") else "")).rstrip("/")
AUTH_USER = os.environ.get("MUSICDL_USERNAME", "musicdl")
AUTH_PASSWORD = os.environ.get("MUSICDL_PASSWORD", "")
SOURCES = {
    "NeteaseMusicClient": {"name": "网易云音乐", "color": "#e45259", "mark": "云"},
    "QQMusicClient": {"name": "QQ 音乐", "color": "#26ad80", "mark": "Q"},
    "KuwoMusicClient": {"name": "酷我音乐", "color": "#dfa725", "mark": "酷"},
    "KugouMusicClient": {"name": "酷狗音乐", "color": "#428bea", "mark": "K"},
    "MiguMusicClient": {"name": "咪咕音乐", "color": "#dd609a", "mark": "咪"},
    "QianqianMusicClient": {"name": "千千音乐", "color": "#8b67cb", "mark": "千"},
}
AUDIO_EXTS = {"mp3", "flac", "wav", "m4a", "aac", "ogg", "opus", "ape", "wma", "alac"}
LOCK = threading.RLock()
SEARCHES: dict = {}
CACHE: dict = {}
JOBS: dict = {}
IMPORTS: dict = {}
SEARCH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search")
DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="download")
IMPORT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="import-lyrics")


def validate_deployment(host, password, public_origin):
    if host not in {"127.0.0.1", "localhost", "::1"} or public_origin:
        if len(password) < 16:
            raise ValueError("外部访问必须设置至少 16 字符的 MUSICDL_PASSWORD")
    if public_origin:
        origin = urlparse(public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path not in {"", "/"} or origin.query or origin.fragment):
            raise ValueError("MUSICDL_PUBLIC_ORIGIN 必须是完整的 HTTPS 网站域名，不含路径")
        # Accessing port also validates malformed port values.
        origin.port


def clean(value, fallback="—"):
    if value is None or str(value) in {"", "None", "null", "NULL"}:
        return fallback
    if isinstance(value, (list, tuple)):
        return " / ".join(map(str, value))
    return str(value)


def song_view(song, sid):
    if isinstance(song, dict) and song.get('fast'):
        return {key: value for key, value in song.items() if key not in {'raw', 'fast'}} | {'id': sid}
    return {"id": sid, "title": clean(song.song_name, "未知歌曲"),
            "artist": clean(song.singers, "未知歌手"), "album": clean(song.album),
            "source": song.source, "format": clean(song.ext, "未知").upper(),
            "duration": clean(song.duration), "size": clean(song.file_size),
            "sizeBytes": song.file_size_bytes or 0, "bitrate": song.bitrate or 0,
            "sourceId": str(getattr(song, "identifier", "") or "")}


def build_client(source, limit=12):
    from musicdl.musicdl import MusicClient
    client = MusicClient(music_sources=[source], init_music_clients_cfg={source: {
        "work_dir": str(OUTPUT / source), "disable_print": True,
        "search_size_per_source": limit, "search_size_per_page": min(limit, 10), "max_retries": 1,
    }}, requests_overrides={source: {"timeout": (6, 12)}})
    musiccompat.adapt_client(client.music_clients[source])
    return client


def source_process(pipe, source, keyword, limit, mode='full'):
    """Isolate third-party calls so a stalled platform can be stopped safely."""
    try:
        client = build_client(source, limit).music_clients[source]
        if mode == 'fast':
            songs = fastsearch.search(client, keyword, limit)
        else:
            songs = client.search(keyword=keyword, num_threadings=3, request_overrides={"timeout": (6, 12)})
            songs = [s for s in songs if not s.episodes and s.with_valid_download_url][:limit]
        pipe.send((songs, None))
    except Exception as exc:
        pipe.send(([], str(exc)[:300]))
    finally:
        pipe.close()


def isolated_call(target, args, timeout):
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(sender, *args), daemon=True)
    try:
        process.start()
        sender.close()
        if receiver.poll(timeout):
            try:
                return receiver.recv()
            except EOFError:
                return [], "平台进程异常退出，请稍后重试"
        return [], "平台响应超时，请稍后重试"
    finally:
        if process.pid:
            if process.is_alive():
                process.terminate()
            process.join(3)
        receiver.close()
        sender.close()


def search_source(source, keyword, limit, mode='full'):
    return isolated_call(source_process, (source, keyword, limit, mode), 25 if mode == 'fast' else 65)


def resolve_process(pipe, descriptor):
    try:
        client = build_client(descriptor['source'], 1).music_clients[descriptor['source']]
        pipe.send((fastsearch.resolve(client, descriptor), None))
    except Exception as exc:
        pipe.send((None, str(exc)[:300]))
    finally:
        pipe.close()


def resolve_source(descriptor):
    song, error = isolated_call(resolve_process, (descriptor,), 65)
    if error:
        raise ValueError(error)
    return song


def search_task(search_id, keyword, sources, limit, mode='full'):
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(search_source, s, keyword, limit, mode) if mode == 'fast'
                       else pool.submit(search_source, s, keyword, limit): s for s in sources}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    songs, error = future.result()
                except Exception as exc:
                    songs, error = [], str(exc)[:300]
                with LOCK:
                    search = SEARCHES[search_id]
                    for song in songs:
                        sid = uuid.uuid4().hex
                        view = song_view(song, sid)
                        CACHE[sid] = {"song": song, "view": view, "created": time.time()}
                        search["songs"].append(view)
                    if error:
                        search["errors"].append({"source": source, "message": error})
                    search["completed"] += 1
        with LOCK:
            SEARCHES[search_id]["status"] = "completed"
    except Exception as exc:
        with LOCK:
            SEARCHES[search_id].update(status="failed", message=str(exc)[:300])


def persist_jobs():
    STATE.mkdir(parents=True, exist_ok=True)
    with LOCK:
        data = list(JOBS.values())[-500:]
        temporary = STATE / "history.tmp"
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(STATE / "history.json")


def load_jobs():
    try:
        for job in json.loads((STATE / "history.json").read_text(encoding="utf-8")):
            if job["status"] in {"queued", "resolving", "downloading"}:
                job.update(status="failed", error="服务已重启，请重新搜索并下载")
            JOBS[job["id"]] = job
    except (OSError, ValueError, KeyError, TypeError):
        pass


def file_id(path):
    return hashlib.sha256(path.relative_to(OUTPUT).as_posix().encode()).hexdigest()[:32]


def library():
    metadata = librarytools.read_metadata(STATE)
    known = {j.get("fileId"): j for j in [*JOBS.values(), *IMPORTS.values()] if j.get("fileId")}
    pending = {j.get('pendingFileId') for j in JOBS.values() if j.get('status') in {'queued', 'resolving', 'downloading'}}
    entries = []
    for path in OUTPUT.rglob("*"):
        if not path.is_file() or path.suffix.lstrip(".").lower() not in AUDIO_EXTS:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(OUTPUT.resolve()) or path.stat().st_size == 0:
            continue
        fid = file_id(path)
        if fid in pending:
            continue
        job = known.get(fid)
        entries.append({**(job["song"] if job else metadata.get(fid) or {"title": path.stem, "artist": "本地音乐",
                         "album": "—", "source": path.relative_to(OUTPUT).parts[0], "duration": "—",
                         **lyrics.audio_metadata(path)}),
                        "id": fid, "fileId": fid, "format": path.suffix[1:].upper(),
                        "size": f"{path.stat().st_size / 1048576:.1f} MB", "sizeBytes": path.stat().st_size,
                        "modified": path.stat().st_mtime, "fileName": path.name})
    return sorted(entries, key=lambda s: s["modified"], reverse=True)


def persist_imports():
    with LOCK:
        librarytools.save_json(STATE, 'imports.json', list(IMPORTS.values()))


def load_imports():
    try:
        for item in json.loads((STATE / 'imports.json').read_text(encoding='utf-8')):
            if item.get('status') == 'checking':
                item.update(status='completed', lyricStatus='failed', message='服务已重启，歌词补全中断，可在播放器中在线找歌词')
            IMPORTS[item['id']] = item
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass


def complete_import_lyrics(fid, raw, message='已补全歌词'):
    """Resolve pending import warnings under LOCK; callers persist changed records."""
    if not raw['original'].strip():
        return False
    changed = False
    for job in IMPORTS.values():
        if job.get('fileId') == fid and job.get('lyricStatus') in {'checking', 'failed'}:
            job.update(status='completed', lyricStatus='existing', message=message)
            changed = True
    return changed


def import_lyrics_task(iid):
    with LOCK:
        if iid not in IMPORTS:
            return
        job = copy.deepcopy(IMPORTS[iid])
    try:
        file = find_file(job['fileId'])
        if not file:
            raise ValueError('导入的音频文件已移走')
        raw = lyrics.load(file, job['fileId'], STATE)
        if raw['original'].strip():
            status, message = 'existing', '已保留音频自带歌词'
        else:
            choices = lyrics.candidates(job['song'])
            selected = librarytools.exact_lyric_match(job['song'], choices)
            if not selected:
                raise ValueError('未找到唯一匹配的歌词版本，请在播放器中在线找歌词或导入 LRC')
            fetched = lyrics.netease_lyrics(selected['id'])
            if not fetched['original'].strip():
                raise ValueError('匹配版本没有歌词，可手动导入 LRC；纯音乐可以没有歌词')
            with LOCK:
                if iid not in IMPORTS or not find_file(job['fileId']):
                    return
                # A manual import while the network request was running takes precedence.
                latest = lyrics.load(file, job['fileId'], STATE)
                if latest['original'].strip():
                    status, message = 'existing', '已保留现有歌词'
                else:
                    lyrics.save(job['fileId'], STATE, **fetched, source='导入音乐 · 自动补全歌词')
                    status, message = 'filled', '已自动补全歌词' + ('与译文' if fetched['translation'].strip() else '')
        with LOCK:
            if iid in IMPORTS:
                IMPORTS[iid].update(status='completed', lyricStatus=status, message=message)
    except Exception as exc:
        with LOCK:
            if iid in IMPORTS:
                # A network failure must not undo lyrics saved while it was running.
                file = find_file(job['fileId'])
                latest = lyrics.load(file, job['fileId'], STATE) if file else {'original': ''}
                if latest['original'].strip():
                    complete_import_lyrics(job['fileId'], latest, '已保留现有歌词')
                else:
                    IMPORTS[iid].update(status='completed', lyricStatus='failed', message=str(exc)[:300])
    finally:
        persist_imports()


def find_file(fid):
    for path in OUTPUT.rglob("*"):
        if (path.is_file() and path.suffix[1:].lower() in AUDIO_EXTS
                and path.resolve().is_relative_to(OUTPUT.resolve()) and file_id(path) == fid):
            return path
    return None


def deletion_ids(body):
    ids = body.get('ids')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 5000
            or any(not isinstance(fid, str) or not re.fullmatch(r'[a-f0-9]{32}', fid) for fid in ids)):
        raise ValueError('请选择 1–5000 条有效记录')
    return list(dict.fromkeys(ids))


def delete_history(body):
    ids = deletion_ids(body)
    with LOCK:
        if any(JOBS[jid]['status'] not in {'completed', 'failed'} for jid in ids if jid in JOBS):
            raise ValueError('进行中的下载任务不能删除，请等待任务结束')
        # History is not the music library's metadata store. Retain song details
        # when removing completed jobs, including files without embedded tags.
        metadata = librarytools.read_metadata(STATE)
        for jid in ids:
            job = JOBS.get(jid)
            if job and job.get('fileId'):
                metadata[job['fileId']] = job['song']
        librarytools.save_json(STATE, 'library-metadata.json', metadata)
        removed = [jid for jid in ids if jid in JOBS]
        remaining = [job for jid, job in JOBS.items() if jid not in ids]
        librarytools.save_json(STATE, 'history.json', remaining[-500:])
        for jid in removed:
            del JOBS[jid]
    return {'deleted': removed}


def delete_music(body):
    if body.get('confirmed') is not True:
        raise ValueError('请先确认永久删除本地音乐文件')
    ids = deletion_ids(body)
    with LOCK:
        active = {j.get('pendingFileId') or j.get('fileId') for j in JOBS.values()
                  if j.get('status') in {'queued', 'resolving', 'downloading'}}
        if active.intersection(ids):
            raise ValueError('选中的音乐正在下载，请等待下载完成')
        # Read state and verify every absolute path before deleting any file.
        metadata = librarytools.read_metadata(STATE)
        lists = librarytools.read_playlists(STATE)
        root = OUTPUT.resolve()
        files = {file_id(path): path for path in OUTPUT.rglob('*')
                 if path.is_file() and path.suffix[1:].lower() in AUDIO_EXTS
                 and path.resolve().is_relative_to(root)}
        same_stem = {}
        for path in files.values():
            same_stem.setdefault(path.with_suffix(''), []).append(path)
        targets = {}
        for fid in ids:
            audio = files.get(fid)
            if audio:
                candidates = [audio, *(audio.with_suffix(ext) for ext in ('.lrc', '.translation.lrc', '.zh.lrc', '.trans.lrc'))]
                if any(not path.resolve().is_relative_to(root) for path in candidates):
                    raise ValueError('歌曲或歌词路径超出音乐库，已停止删除')
                targets[fid] = candidates
        deleted, missing, errors, warnings = [], [], [], []
        for fid in ids:
            if fid not in targets:
                missing.append(fid)
                continue
            audio, *sidecars = targets[fid]
            try:
                audio.unlink()
            except FileNotFoundError:
                missing.append(fid)
            except OSError:
                errors.append({'id': fid, 'message': '文件无法删除，可能正在使用或没有目录权限'})
                continue
            else:
                deleted.append(fid)
            # Different audio formats may share the same external lyrics.
            if any(path != audio and path.exists() for path in same_stem[audio.with_suffix('')]):
                continue
            for path in sidecars:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    warnings.append({'id': fid, 'message': '音频已删除，但部分歌词文件无法删除'})
        removed = set(deleted + missing)
        for fid in removed:
            metadata.pop(fid, None)
            for suffix in ('.json', '.tmp'):
                cache = STATE / 'lyrics' / (fid + suffix)
                if not cache.resolve().is_relative_to(STATE.resolve()):
                    warnings.append({'id': fid, 'message': '歌词缓存路径异常，未删除缓存'})
                    continue
                try:
                    cache.unlink(missing_ok=True)
                except OSError:
                    warnings.append({'id': fid, 'message': '音频已删除，但歌词缓存无法删除'})
        for jid in [jid for jid, job in JOBS.items() if job.get('fileId') in removed]:
            del JOBS[jid]
        for iid in [iid for iid, job in IMPORTS.items() if job.get('fileId') in removed]:
            del IMPORTS[iid]
        for item in lists:
            item['files'] = [fid for fid in item['files'] if fid not in removed]
        librarytools.save_json(STATE, 'library-metadata.json', metadata)
        librarytools.save_json(STATE, 'playlists.json', lists)
        persist_jobs()
        persist_imports()
    return {'deleted': deleted, 'missing': missing, 'errors': errors, 'warnings': warnings}


def download_task(jid, song):
    with LOCK:
        JOBS[jid]["status"] = "resolving" if isinstance(song, dict) and song.get('fast') else "downloading"
    persist_jobs()
    try:
        if isinstance(song, dict) and song.get('fast'):
            song = resolve_source(song)
            with LOCK:
                JOBS[jid]['song'] = song_view(song, JOBS[jid]['song']['id'])
                JOBS[jid]['status'] = 'downloading'
            persist_jobs()
        ext = clean(song.ext, "").lstrip(".").lower()
        if ext not in AUDIO_EXTS:
            raise ValueError("该音频格式暂不支持")
        directory = OUTPUT / song.source
        directory.mkdir(parents=True, exist_ok=True)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean(song.song_name, "音乐"))[:65].strip(" .") or "音乐"
        artist = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean(song.singers, "未知歌手"))[:35].strip(" .")
        target = directory / f"{name} - {artist} - {jid[:8]}.{ext}"
        with LOCK:
            JOBS[jid]['pendingFileId'] = file_id(target)
        song.work_dir = str(directory)
        song._save_path = str(target)
        client = build_client(song.source).music_clients[song.source]
        downloaded = client.download([song], num_threadings=1, request_overrides={"timeout": (6, 30)})
        if not downloaded:
            raise RuntimeError("下载失败，链接可能已失效或平台暂不可用；请重新搜索后重试")
        # SongInfo.save_path may change if accessed after a file is written; retain the actual target.
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("平台没有返回有效音频文件")
        translated = lyrics.raw_translation(song)
        if translated:
            try:
                target.with_suffix(".translation.lrc").write_text(translated, encoding="utf-8")
            except OSError:
                pass  # A lyric sidecar failure must not invalidate a completed audio download.
        with LOCK:
            JOBS[jid].update(status="completed", fileId=file_id(target), fileName=target.name,
                             sizeBytes=target.stat().st_size, completed=time.time())
    except Exception as exc:
        with LOCK:
            JOBS[jid].update(status="failed", error=str(exc)[:400])
        if "target" in locals():
            target.unlink(missing_ok=True)
    finally:
        persist_jobs()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 20

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        try:
            authority = self.headers.get("Host", "")
            target = urlparse("http://" + authority)
            if target.username or target.password or target.path or target.query or target.fragment:
                return False
            host = target.hostname
            target.port
            origin = self.headers.get("Origin")
            if host in {"127.0.0.1", "localhost", "::1"}:
                return not origin or origin == f"http://{authority}"
            public = urlparse(PUBLIC_ORIGIN)
            return bool(PUBLIC_ORIGIN and target.netloc.lower() == public.netloc.lower()
                        and (not origin or origin == PUBLIC_ORIGIN))
        except ValueError:
            return False

    def check_access(self):
        cross_site = self.headers.get("Sec-Fetch-Site") == "cross-site"
        # Opening a shared URL is a normal top-level navigation; cross-site API
        # calls and form submissions must still be rejected.
        navigation = self.command == "GET" and self.headers.get("Sec-Fetch-Mode") == "navigate"
        if not self.local_request() or (cross_site and not navigation):
            self.close_connection = True
            self.json_response({"error": "不允许该域名或跨站请求"}, 403)
            return False
        if AUTH_PASSWORD:
            try:
                method, token = self.headers.get("Authorization", "").split(" ", 1)
                decoded = base64.b64decode(token, validate=True)
                valid = method.lower() == "basic" and hmac.compare_digest(
                    decoded, f"{AUTH_USER}:{AUTH_PASSWORD}".encode("utf-8"))
            except (ValueError, binascii.Error):
                valid = False
            if not valid:
                body = b'{"error":"Authentication required"}'
                self.close_connection = True
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Musicdl", charset="UTF-8"')
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return False
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            try:
                importlib.metadata.version("musicdl")
                return self.json_response({"ok": True})
            except importlib.metadata.PackageNotFoundError:
                return self.json_response({"ok": False}, 503)
        if not self.check_access():
            return
        if path == "/api/status":
            try:
                version = importlib.metadata.version("musicdl")
            except importlib.metadata.PackageNotFoundError:
                version = None
            return self.json_response({"ready": bool(version), "version": version,
                                      "sources": [{"id": k, **v} for k, v in SOURCES.items()],
                                      "deployment": "cloud" if PUBLIC_ORIGIN else "local",
                                      "output": str(OUTPUT)})
        if path.startswith("/api/search/"):
            with LOCK:
                search = copy.deepcopy(SEARCHES.get(path.rsplit("/", 1)[-1]))
            return self.json_response(search if search else {"error": "搜索已过期"}, 200 if search else 404)
        if path == "/api/downloads":
            with LOCK:
                jobs = copy.deepcopy(list(JOBS.values()))
            return self.json_response({"jobs": sorted(jobs, key=lambda j: j["created"], reverse=True)})
        if path == "/api/library":
            try:
                with LOCK:
                    songs = library()
                return self.json_response({"songs": songs})
            except ValueError as exc:
                return self.json_response({'error': str(exc)}, 400)
        if path == '/api/playlists':
            try:
                with LOCK:
                    lists = librarytools.playlists(STATE, [s['fileId'] for s in library()])
                return self.json_response({'playlists': lists})
            except ValueError as exc:
                return self.json_response({'error': str(exc)}, 400)
        if path == '/api/imports':
            with LOCK:
                changed = False
                # Repair records written before saving lyrics also updated import status.
                for job in IMPORTS.values():
                    if job.get('lyricStatus') != 'failed':
                        continue
                    file = find_file(job['fileId'])
                    if file:
                        changed = complete_import_lyrics(job['fileId'], lyrics.load(file, job['fileId'], STATE)) or changed
                if changed:
                    persist_imports()
                items = copy.deepcopy(list(IMPORTS.values()))
            return self.json_response({'imports': sorted(items, key=lambda item: item['created'], reverse=True)})
        if path.startswith("/api/lyrics/"):
            match = re.fullmatch(r"/api/lyrics/([a-f0-9]{32})(/furigana)?", path)
            if not match:
                return self.json_response({"error": "无效的歌词请求"}, 404)
            fid, readings = match.groups()
            file = find_file(fid)
            if not file:
                return self.json_response({"error": "文件不存在"}, 404)
            raw = lyrics.load(file, fid, STATE)
            data = lyrics.align(**raw)
            if readings:
                try:
                    data = songtools.annotate(data)
                except ImportError:
                    return self.json_response({"error": "请安装 requirements.txt 中的日语注音依赖"}, 503)
            return self.json_response(data)
        if path.startswith("/api/files/"):
            file = find_file(path.rsplit("/", 1)[-1])
            if file:
                return self.serve_file(file, attachment="download" in urlparse(self.path).query)
            return self.json_response({"error": "文件不存在"}, 404)
        assets = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/memory.js": "memory.js", "/queue.js": "queue.js", "/styles.css": "styles.css", "/lyrics.css": "lyrics.css", "/favicon.svg": "favicon.svg"}
        if path in assets:
            return self.serve_file(ROOT / "web" / assets[path])
        return self.json_response({"error": "页面不存在"}, 404)

    def serve_file(self, path, attachment=False):
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        if range_header := self.headers.get("Range"):
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                return self.json_response({"error": "无效字节范围"}, 416)
            a, b = match.groups()
            if a:
                start, end = int(a), min(int(b), size - 1) if b else size - 1
            else:
                start = max(0, size - int(b))
            if start > end or start >= size:
                return self.json_response({"error": "字节范围超出文件"}, 416)
            partial = True
        self.send_response(206 if partial else 200)
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_header("Content-Type", mime + ("; charset=utf-8" if path.suffix in {".html", ".js", ".css", ".svg"} else ""))
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if attachment:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        if not self.check_access():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            path = urlparse(self.path).path
            if path == '/api/imports':
                self.close_connection = True
                return self.import_audio(length)
            is_lyrics = path.startswith("/api/lyrics/")
            if not 0 < length <= (1572864 if is_lyrics else 262144 if path in {'/api/playlists', '/api/downloads/delete', '/api/library/delete'} else 16384):
                self.close_connection = True
                return self.json_response({"error": "请求长度无效"}, 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("无效请求")
            path = urlparse(self.path).path
            if path == '/api/downloads/delete':
                return self.json_response(delete_history(body))
            if path == '/api/library/delete':
                return self.json_response(delete_music(body))
            if path == '/api/playlists':
                with LOCK:
                    available = [s['fileId'] for s in library()]
                    pid = librarytools.edit_playlist(STATE, body, available)
                    lists = librarytools.playlists(STATE, available)
                return self.json_response({'id': pid, 'playlists': lists})
            if path == "/api/moegirl":
                title, artist = body.get("title"), body.get("artist", "")
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or not isinstance(artist, str) or len(artist) > 200 or any(c in title for c in "|\r\n\x00"):
                    raise ValueError("请输入 1–200 字的单首歌曲名称")
                return self.json_response(songtools.moegirl(title.strip(), artist.strip()))
            if is_lyrics:
                match = re.fullmatch(r"/api/lyrics/([a-f0-9]{32})/(import|lookup)", path)
                if not match:
                    raise ValueError("无效的歌词请求")
                fid, action = match.groups()
                file = find_file(fid)
                if not file:
                    return self.json_response({"error": "文件不存在"}, 404)
                raw = lyrics.load(file, fid, STATE)
                initial_raw = dict(raw)
                if action == "import":
                    kind, content = body.get("kind"), body.get("text")
                    if kind not in {"original", "translation"} or not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > lyrics.MAX_TEXT:
                        raise ValueError("请选择不超过 256 KB 的原文或翻译 LRC")
                    lines, plain = lyrics.parse_lrc(content)
                    if not lines and not plain:
                        raise ValueError("文件没有可用的歌词内容")
                    raw[kind] = content
                    raw["source"] = "导入歌词"
                else:
                    meta = next((s for s in library() if s["fileId"] == fid), {})
                    selected = body.get("songId")
                    if selected is None:
                        keyword = body.get("keyword")
                        if keyword is not None and (not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 100):
                            raise ValueError("请输入 1–100 字的歌词搜索关键词")
                        if meta.get("source") == "NeteaseMusicClient" and re.fullmatch(r"\d{1,20}", meta.get("sourceId", "")) and not keyword:
                            selected = meta["sourceId"]
                        else:
                            choices = lyrics.candidates(meta, keyword)
                            matcher = librarytools.exact_lyric_match if meta.get('source') == 'LocalImport' else lyrics.exact_candidate
                            exact = matcher(meta, choices) if not keyword else None
                            if not exact:
                                return self.json_response({"candidates": choices})
                            selected = exact["id"]
                    fetched = lyrics.netease_lyrics(selected)
                    if not fetched["original"].strip():
                        raise ValueError("该版本没有可用歌词，可尝试其他版本或导入 LRC")
                    # Explicit version selection replaces both tracks to keep their timing aligned.
                    raw = {**fetched, "source": "网易云歌词"}
                with LOCK:
                    if not find_file(fid):
                        return self.json_response({'error': '文件已删除，无法保存歌词'}, 404)
                    if action == "lookup" and not body.get("songId"):
                        latest_raw = lyrics.load(file, fid, STATE)
                        if latest_raw != initial_raw:
                            return self.json_response(lyrics.align(**latest_raw))
                    lyrics.save(fid, STATE, **raw)
                    message = '已手动补全歌词' if action == 'import' else '已在线补全歌词'
                    if complete_import_lyrics(fid, raw, message):
                        persist_imports()
                return self.json_response(lyrics.align(**raw))
            if path == "/api/search":
                keyword = body.get("keyword", "")
                sources = body.get("sources", list(SOURCES)[:3])
                limit = body.get("limit", 12)
                mode = body.get("mode", "full")
                if mode not in {'fast', 'full'}:
                    raise ValueError("请选择快速搜索或完整搜索")
                if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 100:
                    raise ValueError("请输入 1–100 字的搜索关键词")
                if not isinstance(sources, list) or not sources or any(s not in SOURCES for s in sources):
                    raise ValueError("请选择有效的音乐平台")
                if not isinstance(limit, int) or not 1 <= limit <= 30:
                    raise ValueError("每个平台的结果数应为 1–30")
                with LOCK:
                    if sum(s["status"] == "searching" for s in SEARCHES.values()) >= 2:
                        return self.json_response({"error": "正在搜索，请等待当前搜索完成"}, 429)
                    now = time.time()
                    for k in [k for k, v in CACHE.items() if now - v["created"] > 7200]:
                        CACHE.pop(k, None)
                    for k in [k for k, v in SEARCHES.items() if now - v["created"] > 7200 and v["status"] != "searching"]:
                        SEARCHES.pop(k, None)
                    sid = uuid.uuid4().hex
                    sources = list(dict.fromkeys(sources))
                    SEARCHES[sid] = {"id": sid, "status": "searching", "keyword": keyword.strip(),
                                     "songs": [], "errors": [], "completed": 0, "total": len(sources), "created": now, "mode": mode}
                SEARCH_POOL.submit(search_task, sid, keyword.strip(), sources, limit, mode)
                return self.json_response({"id": sid}, 202)
            if path == "/api/downloads":
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids or len(ids) > 50 or any(not isinstance(s, str) for s in ids):
                    raise ValueError("请选择 1–50 首歌曲")
                queued, tasks = [], []
                with LOCK:
                    if any(sid not in CACHE for sid in ids):
                        raise ValueError("搜索结果已过期，请重新搜索；收藏中的歌曲也需要重新搜索")
                    if sum(j["status"] in {"queued", "resolving", "downloading"} for j in JOBS.values()) + len(set(ids)) > 100:
                        return self.json_response({"error": "下载队列已满，请稍后重试"}, 429)
                    for sid in dict.fromkeys(ids):
                        existing = next((j for j in JOBS.values() if j.get("song", {}).get("id") == sid and j["status"] in {"queued", "resolving", "downloading"}), None)
                        if existing:
                            queued.append(existing["id"])
                            continue
                        jid = uuid.uuid4().hex
                        JOBS[jid] = {"id": jid, "song": CACHE[sid]["view"], "status": "queued", "created": time.time()}
                        tasks.append((jid, copy.deepcopy(CACHE[sid]["song"])))
                        queued.append(jid)
                persist_jobs()
                for jid, song in tasks:
                    DOWNLOAD_POOL.submit(download_task, jid, song)
                return self.json_response({"ids": queued}, 202)
            return self.json_response({"error": "接口不存在"}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.json_response({"error": str(exc)}, 400)
        except OSError:
            self.json_response({'error': '文件无法保存，请检查磁盘空间和目录权限'}, 500)

    def import_audio(self, length):
        if not 0 < length <= librarytools.MAX_IMPORT:
            return self.json_response({'error': '每首音频应大于 0 且不超过 256 MB'}, 413)
        filename = unquote(self.headers.get('X-Filename', ''))
        if not filename or len(filename) > 240 or any(c in filename for c in '/\\\r\n\x00'):
            raise ValueError('无效的导入文件名')
        ext = Path(filename).suffix.lstrip('.').lower()
        if ext not in AUDIO_EXTS:
            raise ValueError('请选择支持的音频文件，如 MP3、FLAC、WAV、M4A')
        pid = self.headers.get('X-Playlist-Id', 'all')
        with LOCK:
            if pid not in {item['id'] for item in librarytools.playlists(STATE, [])}:
                raise ValueError('目标播放列表不存在，请刷新后重试')
        directory = OUTPUT / 'LocalImport'
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.resolve().is_relative_to(OUTPUT.resolve()):
            raise ValueError('导入目录不在音乐库内')
        iid = uuid.uuid4().hex
        target = directory / f'{iid}.{ext}'
        try:
            with target.open('xb') as stream:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1048576, remaining))
                    if not chunk:
                        raise ValueError('文件上传中断，请重新导入')
                    stream.write(chunk)
                    remaining -= len(chunk)
            duration = int(librarytools.validate_audio(target))
            fid = file_id(target)
            meta = {'title': Path(filename).stem, 'artist': '本地音乐', 'album': '—',
                    'duration': f'{duration//3600:02}:{duration//60%60:02}:{duration%60:02}', **lyrics.audio_metadata(target)}
            song = {**meta, 'id': fid, 'fileId': fid, 'source': 'LocalImport', 'format': ext.upper(),
                    'size': f'{length/1048576:.1f} MB', 'sizeBytes': length, 'fileName': filename}
            with LOCK:
                # Revalidate after the upload: another tab may have deleted the target list.
                if pid != 'all':
                    librarytools.edit_playlist(STATE, {'action': 'add', 'id': pid, 'files': [fid]}, [fid])
                IMPORTS[iid] = {'id': iid, 'fileId': fid, 'song': song, 'filename': filename,
                                'playlistId': pid, 'status': 'checking', 'created': time.time(),
                                'lyricStatus': 'checking', 'message': '正在检查并补全歌词'}
                persist_imports()
        except Exception:
            with LOCK:
                IMPORTS.pop(iid, None)
            target.unlink(missing_ok=True)
            raise
        IMPORT_POOL.submit(import_lyrics_task, iid)
        return self.json_response({'import': copy.deepcopy(IMPORTS[iid])}, 202)


def main():
    musiccompat.configure_stdio()
    parser = argparse.ArgumentParser(description="musicdl 网页工作台")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    args = parser.parse_args()
    try:
        validate_deployment(args.host, AUTH_PASSWORD, PUBLIC_ORIGIN)
    except ValueError as exc:
        parser.error(str(exc))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    load_jobs()
    load_imports()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Musicdl Web: {PUBLIC_ORIGIN or f'http://{args.host}:{args.port}'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Musicdl Web", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    mp.freeze_support()
    main()
