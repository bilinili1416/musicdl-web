"""Read local LRC/embedded lyrics and align optional translations by timestamp."""
from __future__ import annotations

import bisect
import json
from pathlib import Path
import re
import unicodedata

MAX_TEXT = 262144
STAMP = re.compile(r"\[(\d{1,3}):([0-5]?\d)(?:[.:](\d{1,3}))?\]")
METADATA_CACHE = {}


def audio_metadata(path):
    """Recover names from existing downloads even when no job history is present."""
    from mutagen import File
    key = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
    if key in METADATA_CACHE:
        return dict(METADATA_CACHE[key])
    result = {}
    try:
        audio = File(path)
        tags = audio.tags if audio else None
        if tags:
            for field, keys in {"title": ("title", "TIT2", "©nam", "Title"),
                                "artist": ("artist", "TPE1", "©ART", "Author"),
                                "album": ("album", "TALB", "©alb", "WM/AlbumTitle")}.items():
                for tag in keys:
                    value = tags.get(tag)
                    if value:
                        result[field] = str(value[0] if isinstance(value, list) else value)
                        break
        if audio and getattr(audio, "info", None) and getattr(audio.info, "length", None):
            length = int(audio.info.length)
            result["duration"] = f"{length // 3600:02}:{length // 60 % 60:02}:{length % 60:02}"
    except Exception:
        pass
    if len(METADATA_CACHE) > 500:
        METADATA_CACHE.clear()
    METADATA_CACHE[key] = result
    return dict(result)


def parse_lrc(text):
    """Keep blank timed cues (instrumental breaks), repeated stamps and offsets."""
    text = str(text or "").replace("\r\n", "\n").lstrip("\ufeff")
    offset = re.search(r"\[offset:([+-]?\d+)\]", text, re.I)
    offset = int(offset[1]) / 1000 if offset else 0
    grouped, plain = {}, []
    for line in text.splitlines():
        stamps = STAMP.findall(line)
        content = STAMP.sub("", line).strip()
        # Enhanced LRC word timing is not displayed as text.
        content = re.sub(r"<\d{1,3}:[0-5]?\d(?:\.\d{1,3})?>", "", content)
        if stamps:
            for minute, second, fraction in stamps:
                time = round(max(0, int(minute) * 60 + int(second) + float("0." + (fraction or "0")) + offset), 3)
                values = grouped.setdefault(time, [])
                if content and content not in values:
                    values.append(content)
        elif content and not re.fullmatch(r"\[[a-z]+:.*\]", content, re.I):
            plain.append(content)
    return [{"time": time, "text": values[0] if values else "", "translation": "\n".join(values[1:])}
            for time, values in sorted(grouped.items())], "\n".join(plain)


def align(original, translation="", source=""):
    lines, plain = parse_lrc(original)
    translated, translated_plain = parse_lrc(translation)
    # A translator may round centiseconds; match the nearest original within 250 ms.
    times = [line["time"] for line in lines]
    unmatched = 0
    for cue in translated:
        if not cue["text"]:
            continue
        position = bisect.bisect_left(times, cue["time"])
        choices = [i for i in (position - 1, position) if 0 <= i < len(lines)]
        nearest = min(choices, key=lambda i: abs(times[i] - cue["time"]), default=None)
        if nearest is None or abs(times[nearest] - cue["time"]) > .251:
            unmatched += 1
            continue
        values = lines[nearest]["translation"].splitlines()
        if cue["text"] not in values:
            values.append(cue["text"])
        lines[nearest]["translation"] = "\n".join(values)
    return {"lines": lines, "plain": plain, "translationPlain": translated_plain,
            "synced": bool(lines), "hasTranslation": any(l["translation"] for l in lines) or bool(translated_plain),
            "unmatchedTranslations": unmatched, "source": source}


def read_text(path):
    if not path.is_file() or path.stat().st_size > MAX_TEXT:
        return ""
    raw = path.read_bytes()
    encodings = ("utf-16", "utf-8-sig", "gb18030") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def embedded_lyrics(path):
    from mutagen import File
    try:
        audio = File(path)
        tags = audio.tags if audio else None
        if not tags:
            return ""
        for key, value in tags.items():
            if str(key).upper().startswith("USLT"):
                return str(value.text)[:MAX_TEXT]
            if str(key).lower() in {"lyrics", "unsyncedlyrics", "©lyr", "wm/lyrics"}:
                return str(value[0] if isinstance(value, list) else value)[:MAX_TEXT]
    except Exception:
        pass
    return ""


def load(path, fid, state):
    original, translation, source = "", "", ""
    sidecar = path.with_suffix(".lrc")
    root = path.parent.resolve()
    if sidecar.resolve().is_relative_to(root):
        original = read_text(sidecar)
        if original:
            source = "本地 LRC"
    if not original:
        original = embedded_lyrics(path)
        if original:
            source = "音频内嵌歌词"
    for suffix in (".translation.lrc", ".zh.lrc", ".trans.lrc"):
        translated = path.with_suffix(suffix)
        if translated.resolve().is_relative_to(root):
            translation = read_text(translated)
        if translation:
            source += " · 本地译文"
            break
    try:
        saved = json.loads((state / "lyrics" / (fid + ".json")).read_text(encoding="utf-8"))
        original = saved.get("original") or original
        translation = saved.get("translation") or translation
        source = saved.get("source") or source
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {"original": original, "translation": translation, "source": source}


def save(fid, state, original, translation, source):
    directory = state / "lyrics"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (fid + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"original": original, "translation": translation, "source": source}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def raw_translation(song):
    raw = (getattr(song, "raw_data", None) or {}).get("lyric", {})
    if not isinstance(raw, dict):
        return ""
    for data in (raw, raw.get("data", {})):
        if not isinstance(data, dict):
            continue
        for key in ("tlyric", "translation", "trans", "translrc"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("lyric")
            if isinstance(value, str) and value.strip() and value not in {"NULL", "None"}:
                return value[:MAX_TEXT]
    return ""


def netease_api(endpoint, params):
    import requests
    try:
        response = requests.get("https://music.163.com/api/" + endpoint, params=params,
                                headers={"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"}, timeout=(4, 7))
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 200:
            raise ValueError("歌词平台暂不可用，请稍后重试或导入本地 LRC")
        return data
    except (requests.RequestException, ValueError) as error:
        raise ValueError("歌词平台暂不可用，请稍后重试或导入本地 LRC") from error


def candidates(meta, keyword=None):
    title = re.split(r"[（(]", meta.get("title", ""))[0].strip()
    artist = re.split(r"[,，/&]", meta.get("artist", ""))[0].strip()
    keyword = keyword or (title + " " + (artist if artist != "本地音乐" else ""))
    data = netease_api("search/get", {"s": keyword[:100], "type": 1, "limit": 8})
    return [{"id": str(song["id"]), "title": song.get("name", ""),
             "artist": " / ".join(a.get("name", "") for a in song.get("artists", [])),
             "album": song.get("album", {}).get("name", ""), "duration": song.get("duration", 0) / 1000}
            for song in data.get("result", {}).get("songs", []) if str(song.get("id", "")).isdigit()]


def exact_candidate(meta, choices):
    def normal(text):
        return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text).casefold())
    artists = {normal(v) for v in re.split(r"[,，/&]", meta.get("artist", "")) if v.strip()}
    for song in choices:
        match_artist = artists & {normal(v) for v in song["artist"].split(" / ")}
        if normal(meta.get("title", "")) == normal(song["title"]) and match_artist:
            return song
    return None


def netease_lyrics(song_id):
    if not re.fullmatch(r"\d{1,20}", str(song_id)):
        raise ValueError("无效的歌词歌曲 ID")
    data = netease_api("song/lyric", {"id": song_id, "lv": -1, "tv": -1, "rv": -1})
    return {"original": data.get("lrc", {}).get("lyric", "")[:MAX_TEXT],
            "translation": data.get("tlyric", {}).get("lyric", "")[:MAX_TEXT]}
