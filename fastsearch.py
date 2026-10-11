"""Catalog-only search and deferred resolution for the pinned musicdl version."""
from __future__ import annotations

import copy
import html
import logging
import re


CATALOG_PATHS = {
    'NeteaseMusicClient': ('result', 'songs'),
    'QQMusicClient': ('music.search.SearchCgiService.DoSearchForQQMusicMobile', 'data', 'body', 'item_song'),
    'KuwoMusicClient': ('abslist',),
    'KugouMusicClient': ('data', 'lists'),
    'MiguMusicClient': ('songResultData', 'result'),
    'QianqianMusicClient': ('data', 'typeTrack'),
}


def text(value):
    if value is None:
        return ''
    return html.unescape(re.sub(r'</?(?:em|b|strong|font)(?:\s[^>]*)?>', '', str(value), flags=re.I)).strip()


def names(items):
    if not isinstance(items, list):
        return text(items)
    return ', '.join(text(item.get('name')) for item in items if isinstance(item, dict) and item.get('name'))


def number(value):
    try:
        return max(0, float(value or 0))
    except (TypeError, ValueError):
        return 0


def metadata(source, raw):
    """Keep the exact catalog record private so downloads resolve the selected ID."""
    if source == 'NeteaseMusicClient':
        title, artist, album = raw.get('name'), names(raw.get('ar') or raw.get('artists')), (raw.get('al') or raw.get('album') or {}).get('name')
        identifier, seconds = raw.get('id'), number(raw.get('dt') or raw.get('duration')) / 1000
    elif source == 'QQMusicClient':
        title, artist = raw.get('title') or raw.get('songname'), names(raw.get('singer'))
        album = (raw.get('album') or {}).get('title') or raw.get('albumname')
        identifier, seconds = raw.get('mid') or raw.get('songmid'), number(raw.get('interval'))
    elif source == 'KuwoMusicClient':
        title, artist, album = raw.get('SONGNAME') or raw.get('name'), raw.get('ARTIST') or raw.get('artist'), raw.get('ALBUM') or raw.get('album')
        identifier = str(raw.get('MUSICRID') or raw.get('musicrid') or '').removeprefix('MUSIC_')
        seconds = number(raw.get('DURATION') or raw.get('duration'))
    elif source == 'KugouMusicClient':
        title = raw.get('SongName') or raw.get('songname') or raw.get('OriSongName') or raw.get('FileName')
        artist = raw.get('SingerName') or raw.get('singername') or names(raw.get('Singers') or raw.get('singerinfo'))
        album = raw.get('AlbumName') or raw.get('album_name') or (raw.get('albuminfo') or {}).get('name')
        identifier = raw.get('FileHash') or raw.get('hash')
        seconds = number(raw.get('Duration') or raw.get('duration')) or number(raw.get('timelen')) / 1000
    elif source == 'MiguMusicClient':
        title, artist = raw.get('name') or raw.get('songName'), names(raw.get('singers') or raw.get('singerList'))
        album = raw.get('album') or names(raw.get('albums'))
        identifier, seconds = raw.get('contentId'), number(raw.get('duration'))
    elif source == 'QianqianMusicClient':
        title, artist, album = raw.get('title'), names(raw.get('artist')), raw.get('albumTitle')
        identifier, seconds = raw.get('TSID'), number(raw.get('duration'))
    else:
        raise ValueError('该平台暂不支持快速搜索')
    if identifier is None or not str(identifier).strip() or not text(title):
        return None
    return {'fast': True, 'source': source, 'raw': copy.deepcopy(raw),
            'title': text(title), 'artist': text(artist) or '未知歌手', 'album': text(album) or '—',
            'sourceId': str(identifier), 'duration': f'{int(seconds)//60}:{int(seconds)%60:02d}' if seconds else '—',
            'format': '待解析', 'size': '—', 'sizeBytes': 0, 'bitrate': 0, 'needsResolve': True}


def search(client, keyword, limit):
    # One catalog page, with no link tests, quality requests or lyric requests.
    from musicdl.modules.utils import resp2json
    client.search_size_per_page = limit
    specs = client._constructsearchurls(keyword=keyword, request_overrides={'timeout': (6, 12)})
    if not specs:
        return []
    spec = specs[0]
    if isinstance(spec, dict):
        args = copy.deepcopy(spec)
        url = args.pop('url')
        args.pop('page', None)
        args.pop('page_no', None)
        response = client.post(url, **args, timeout=(6, 12))
    else:
        response = client.get(spec, timeout=(6, 12))
    if response is None:
        raise ValueError('搜索接口未响应，请稍后重试')
    response.raise_for_status()
    records = resp2json(response)
    for key in CATALOG_PATHS[client.source]:
        if not isinstance(records, dict) or key not in records:
            raise ValueError('平台搜索接口返回异常，可切换完整搜索或其他平台')
        records = records[key]
    if not isinstance(records, list):
        raise ValueError('平台没有返回有效的歌曲列表')
    songs, seen = [], set()
    for raw in records:
        if not isinstance(raw, dict):
            continue
        song = metadata(client.source, raw)
        if song and song['sourceId'] not in seen:
            seen.add(song['sourceId'])
            songs.append(song)
        if len(songs) >= limit:
            break
    return songs


def resolve(client, descriptor):
    """Use musicdl's original resolvers on this record, never search by title again."""
    from musicdl.modules import SongInfo
    raw = copy.deepcopy(descriptor['raw'])
    fallback = SongInfo(source=client.source)
    third_party = getattr(client, '_parsewiththirdpartapis', None)
    if third_party:
        try:
            fallback = third_party(search_result=raw, request_overrides={'timeout': (6, 12)}) or fallback
        except Exception as exc:
            logging.getLogger('musicdl-web').warning('%s third-party resolution failed (%s)',
                                                     client.source, type(exc).__name__)
    song = fallback
    try:
        song = client._parsewithofficialapiv1(search_result=raw, song_info_flac=fallback,
                                             lossless_quality_is_sufficient=not bool(client.default_cookies),
                                             request_overrides={'timeout': (6, 12)}) or fallback
    except Exception as exc:
        logging.getLogger('musicdl-web').warning('%s official resolution failed (%s)',
                                                 client.source, type(exc).__name__)
    if not song.with_valid_download_url:
        song = fallback
    if not song.with_valid_download_url or song.episodes:
        raise ValueError('该歌曲暂时没有可用下载链接，可尝试其他平台或完整搜索')
    if str(song.identifier).casefold() != descriptor['sourceId'].casefold():
        raise ValueError('平台返回的歌曲标识不匹配，已停止下载，请重新搜索')
    return song
