"""Offline tests for lyrics_sources (sample + mocked responses).  Run:  python -m unittest tests.test_sources"""
import base64, json, sys, unittest, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lyrics_sources as L


class Resp:
    def __init__(self, data=None, text=None, status=200):
        self._d, self.status_code, self.ok = data, status, status < 400
        self.text = text if text is not None else (json.dumps(data) if data is not None else "")
    def json(self):
        if self._d is None:
            raise ValueError("no json")
        return self._d


def router(routes):
    """routes: list of (url_substring, Resp | callable(params))"""
    def fake(url, params=None):
        for sub, r in routes:
            if sub in url:
                return r(params) if callable(r) else r
        return Resp(status=404)
    return fake


TTML = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyrics" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" itunes:timing="Word"><body><div>
<p begin="0:10.000" end="0:12.500"><span begin="10.000" end="10.500">Hel</span><span begin="10.500" end="11.000">lo</span> <span begin="11.000" end="12.000">world</span><span ttm:role="x-bg" begin="11.5" end="12.4"><span begin="11.5" end="12.4">(oh)</span></span></p>
<p begin="0:13.000" end="0:15.000">a plain line without spans</p>
</div></body></tt>"""


class Parsers(unittest.TestCase):
    def test_ttml_words_and_plain_line(self):
        lines, wordy = L.parse_ttml(TTML)
        self.assertTrue(wordy)
        self.assertEqual([w[0] for w in lines[0].words], ["Hello", "world"])      # syllables merged, bg skipped
        self.assertAlmostEqual(lines[0].words[0][1], 10.0); self.assertAlmostEqual(lines[0].words[0][2], 11.0)
        self.assertEqual(len(lines[1].words), 5)                                   # estimated words

    def test_krc_roundtrip(self):
        krc = "[id:$0]\n[0,3000]<0,500,0>Hel<500,500,0>lo <1000,800,0>world\n[3000,2000]<0,1000,0>你<1000,500,0>好<1500,500,0>吗\n"
        comp = zlib.compress(krc.encode())
        enc = b"krc1" + bytes(b ^ L._KRC_KEY[i % 16] for i, b in enumerate(comp))
        lines = L.parse_krc(L.decode_krc(base64.b64encode(enc)))
        self.assertEqual([w[0] for w in lines[0].words], ["Hello", "world"])
        self.assertAlmostEqual(lines[0].words[1][1], 1.0)
        self.assertEqual([w[0] for w in lines[1].words], ["你好", "吗"])              # CJK grouped in pairs

    def test_kpoe_word_and_line(self):
        word = {"type": "Word", "lyrics": [{"time": 1000, "duration": 2000, "text": "Hi there",
                "syllabus": [{"time": 1000, "duration": 500, "text": "Hi "}, {"time": 1500, "duration": 900, "text": "there"},
                             {"time": 1600, "duration": 300, "text": "(bg)", "isBackground": True}]}]}
        lines, wordy = L.parse_kpoe(word)
        self.assertTrue(wordy); self.assertEqual([w[0] for w in lines[0].words], ["Hi", "there"])
        lines, wordy = L.parse_kpoe({"type": "Line", "lyrics": [{"time": 0, "duration": 3000, "text": "one two three"}]})
        self.assertFalse(wordy); self.assertEqual(len(lines[0].words), 3)

    def test_richsync(self):
        body = json.dumps([{"ts": 5.0, "te": 8.0, "l": [{"c": "Hel", "o": 0}, {"c": "lo", "o": 0.4}, {"c": " ", "o": 0.8}, {"c": "you", "o": 1.0}]}])
        lines = L.parse_richsync(body)
        self.assertEqual([w[0] for w in lines[0].words], ["Hello", "you"])


class Sources(unittest.TestCase):
    def setUp(self):
        L._CACHE.clear(); L._FAILS.clear(); self._orig = L._get

    def tearDown(self):
        L._get = self._orig

    def test_lrcred(self):
        song = {"duration": 200, "lyrics": {"sync": "word", "lines": [{"text": "Hello world", "begin": 1.0, "end": 3.0,
                "words": [[{"text": "Hel", "begin": 1.0, "end": 1.4}, {"text": "lo", "begin": 1.4, "end": 2.0}], [{"text": "world", "begin": 2.0, "end": 3.0}]]}]}}
        L._get = router([("match.json", Resp({"hits": [{"isrc": "USX123"}]})), ("USX123.song.json", Resp(song))])
        r = L.src_lrcred("Artist", "Song", 200, {})
        self.assertEqual((r.source, r.word_synced, [w[0] for w in r.lines[0].words]), ("lrc.red", True, ["Hello", "world"]))
        self.assertIsNone(L.src_lrcred("Artist", "Song", 100, {}))                  # wrong duration is rejected

    def test_binilyrics_and_betterlyrics(self):
        L._get = router([("lyrics-api.binimum.org", Resp({"results": [{"lyricsUrl": "https://x.example/a.ttml"}]})),
                         ("x.example", Resp(text=TTML))])
        r = L.src_binilyrics("A", "B", 100, {}); self.assertTrue(r.word_synced)
        L._get = router([("boidu.dev", Resp({"ttml": TTML}))])
        r = L.src_betterlyrics("A", "B", 100, {}); self.assertEqual(r.source, "BetterLyrics")

    def test_lyricsplus_falls_over_to_next_server(self):
        import requests
        calls = []
        payload = {"type": "Word", "lyrics": [{"time": 0, "duration": 2000, "text": "a b", "syllabus": [{"time": 0, "duration": 900, "text": "a "}, {"time": 1000, "duration": 900, "text": "b"}]}]}
        def fake(url, params=None):
            calls.append(url)
            if len(calls) == 1:
                raise requests.ConnectionError("down")
            return Resp(payload)
        L._get = fake
        r = L.src_lyricsplus("A", "B", 100, {}); self.assertTrue(r.word_synced); self.assertEqual(len(calls), 2)

    def test_kugou_krc(self):
        krc = "[0,2000]<0,1000,0>Hello <1000,1000,0>there\n"
        enc = b"krc1" + bytes(b ^ L._KRC_KEY[i % 16] for i, b in enumerate(zlib.compress(krc.encode())))
        L._get = router([("search/song", Resp({"data": {"info": [{"hash": "h1", "duration": 200}]}})),
                         ("lyrics.kugou.com/search", Resp({"candidates": [{"id": "1", "accesskey": "k"}]})),
                         ("download", lambda p: Resp({"content": base64.b64encode(enc).decode()}) if p["fmt"] == "krc" else Resp(status=404))])
        r = L.src_kugou("A", "B (From X)", 200, {}); self.assertEqual((r.source, r.word_synced), ("KuGou", True))

    def test_musixmatch_needs_key_and_handles_restricted(self):
        self.assertIsNone(L.src_musixmatch("A", "B", 100, {}))
        L._get = router([("matcher.track.get", Resp({"message": {"header": {"status_code": 200}, "body": {"track": {"track_id": 1, "has_richsync": 1, "track_length": 100}}}})),
                         ("track.richsync.get", Resp({"message": {"header": {"status_code": 401}, "body": []}}))])
        self.assertIsNone(L.src_musixmatch("A", "B", 100, {"musixmatch_key": "k"}))

    def test_dispatcher_prefers_word_sync_respects_order_and_failures(self):
        line_res = L.Result([L.Line(0, 1, [("x", 0, 1)])], False, "LRCLIB")
        word_res = L.Result([L.Line(0, 1, [("y", 0, 1)])], True, "lrc.red")
        order = []
        def mk(name, res=None, boom=False):
            def f(*a):
                order.append(name)
                if boom: raise RuntimeError("down")
                return res
            return f
        saved = dict(L.REGISTRY)
        L.REGISTRY.update(lrcred=mk("lrcred", boom=True), binilyrics=mk("bini", line_res), lyricsplus=mk("plus", word_res),
                          kugou=mk("kugou"), betterlyrics=mk("better"), musixmatch=mk("mxm"), lrclib=mk("lrclib", line_res))
        try:
            r = L.find_lyrics("A", "T", 100, {})
            self.assertEqual(r.source, "lrc.red"); self.assertEqual(order, ["lrcred", "bini", "plus"])   # stops at first word-synced
            order.clear(); L._CACHE.clear()
            cfg = {"sources": [{"id": "lrclib", "on": True}, {"id": "lyricsplus", "on": False}]}
            r = L.find_lyrics("A", "T", 100, cfg)                                                       # custom order, one disabled
            self.assertEqual(order[0], "lrclib"); self.assertNotIn("plus", order)
            order.clear(); L._CACHE.clear()
            for _ in range(3): L.find_lyrics("A", "T2", 100, {})                                        # 3 failures -> source rests
            order.clear(); L._CACHE.clear(); L.find_lyrics("A", "T3", 100, {})
            self.assertNotIn("lrcred", order)
        finally:
            L.REGISTRY.update(saved)


if __name__ == "__main__":
    unittest.main()
