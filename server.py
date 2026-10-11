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
from urllib.parse import quote, urlparse

import lyrics
import songtools

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
SEARCH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search")
DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="download")


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
    return {"id": sid, "title": clean(song.song_name, "未知歌曲"),
            "artist": clean(song.singers, "未知歌手"), "album": clean(song.album),
            "source": song.source, "format": clean(song.ext, "未知").upper(),
            "duration": clean(song.duration), "size": clean(song.file_size),
            "sizeBytes": song.file_size_bytes or 0, "bitrate": song.bitrate or 0,
            "sourceId": str(getattr(song, "identifier", "") or "")}


def build_client(source, limit=12):
    from musicdl.musicdl import MusicClient
    return MusicClient(music_sources=[source], init_music_clients_cfg={source: {
        "work_dir": str(OUTPUT / source), "disable_print": True,
        "search_size_per_source": limit, "search_size_per_page": min(limit, 10), "max_retries": 1,
    }}, requests_overrides={source: {"timeout": (6, 12)}})


def source_process(pipe, source, keyword, limit):
    """Isolate third-party calls so a stalled platform can be stopped safely."""
    try:
        client = build_client(source, limit).music_clients[source]
        songs = client.search(keyword=keyword, num_threadings=3, request_overrides={"timeout": (6, 12)})
        songs = [s for s in songs if not s.episodes and s.with_valid_download_url][:limit]
        pipe.send((songs, None))
    except Exception as exc:
        pipe.send(([], str(exc)[:300]))
    finally:
        pipe.close()


def search_source(source, keyword, limit):
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=source_process, args=(sender, source, keyword, limit), daemon=True)
    try:
        process.start()
        sender.close()
        if receiver.poll(65):
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


def search_task(search_id, keyword, sources, limit):
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(search_source, s, keyword, limit): s for s in sources}
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
            if job["status"] in {"queued", "downloading"}:
                job.update(status="failed", error="服务已重启，请重新搜索并下载")
            JOBS[job["id"]] = job
    except (OSError, ValueError, KeyError, TypeError):
        pass


def file_id(path):
    return hashlib.sha256(path.relative_to(OUTPUT).as_posix().encode()).hexdigest()[:32]


def library():
    known = {j.get("fileId"): j for j in JOBS.values() if j.get("fileId")}
    entries = []
    for path in OUTPUT.rglob("*"):
        if not path.is_file() or path.suffix.lstrip(".").lower() not in AUDIO_EXTS:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(OUTPUT.resolve()) or path.stat().st_size == 0:
            continue
        fid = file_id(path)
        job = known.get(fid)
        entries.append({**(job["song"] if job else {"title": path.stem, "artist": "本地音乐",
                         "album": "—", "source": path.relative_to(OUTPUT).parts[0], "duration": "—",
                         **lyrics.audio_metadata(path)}),
                        "id": fid, "fileId": fid, "format": path.suffix[1:].upper(),
                        "size": f"{path.stat().st_size / 1048576:.1f} MB", "sizeBytes": path.stat().st_size,
                        "modified": path.stat().st_mtime, "fileName": path.name})
    return sorted(entries, key=lambda s: s["modified"], reverse=True)


def find_file(fid):
    for path in OUTPUT.rglob("*"):
        if (path.is_file() and path.suffix[1:].lower() in AUDIO_EXTS
                and path.resolve().is_relative_to(OUTPUT.resolve()) and file_id(path) == fid):
            return path
    return None


def download_task(jid, song):
    with LOCK:
        JOBS[jid]["status"] = "downloading"
    persist_jobs()
    try:
        ext = clean(song.ext, "").lstrip(".").lower()
        if ext not in AUDIO_EXTS:
            raise ValueError("该音频格式暂不支持")
        directory = OUTPUT / song.source
        directory.mkdir(parents=True, exist_ok=True)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean(song.song_name, "音乐"))[:65].strip(" .") or "音乐"
        artist = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean(song.singers, "未知歌手"))[:35].strip(" .")
        target = directory / f"{name} - {artist} - {jid[:8]}.{ext}"
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
            with LOCK:
                songs = library()
            return self.json_response({"songs": songs})
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
        assets = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/memory.js": "memory.js", "/styles.css": "styles.css", "/lyrics.css": "lyrics.css", "/favicon.svg": "favicon.svg"}
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
            is_lyrics = path.startswith("/api/lyrics/")
            if not 0 < length <= (1572864 if is_lyrics else 16384):
                self.close_connection = True
                return self.json_response({"error": "请求长度无效"}, 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("无效请求")
            path = urlparse(self.path).path
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
                            exact = lyrics.exact_candidate(meta, choices) if not keyword else None
                            if not exact:
                                return self.json_response({"candidates": choices})
                            selected = exact["id"]
                    fetched = lyrics.netease_lyrics(selected)
                    if not fetched["original"].strip():
                        raise ValueError("该版本没有可用歌词，可尝试其他版本或导入 LRC")
                    # Explicit version selection replaces both tracks to keep their timing aligned.
                    raw = {**fetched, "source": "网易云歌词"}
                with LOCK:
                    if action == "lookup" and not body.get("songId"):
                        latest_raw = lyrics.load(file, fid, STATE)
                        if latest_raw != initial_raw:
                            return self.json_response(lyrics.align(**latest_raw))
                    lyrics.save(fid, STATE, **raw)
                return self.json_response(lyrics.align(**raw))
            if path == "/api/search":
                keyword = body.get("keyword", "")
                sources = body.get("sources", list(SOURCES)[:3])
                limit = body.get("limit", 12)
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
                                     "songs": [], "errors": [], "completed": 0, "total": len(sources), "created": now}
                SEARCH_POOL.submit(search_task, sid, keyword.strip(), sources, limit)
                return self.json_response({"id": sid}, 202)
            if path == "/api/downloads":
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids or len(ids) > 50 or any(not isinstance(s, str) for s in ids):
                    raise ValueError("请选择 1–50 首歌曲")
                queued, tasks = [], []
                with LOCK:
                    if any(sid not in CACHE for sid in ids):
                        raise ValueError("搜索结果已过期，请重新搜索；收藏中的歌曲也需要重新搜索")
                    if sum(j["status"] in {"queued", "downloading"} for j in JOBS.values()) + len(set(ids)) > 100:
                        return self.json_response({"error": "下载队列已满，请稍后重试"}, 429)
                    for sid in dict.fromkeys(ids):
                        existing = next((j for j in JOBS.values() if j.get("song", {}).get("id") == sid and j["status"] in {"queued", "downloading"}), None)
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


def main():
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
