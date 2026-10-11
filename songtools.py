"""Offline Japanese readings and explicit Moegirl article lookup."""
from __future__ import annotations

import copy
from functools import lru_cache
import re
import threading
import time
import unicodedata
from urllib.parse import quote, urlencode

KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d]")
KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f々]")
READING_LOCK = threading.RLock()
CONVERTER = None
WIKI_ROOT = "https://moegirl.icu/"
WIKI_CACHE = {}
WIKI_LOCK = threading.Lock()


@lru_cache(maxsize=256)
def ruby_tokens(text):
    """Preserve every source character; only place readings above kanji."""
    global CONVERTER
    if not KANJI.search(text) or len(text) > 4096:
        return [{"text": text, "reading": ""}]
    if "\n" in text or "\r" in text:
        # A line break is a sentence boundary, not part of the previous proper name.
        return [token for part in re.split(r"(\r\n|\r|\n)", text) if part
                for token in ([{"text": part, "reading": ""}] if part in {"\r\n", "\r", "\n"} else ruby_tokens(part))]
    with READING_LOCK:
        if CONVERTER is None:
            import unidic_lite
            from fugashi import Tagger
            CONVERTER = Tagger('-d "' + unidic_lite.DICDIR + '"')
        words = [(word.surface, word.feature.kana or "") for word in CONVERTER(text)]
    parts, cursor = [], 0
    for surface, kana in words:
        start = text.find(surface, cursor)
        if start < 0:
            return [{"text": text, "reading": ""}]
        if start > cursor:
            parts.append((text[cursor:start], ""))
        kana = unicodedata.normalize("NFKC", kana)
        reading = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in kana)
        parts.append((surface, reading))
        cursor = start + len(surface)
    if cursor < len(text):
        parts.append((text[cursor:], ""))
    result = []
    for original, reading in parts:
        if not KANJI.search(original) or not reading:
            result.append({"text": original, "reading": ""})
            continue
        segments = [s for s in re.split("(" + KANJI.pattern + "+)", original) if s]
        pattern = "".join("(.+?)" if KANJI.search(s) else re.escape(s) for s in segments)
        aligned = re.fullmatch(pattern, reading)
        if aligned:
            readings = iter(aligned.groups())
            result.extend({"text": s, "reading": next(readings) if KANJI.search(s) else ""} for s in segments)
            continue
        # Remove unchanged hiragana from the edges, keeping okurigana on the baseline.
        start = 0
        while start < min(len(original), len(reading)) and original[start] == reading[start] and not KANJI.search(original[start]):
            start += 1
        end = 0
        while end < min(len(original) - start, len(reading) - start) and original[-end - 1] == reading[-end - 1] and not KANJI.search(original[-end - 1]):
            end += 1
        if start:
            result.append({"text": original[:start], "reading": ""})
        result.append({"text": original[start:len(original) - end if end else None],
                       "reading": reading[start:len(reading) - end if end else None]})
        if end:
            result.append({"text": original[-end:], "reading": ""})
    return result


def annotate(data):
    result = copy.deepcopy(data)
    texts = [line["text"] for line in result["lines"]] + [result["plain"]]
    result["japanese"] = bool(KANA.search("\n".join(texts)))
    remaining, skipped = 20000, False
    for line in result["lines"]:
        text = line["text"]
        if len(text) > min(remaining, 4096):
            skipped = True
            continue
        line["ruby"] = ruby_tokens(text)
        remaining -= len(text)
    plain = result["plain"]
    if len(plain) <= min(remaining, 4096):
        result["plainRuby"] = ruby_tokens(plain)
    elif plain:
        skipped = True
    result["readingNote"] = "本地词典自动注音，人名和特殊读法可能有误。" + ("超长部分保留原文，未注音。" if skipped else "")
    return result


def article(title):
    return {"title": title, "url": WIKI_ROOT + quote(title.replace(" ", "_"), safe="")}


def wiki_api(params):
    import requests
    response = requests.get(WIKI_ROOT + "api.php", params={**params, "format": "json", "formatversion": 2},
                            headers={"User-Agent": "MusicdlWeb/1.0 (personal music article lookup)"},
                            timeout=(3, 4), allow_redirects=False)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or "error" in data:
        raise ValueError("萌娘百科查询暂不可用")
    return data


def moegirl(title, artist=""):
    import requests
    key = (title, artist)
    with WIKI_LOCK:
        cached = WIKI_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 600:
            return copy.deepcopy(cached[1])
    result = {"status": "candidates", "candidates": [],
              "searchUrl": WIKI_ROOT + "index.php?" + urlencode({"title": "Special:搜索", "search": title, "fulltext": 1})}
    try:
        resolved = wiki_api({"action": "query", "titles": title, "redirects": 1,
                             "prop": "pageprops|templates", "ppprop": "disambiguation", "tlnamespace": 10, "tllimit": 100})
        pages = resolved.get("query", {}).get("pages", [])
        pages = list(pages.values()) if isinstance(pages, dict) else pages
        exact = [p for p in pages if "missing" not in p and "invalid" not in p
                 and p.get("ns") == 0 and p.get("pageid", -1) > 0
                 and "disambiguation" not in p.get("pageprops", {})
                 and any(t.get("title", "").casefold() in {"template:infobox song", "template:歌曲信息"} for t in p.get("templates", []))]
        if len(exact) == 1:
            result.update(status="matched", article=article(exact[0]["title"]))
        else:
            # Search matches are suggestions, never silently treated as the right song.
            data = wiki_api({"action": "query", "list": "search", "srsearch": '"' + title.replace('"', ' ') + '"',
                             "srlimit": 8, "srnamespace": 0, "srprop": ""})
            result["candidates"] = [article(p["title"]) for p in data.get("query", {}).get("search", [])
                                    if isinstance(p.get("title"), str)]
    except (requests.RequestException, ValueError, TypeError, KeyError):
        result.update(status="unavailable", message="萌娘百科暂时无法查询，可打开站内搜索，或稍后重试。")
        return result
    with WIKI_LOCK:
        if len(WIKI_CACHE) >= 256:
            WIKI_CACHE.pop(next(iter(WIKI_CACHE)))
        WIKI_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result
