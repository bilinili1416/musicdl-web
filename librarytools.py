"""Persistent playlists, validated local imports, and conservative lyric matching."""
from __future__ import annotations

import json
import re
import unicodedata

MAX_IMPORT = 256 * 1024 * 1024


def read_metadata(state):
    try:
        data = json.loads((state / 'library-metadata.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError('音乐库信息无法读取，请检查 library-metadata.json') from exc
    if not isinstance(data, dict):
        raise ValueError('音乐库信息格式无效')
    return {fid: song for fid, song in data.items()
            if re.fullmatch(r'[a-f0-9]{32}', fid) and isinstance(song, dict)}


def read_playlists(state):
    try:
        data = json.loads((state / 'playlists.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ValueError('播放列表记录无法读取，请检查 playlists.json') from exc
    if not isinstance(data, list):
        raise ValueError('播放列表记录格式无效')
    return [item for item in data if isinstance(item, dict)
            and re.fullmatch(r'[a-f0-9]{32}', str(item.get('id', '')))
            and isinstance(item.get('name'), str) and isinstance(item.get('files'), list)]


def save_json(state, filename, data):
    state.mkdir(parents=True, exist_ok=True)
    target = state / filename
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)


def playlists(state, available):
    ids = list(dict.fromkeys(available))
    valid = set(ids)
    return [{'id': 'all', 'name': '全部本地音乐', 'files': ids}] + [
        {**item, 'files': list(dict.fromkeys(fid for fid in item['files'] if isinstance(fid, str) and fid in valid))}
        for item in read_playlists(state)]


def edit_playlist(state, body, available):
    import uuid
    items = read_playlists(state)
    action, pid = body.get('action'), body.get('id')
    if action in {'create', 'rename'}:
        name = body.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError('播放列表名称应为 1–60 字')
    if action == 'create':
        if len(items) >= 100:
            raise ValueError('最多创建 100 个播放列表')
        pid = uuid.uuid4().hex
        items.append({'id': pid, 'name': name.strip(), 'files': []})
    else:
        if pid == 'all':
            raise ValueError('默认列表自动包含全部本地音乐，不能修改或删除')
        item = next((item for item in items if item['id'] == pid), None)
        if not item:
            raise ValueError('播放列表不存在，请刷新后重试')
        if action == 'rename':
            item['name'] = name.strip()
        elif action == 'delete':
            items.remove(item)
        elif action in {'add', 'remove'}:
            files = body.get('files')
            if not isinstance(files, list) or not files or len(files) > 500 or any(not isinstance(fid, str) for fid in files):
                raise ValueError('请选择 1–500 首本地歌曲')
            if action == 'add':
                if any(fid not in available for fid in files):
                    raise ValueError('歌曲已移走或不存在，请刷新音乐库')
                item['files'] = list(dict.fromkeys(item['files'] + files))
                if len(item['files']) > 5000:
                    raise ValueError('每个列表最多包含 5000 首歌曲')
            else:
                removed = set(files)
                item['files'] = [fid for fid in item['files'] if fid not in removed]
        else:
            raise ValueError('无效的播放列表操作')
    save_json(state, 'playlists.json', items)
    return pid


def validate_audio(path):
    from mutagen import File
    try:
        audio = File(path)
        if audio is None or not getattr(audio, 'info', None):
            raise ValueError('无法识别有效音频，请检查文件格式')
        return max(0, float(getattr(audio.info, 'length', 0) or 0))
    except Exception as exc:
        raise ValueError('文件不是有效音频，或该音频格式无法读取') from exc


def exact_lyric_match(meta, choices):
    def normal(value):
        return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', value).casefold())
    artist = meta.get('artist', '')
    artists = {normal(s) for s in re.split(r'[,，/&]', artist) if s.strip()}
    known_artist = artist not in {'', '本地音乐', '未知歌手'}
    duration = 0
    try:
        for part in meta.get('duration', '').split(':'):
            duration = duration * 60 + int(part)
    except (ValueError, AttributeError):
        duration = 0
    matches = {}
    for choice in choices:
        if normal(meta.get('title', '')) != normal(choice.get('title', '')):
            continue
        if known_artist and not artists & {normal(s) for s in choice.get('artist', '').split(' / ')}:
            continue
        if duration and choice.get('duration') and abs(duration - choice['duration']) > 4:
            continue
        # Without tags, require duration evidence as well as a unique title.
        if not known_artist and (not duration or not choice.get('duration')):
            continue
        matches[choice['id']] = choice
    return next(iter(matches.values())) if len(matches) == 1 else None
