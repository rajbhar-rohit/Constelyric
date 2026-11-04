"""
Lyrics sources for Constelyric.

Every source turns "artist + title + duration" into a Result(lines, word_synced, source).
Sources are tried in the user's order; the first *word-synced* result wins, otherwise the first
line-synced one is used.  Nothing is written to disk - results are only cached in memory.

Third-party services (all unofficial community services except Musixmatch and LRCLIB's open API):
  lrc.red, BiniLyrics, LyricsPlus, KuGou, BetterLyrics, LRCLIB, and Musixmatch (needs your own key).
Your track title and artist are sent to whichever services are enabled - see the README.
"""
import base64
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

APP_UA = {"User-Agent": "Constelyric/1.0 (open-source lyrics app)"}
TIMEOUT = 6            # seconds per request
DURATION_TOLERANCE = 8  # seconds


# --------------------------------------------------------------------------
# Data model + helpers
# --------------------------------------------------------------------------
@dataclass
class Line:
    start: float
    end: float
    words: list = field(default_factory=list)       # [(text, t0, t1), ...]


@dataclass
class Result:
    lines: list
    word_synced: bool
    source: str


def clean_title(t: str) -> str:
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t)
    t = re.split(r"\s+-\s+|\s+\|\s+|\s+feat\.?\s+|\s+ft\.?\s+", t, flags=re.I)[0]
    return t.strip()


def first_artist(a: str) -> str:
    return re.split(r"\s*(?:,|&|;|/|\bfeat\.?\b|\bft\.?\b|\band\b)\s*", a, flags=re.I)[0].strip()


def split_words(text, t, end):
    """Spread a line's time over its words by length (used when a source only has line timing)."""
    words = text.split()
    weights = [len(w) + 1 for w in words]
    total = sum(weights) or 1
    sung, acc, out = max(end - t, 0.5) * 0.92, t, []
    for w, wt in zip(words, weights):
        d = sung * wt / total
        out.append((w, acc, acc + d))
        acc += d
    return out


def build_lines(lrc_text: str) -> list:
    raw = []
    for row in lrc_text.splitlines():
        m = re.match(r"\s*\[(\d+):(\d+(?:\.\d+)?)\](.*)", row)
        if m:
            raw.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    raw.sort()
    lines = []
    for i, (t, text) in enumerate(raw):
        nxt = raw[i + 1][0] if i + 1 < len(raw) else t + 5
        end = min(nxt, t + 7)
        lines.append(Line(t, nxt, split_words(text, t, end)))
    return lines


def group_syllables(sylls):
    """[(text, t0, t1)] syllables -> words.  A trailing space ends a word; CJK lines with no spaces
    are grouped in pairs so they still read as 'words'."""
    words, cur, cs, ce = [], "", None, 0.0
    for i, (txt, t0, t1) in enumerate(sylls):
        if cs is None:
            cs = t0
        cur += txt
        ce = max(t1, t0 + 0.05)
        nxt = sylls[i + 1][0] if i + 1 < len(sylls) else None
        if txt[-1:].isspace() or nxt is None or nxt[:1].isspace():
            w = cur.strip()
            if w:
                words.append((w, cs, max(ce, cs + 0.05)))
            cur, cs = "", None
    if len(words) == 1 and len(sylls) > 2 and not any(re.search(r"\s", s[0]) for s in sylls):
        words = []
        for i in range(0, len(sylls), 2):
            chunk = sylls[i:i + 2]
            words.append(("".join(c[0] for c in chunk), chunk[0][1], max(chunk[-1][2], chunk[0][1] + 0.05)))
    return words


def _make_line(words, start=None, end=None):
    words = [w for w in words if w[0]]
    if not words:
        return None
    s = words[0][1] if start is None else start
    e = words[-1][2] if end is None else end
    return Line(s, max(e, words[-1][2]), words)


def _dur_ok(ours, theirs, tol=DURATION_TOLERANCE):
    return not ours or not theirs or abs(float(ours) - float(theirs)) <= tol


def _get(url, params=None):
    return requests.get(url, params=params, headers=APP_UA, timeout=TIMEOUT)


def _json(r):
    return r.json() if r is not None and r.ok else None


def _tries(artist, title):
    """The track as given, then a cleaned-up version (no '(From ...)', first artist only)."""
    first = (artist, title)
    cleaned = (first_artist(artist), clean_title(title))
    return [first] if cleaned == first else [first, cleaned]


# --------------------------------------------------------------------------
# TTML (Apple Music style) parser - used by BiniLyrics and BetterLyrics
# --------------------------------------------------------------------------
def _clock(s):
    s = s.strip()
    if s.endswith("ms"):
        return float(s[:-2]) / 1000
    if s.endswith("s"):
        return float(s[:-1])
    sec = 0.0
    for part in s.split(":"):
        sec = sec * 60 + float(part)
    return sec


def parse_ttml(xml_text):
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    local = lambda el: el.tag.rsplit("}", 1)[-1]
    lines, word_synced = [], False
    for p in root.iter():
        if local(p) != "p":
            continue
        pb, pe = p.get("begin"), p.get("end")
        sylls = []
        for child in p:
            if local(child) != "span" or len(child):          # skip nested containers (background vocals)
                continue
            if any(k.endswith("role") and v == "x-bg" for k, v in child.attrib.items()):
                continue
            if child.get("begin") is None:
                continue
            txt = child.text or ""
            t0 = _clock(child.get("begin"))
            t1 = _clock(child.get("end")) if child.get("end") else t0
            if (child.tail or "").strip() == "" and (child.tail or "") != "" and not txt[-1:].isspace():
                txt += " "                                    # whitespace between spans = word break
            sylls.append((txt, t0, t1))
        if sylls:
            line = _make_line(group_syllables(sylls), _clock(pb) if pb else None, _clock(pe) if pe else None)
            word_synced = word_synced or len(line.words) > 0
        else:
            text = " ".join("".join(p.itertext()).split())
            if not text or pb is None:
                continue
            b = _clock(pb)
            line = _make_line(split_words(text, b, _clock(pe) if pe else b + 4), b, _clock(pe) if pe else None)
        if line:
            lines.append(line)
    lines.sort(key=lambda l: l.start)
    return lines, word_synced


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
def src_lrclib(artist, title, duration, cfg):
    ct, fa = clean_title(title), first_artist(artist)
    for a, t in ((artist, title), (fa, ct), (fa, title)):
        params = {"artist_name": a, "track_name": t}
        if duration:
            params["duration"] = int(duration)
        d = _json(_get("https://lrclib.net/api/get", params))
        if d and d.get("syncedLyrics"):
            return Result(build_lines(d["syncedLyrics"]), False, "LRCLIB")
    for q in (f"{fa} {ct}", f"{ct} {fa}", ct, title):
        hits = [h for h in (_json(_get("https://lrclib.net/api/search", {"q": q})) or []) if h.get("syncedLyrics")]
        if hits:
            if duration:
                hits.sort(key=lambda h: abs((h.get("duration") or 0) - duration))
                if not _dur_ok(duration, hits[0].get("duration")):
                    continue
            return Result(build_lines(hits[0]["syncedLyrics"]), False, "LRCLIB")
    return None


def _lrcred_lines(song):
    lyr = song.get("lyrics") or {}
    word_level = lyr.get("sync") == "word"
    lines = []
    for ln in lyr.get("lines") or []:
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        b = float(ln.get("begin") or 0)
        e = float(ln.get("end") or b + 4)
        words = []
        if word_level and ln.get("words"):
            for word in ln["words"]:                           # each word = list of syllables
                if isinstance(word, list) and word:
                    txt = "".join(s.get("text", "") for s in word).strip()
                    t0 = float(word[0].get("begin") or b)
                    t1 = float(word[-1].get("end") or t0 + 0.2)
                    if txt:
                        words.append((txt, t0, max(t1, t0 + 0.05)))
        if not words:
            words = split_words(text, b, e)
        line = _make_line(words, b, e)
        if line:
            lines.append(line)
    return lines, word_level


def src_lrcred(artist, title, duration, cfg):
    for a, t in _tries(artist, title):
        params = {"track": t, "artist": a}
        if duration:
            params["duration"] = int(duration)
        hits = (_json(_get("https://lrc.red/match.json", params)) or {}).get("hits") or []
        isrc = next((h.get("isrc") for h in hits if h.get("isrc")), None)
        if not isrc:
            continue
        song = _json(_get(f"https://lrc.red/s/{quote(str(isrc).strip())}.song.json"))
        if not song or not _dur_ok(duration, song.get("duration")):
            continue
        lines, wordy = _lrcred_lines(song)
        if not lines and isinstance(song.get("source"), str):
            lines, wordy = parse_ttml(song["source"])
        if lines:
            return Result(lines, wordy, "lrc.red")
    return None


def src_binilyrics(artist, title, duration, cfg):
    for a, t in _tries(artist, title):
        params = {"track": t, "artist": a}
        if duration:
            params["duration"] = int(duration)
        results = (_json(_get("https://lyrics-api.binimum.org/", params)) or {}).get("results") or []
        url = results[0].get("lyricsUrl") if results else None
        if not url or not str(url).startswith("https://"):
            continue
        r = _get(url)
        if r.ok:
            lines, wordy = parse_ttml(r.text)
            if lines:
                return Result(lines, wordy, "BiniLyrics")
    return None


KPOE_SERVERS = ["https://lyricsplus.binimum.org", "https://lyricsplus.prjktla.workers.dev",
                "https://lyricsplus-seven.vercel.app", "https://lyrics-plus-backend.vercel.app"]


def parse_kpoe(payload):
    raw = payload.get("lyrics") if isinstance(payload.get("lyrics"), list) else None
    if raw is None and isinstance(payload.get("data"), dict):
        raw = payload["data"].get("lyrics")
    if raw is None and isinstance(payload.get("data"), list):
        raw = payload["data"]
    if not raw:
        return [], False
    is_line = str(payload.get("type", "")).lower() == "line"
    lines, word_synced = [], False
    for e in raw:
        if not isinstance(e, dict):
            continue
        start = float(e.get("time") or 0) / 1000
        end = (float(e["endTime"]) / 1000) if e.get("endTime") else start + float(e.get("duration") or 0) / 1000
        sylls = [s for s in (e.get("syllabus") or e.get("words") or []) if isinstance(s, dict) and not s.get("isBackground")]
        words = []
        if sylls and not is_line:
            words = group_syllables([(s.get("text", ""), float(s.get("time") or 0) / 1000,
                                      (float(s.get("time") or 0) + float(s.get("duration") or 0)) / 1000) for s in sylls])
            word_synced = word_synced or bool(words)
        if not words and (e.get("text") or "").strip():
            words = split_words(e["text"], start, end if end > start else start + 4)
        line = _make_line(words, start, end if end > start else None)
        if line:
            lines.append(line)
    lines.sort(key=lambda l: l.start)
    return lines, word_synced and not is_line


def src_lyricsplus(artist, title, duration, cfg):
    servers, err = random.sample(KPOE_SERVERS, 2), None
    for a, t in _tries(artist, title):
        params = {"title": t, "artist": a}
        if duration:
            params["duration"] = int(duration)
        for base in servers:
            try:
                r = _get(base + "/v2/lyrics/get", params)
            except requests.RequestException as e:           # that server is down: try the next
                err = e
                continue
            if r.ok:
                lines, wordy = parse_kpoe(r.json())
                if lines:
                    return Result(lines, wordy, "LyricsPlus")
            break                                              # server answered: no need to ask another
        else:
            continue
    if err and not isinstance(err, type(None)):
        raise err
    return None


# KuGou: search -> lyric candidates -> download.  'krc' carries word timing but is XOR+zlib encoded.
_KRC_KEY = bytes([0x40, 0x47, 0x61, 0x77, 0x5E, 0x32, 0x74, 0x47, 0x51, 0x36, 0x31, 0x2D, 0xCE, 0xD2, 0x6E, 0x69])


def decode_krc(b64):
    raw = base64.b64decode(b64)
    data = bytes(b ^ _KRC_KEY[i % 16] for i, b in enumerate(raw[4:]))
    return zlib.decompress(data).decode("utf-8", "replace")


def parse_krc(text):
    lines = []
    for row in text.splitlines():
        m = re.match(r"\[(\d+),(\d+)\](.*)", row)
        if not m:
            continue
        ls, ld, rest = int(m.group(1)), int(m.group(2)), m.group(3)
        sylls = [(txt, (ls + int(o)) / 1000, (ls + int(o) + int(d)) / 1000)
                 for o, d, txt in re.findall(r"<(\d+),(\d+),\d+>([^<]*)", rest) if txt != ""]
        words = group_syllables(sylls) if sylls else split_words(re.sub(r"<[^>]*>", "", rest), ls / 1000, (ls + ld) / 1000)
        line = _make_line(words, ls / 1000, (ls + ld) / 1000)
        if line:
            lines.append(line)
    lines.sort(key=lambda l: l.start)
    return lines


def src_kugou(artist, title, duration, cfg):
    ct, fa = clean_title(title), first_artist(artist)
    r = _get("https://mobileservice.kugou.com/api/v3/search/song",
             {"version": 9108, "plat": 0, "pagesize": 8, "showtype": 0, "keyword": f"{ct} - {fa}"})
    infos = ((_json(r) or {}).get("data") or {}).get("info") or []
    songs = [s for s in infos if s.get("hash") and _dur_ok(duration, s.get("duration"))]
    for song in songs[:3]:
        cands = (_json(_get("https://lyrics.kugou.com/search",
                            {"ver": 1, "man": "yes", "client": "pc", "hash": song["hash"]})) or {}).get("candidates") or []
        if not cands:
            continue
        c = cands[0]
        for fmt in ("krc", "lrc"):
            d = _json(_get("https://lyrics.kugou.com/download", {"fmt": fmt, "charset": "utf8", "client": "pc",
                                                                  "ver": 1, "id": c["id"], "accesskey": c["accesskey"]}))
            content = (d or {}).get("content")
            if not content:
                continue
            try:
                if fmt == "krc":
                    lines = parse_krc(decode_krc(content))
                    if lines:
                        return Result(lines, True, "KuGou")
                else:
                    lines = build_lines(base64.b64decode(content).decode("utf-8-sig", "replace"))
                    if lines:
                        return Result(lines, False, "KuGou")
            except Exception:
                continue                                       # bad KRC: fall back to plain LRC
    return None


def src_betterlyrics(artist, title, duration, cfg):
    for a, t in _tries(artist, title):
        params = {"s": t, "a": a}
        if duration:
            params["d"] = int(duration)
        r = _get("https://lyrics-api.boidu.dev/getLyrics", params)
        if not r.ok:
            continue
        body = r.text.strip()
        ttml = body
        if not body.startswith("<"):
            try:
                data = r.json()
                ttml = data.get("ttml") or data.get("lyrics") or "" if isinstance(data, dict) else ""
            except ValueError:
                ttml = ""
        if ttml.lstrip().startswith("<"):
            lines, wordy = parse_ttml(ttml)
            if lines:
                return Result(lines, wordy, "BetterLyrics")
    return None


def parse_richsync(body):
    lines = []
    for e in json.loads(body):
        ts, te, toks = float(e["ts"]), float(e["te"]), e.get("l") or []
        sylls = []
        for j, tok in enumerate(toks):
            t0 = ts + float(tok.get("o", 0))
            t1 = ts + float(toks[j + 1]["o"]) if j + 1 < len(toks) else te
            sylls.append((tok.get("c", ""), t0, t1))
        line = _make_line(group_syllables(sylls), ts, te) if sylls else None
        if line:
            lines.append(line)
    return lines


def src_musixmatch(artist, title, duration, cfg):
    """Official Musixmatch API with YOUR key (musixmatch.com/developer). Synced lyrics usually need
    a paid plan; if the key can't access them this quietly returns nothing."""
    key = (cfg.get("musixmatch_key") or os.environ.get("MUSIXMATCH_API_KEY", "")).strip()
    if not key:
        return None
    base = "https://api.musixmatch.com/ws/1.1/"

    def api(method, **p):
        msg = _get(base + method, dict(p, apikey=key, format="json")).json().get("message", {})
        return msg.get("body") if (msg.get("header") or {}).get("status_code") == 200 else None

    ct, fa = clean_title(title), first_artist(artist)
    body = api("matcher.track.get", q_track=ct, q_artist=fa)
    track = (body or {}).get("track") if isinstance(body, dict) else None
    if not track or not _dur_ok(duration, track.get("track_length")):
        return None
    tid = track["track_id"]
    if track.get("has_richsync"):
        rs = api("track.richsync.get", track_id=tid)
        if isinstance(rs, dict) and rs.get("richsync"):
            lines = parse_richsync(rs["richsync"]["richsync_body"])
            if lines:
                return Result(lines, True, "Musixmatch")
    if track.get("has_subtitles"):
        sub = api("track.subtitle.get", track_id=tid, subtitle_format="lrc")
        if isinstance(sub, dict) and sub.get("subtitle"):
            lines = build_lines(sub["subtitle"]["subtitle_body"])
            if lines:
                return Result(lines, False, "Musixmatch")
    return None


# --------------------------------------------------------------------------
# Registry, order, dispatcher
# --------------------------------------------------------------------------
REGISTRY = {"lrcred": src_lrcred, "binilyrics": src_binilyrics, "lyricsplus": src_lyricsplus,
            "kugou": src_kugou, "betterlyrics": src_betterlyrics, "musixmatch": src_musixmatch,
            "lrclib": src_lrclib}

INFO = {  # id -> (name, short description)
    "lrcred": ("lrc.red", "word-by-word · community"),
    "binilyrics": ("BiniLyrics", "word-by-word · Apple-style"),
    "lyricsplus": ("LyricsPlus", "word-by-word · multi-server"),
    "kugou": ("KuGou", "word-by-word · good for Asian songs"),
    "betterlyrics": ("BetterLyrics", "word-by-word · small server"),
    "musixmatch": ("Musixmatch", "needs your own API key"),
    "lrclib": ("LRCLIB", "line-by-line · open database"),
}
DEFAULT_ORDER = ["lrcred", "binilyrics", "lyricsplus", "kugou", "betterlyrics", "musixmatch", "lrclib"]


def default_sources():
    return [{"id": i, "on": i != "musixmatch"} for i in DEFAULT_ORDER]


def ordered_sources(cfg):
    out, seen = [], set()
    for s in (cfg.get("sources") or []):
        if isinstance(s, dict) and s.get("id") in REGISTRY and s["id"] not in seen:
            out.append({"id": s["id"], "on": bool(s.get("on", True))})
            seen.add(s["id"])
    out += [s for s in default_sources() if s["id"] not in seen]
    return out


_CACHE, _FAILS = {}, {}      # in-memory only


def _norm(s):
    return re.sub(r"\W+", "", s.lower())


def _tripped(sid):
    n, until = _FAILS.get(sid, (0, 0.0))
    return n >= 3 and time.time() < until


def _record(sid, ok):
    n, _ = _FAILS.get(sid, (0, 0.0))
    _FAILS[sid] = (0, 0.0) if ok else (n + 1, time.time() + 600)    # 3 failures in a row: rest 10 min


def find_lyrics(artist, title, duration, cfg=None):
    """Try the enabled sources in order. Returns a Result or None."""
    cfg = cfg or {}
    key = (_norm(artist), _norm(title))
    if key in _CACHE:
        return _CACHE[key]
    best = None
    for src in ordered_sources(cfg):
        if not src["on"] or _tripped(src["id"]):
            continue
        try:
            res = REGISTRY[src["id"]](artist, title, duration, cfg)
            _record(src["id"], True)
        except Exception as e:
            print(f"[lyrics] {src['id']} failed: {e}")
            _record(src["id"], False)
            continue
        if not res or not res.lines:
            continue
        print(f"[lyrics] {INFO[src['id']][0]}: {'word' if res.word_synced else 'line'}-synced, {len(res.lines)} lines")
        if res.word_synced:
            best = res
            break
        best = best or res                  # keep the first line-synced one as a fallback
    if best:
        if len(_CACHE) > 40:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = best
    else:
        print(f"[lyrics] nothing found for {artist!r} / {title!r}")
    return best
