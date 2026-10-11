import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import lyrics
import songtools


class ReadingTests(unittest.TestCase):
    def test_readings_keep_spaces_markup_and_okurigana(self):
        text = "  青のすみか\n君の笑顔 思い出す <script>🙂"
        tokens = songtools.ruby_tokens(text)
        self.assertEqual("".join(t["text"] for t in tokens), text)
        self.assertIn({"text": "君", "reading": "きみ"}, tokens)
        self.assertIn({"text": "思", "reading": "おも"}, tokens)
        self.assertIn({"text": "出", "reading": "だ"}, tokens)
        self.assertTrue(all(songtools.KANJI.search(t["text"]) for t in tokens if t["reading"]))

    def test_translation_and_timing_are_not_annotated_or_changed(self):
        source = lyrics.align("[00:01.20]青い空\n[00:02.50]\n[00:04]君の笑顔", "[00:01.20]蓝色天空\n[00:04]你的笑容")
        annotated = songtools.annotate(source)
        self.assertTrue(annotated["japanese"])
        self.assertEqual([(l["time"], l["text"], l["translation"]) for l in source["lines"]],
                         [(l["time"], l["text"], l["translation"]) for l in annotated["lines"]])
        self.assertNotIn("ruby", source["lines"][0])
        self.assertFalse(any(t["reading"] for t in songtools.ruby_tokens("ひらがな カタカナ Jazz")))
        oversized = songtools.annotate(lyrics.align("青" * 4100 + "い"))
        self.assertEqual(oversized["plain"], "青" * 4100 + "い")
        self.assertIn("超长", oversized["readingNote"])


class WikiTests(unittest.TestCase):
    def setUp(self):
        songtools.WIKI_CACHE.clear()

    def test_japanese_alias_resolves_to_canonical_song_and_caches(self):
        response = {"query": {"redirects": [{"from": "青のすみか", "to": "苍蓝所栖"}],
                              "pages": [{"ns": 0, "pageid": 569140, "title": "苍蓝所栖", "templates": [{"title": "Template:Infobox Song"}]}]}}
        with patch.object(songtools, "wiki_api", return_value=response) as api:
            first = songtools.moegirl("青のすみか", "キタニタツヤ")
            self.assertEqual(first["status"], "matched")
            self.assertEqual(first["article"]["url"], "https://moegirl.icu/%E8%8B%8D%E8%93%9D%E6%89%80%E6%A0%96")
            first["article"]["title"] = "mutation"
            self.assertEqual(songtools.moegirl("青のすみか", "キタニタツヤ")["article"]["title"], "苍蓝所栖")
            self.assertEqual(api.call_count, 1)

    def test_disambiguation_and_non_song_pages_require_selection(self):
        for page in [{"ns": 0, "pageid": 1, "title": "Spiral", "pageprops": {"disambiguation": ""}, "templates": [{"title": "Template:Infobox Song"}]},
                     {"ns": 0, "pageid": 1, "title": "Spiral"}, {"ns": 0, "title": "Spiral", "missing": True}]:
            songtools.WIKI_CACHE.clear()
            with patch.object(songtools, "wiki_api", side_effect=[{"query": {"pages": [page]}}, {"query": {"search": [{"title": "Spiral(歌曲)"}, {"title": "Spiral(其他歌曲)"}]}}]):
                result = songtools.moegirl("Spiral")
                self.assertEqual(result["status"], "candidates")
                self.assertEqual(len(result["candidates"]), 2)
                self.assertNotIn("article", result)

    def test_network_failure_keeps_a_safe_search_link_and_is_not_cached(self):
        import requests
        with patch.object(songtools, "wiki_api", side_effect=requests.Timeout):
            result = songtools.moegirl("青のすみか & #<script>")
        self.assertEqual(result["status"], "unavailable")
        parsed = urlparse(result["searchUrl"])
        self.assertEqual(parsed.hostname, "moegirl.icu")
        self.assertEqual(parse_qs(parsed.query)["search"], ["青のすみか & #<script>"])
        self.assertFalse(songtools.WIKI_CACHE)


if __name__ == "__main__":
    unittest.main()
